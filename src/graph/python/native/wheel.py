"""Wheel artifact reader + wheel-vs-sdist oracle + pre-install soname preflight.

Folds the three wheel-facing native leaves (3c-3):
  * ``wheel_inspect`` — pure host-side wheel download + ``DT_NEEDED`` soname read;
  * ``wheel_oracle`` — the wheel-vs-sdist build-from-source tag-matching decision
    (``risk_from_packages``), extracted from ``resolve_lock``;
  * ``wheel_preflight`` — seeds RESOLVER/UNKNOWN ``SystemLib`` priors from each
    package's target wheel before install (``wheel_preflight_probe``).
"""

from __future__ import annotations

import io
import logging
import os
import re
import shlex
import sys
import tempfile
import zipfile

from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile

from graph.contracts.executor import Executor
from graph.model import syslib_id
from graph.model import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    NodeType,
    State,
)
from graph.python.native.apt import ObservedNeed, resolve
from graph.python.native.system_libs import make_syslib_node
from graph.python.read.target_env import TargetEnv, pip_wheel_platform_tag

logger = logging.getLogger(__name__)


# === wheel_inspect: pure host-side wheel DT_NEEDED soname read ===
# Extension-module basenames: ``cpython-NN[N]*.so`` (NN = 2-digit for 3.0-3.9,
# NNN = 3-digit for 3.10+) or ``abi3.so``. Mirrors ldd_probe.EXT_SO_MAP_CMD.
_EXT_SO_RE = re.compile(r"\.cpython-\d{2,3}.*\.so\Z|\.abi3\.so\Z")
# Auditwheel vendors external libs into a top-level ``<distname>.libs/`` directory and
# relinks them to hash-suffixed names. We WALK those bundled libs (for their transitive
# DT_NEEDED) but subtract their own basenames from the result — they are satisfied
# inside the wheel, so they are not external system requirements.
_BUNDLED_DIR_RE = re.compile(r"(^|/)[A-Za-z0-9][A-Za-z0-9._+-]*\.libs/")
# Base-image / toolchain sonames every glibc target already provides. Reading
# ALL DT_NEEDED (unlike ldd's ``=> not found``) would otherwise seed a dozen
# trivially-satisfied nodes per package; filtering keeps the interesting
# external libs (libGL, libglib, libpq).
_BASE_IMAGE_SONAMES = frozenset(
    {
        "libc.so.6",
        "libm.so.6",
        "libdl.so.2",
        "libpthread.so.0",
        "librt.so.1",
        "libutil.so.1",
        "libnsl.so.1",
        "libresolv.so.2",
        "libstdc++.so.6",
        "libgcc_s.so.1",
        "libcrypt.so.1",
        "ld-linux-x86-64.so.2",
        "ld-linux-aarch64.so.1",
        # zlib: required by CPython's core `zlib` module, so it is guaranteed
        # present (shared lib, not just the Python module) in any CPython
        # base image regardless of what the target package needs it for.
        "libz.so.1",
    }
)


def download_target_wheel(
    name: str,
    version: str | None,
    *,
    platform_tag: str,
    py_version: str,
    abi: str,
    dest: str,
    executor: Executor,
) -> str | None:
    """Download the target wheel into ``dest`` (no install); return path or None.

    ``--only-binary=:all:`` makes sdist-only or incompatible packages fail fast
    (returns None -> caller falls through to build-essential seeding). The host
    interpreter is invoked via ``sys.executable`` because the dev host may have
    no bare ``python`` on PATH. Any executor failure / empty result returns None
    so the stage degrades to a no-op.
    """
    spec = f"{name}=={version}" if version else name
    cmd = (
        f"{shlex.quote(sys.executable)} -m pip download "
        "--no-deps --only-binary=:all: "
        f"--dest {shlex.quote(dest)} "
        f"--platform {shlex.quote(platform_tag)} "
        f"--python-version {shlex.quote(py_version)} "
        f"--implementation cp --abi {shlex.quote(abi)} "
        f"{shlex.quote(spec)}"
    )
    try:
        result = executor.run(cmd, timeout=300)
    except Exception:
        return None
    if not result.ok:
        return None
    try:
        wheels = [f for f in os.listdir(dest) if f.endswith(".whl")]
    except OSError:
        return None
    if not wheels:
        return None
    return os.path.join(dest, sorted(wheels)[0])


def inspect_wheel_sonames(wheel_path: str) -> set[str]:
    """External sonames a wheel needs at run time, incl. transitive-via-bundled.

    Reads ``DT_NEEDED`` from the wheel's extension module(s) AND its bundled
    ``<dist>.libs/`` shared libraries (mirroring what ``ldd`` resolves
    transitively post-install), then subtracts base-image/toolchain libs and the
    wheel's own bundled library names (satisfied internally). Empty set for
    pure-Python wheels or on any read error (caller no-ops).
    """
    needed: set[str] = set()
    provided: set[str] = set()  # sonames the wheel supplies internally
    scan: list = []             # zip members whose DT_NEEDED we read
    try:
        with zipfile.ZipFile(wheel_path) as zf:
            for info in zf.infolist():
                filename = info.filename
                base = os.path.basename(filename)
                if not (base.endswith(".so") or ".so." in base):
                    continue
                if _BUNDLED_DIR_RE.search(filename):
                    provided.add(base)  # a lib the wheel bundles
                    scan.append(info)   # ...but still walk it for transitive deps
                elif _EXT_SO_RE.search(base):
                    scan.append(info)   # the extension module itself
                # else: a top-level non-extension .so — ignore
            for info in scan:
                try:
                    data = zf.read(info)
                except Exception:
                    continue
                needed |= _dt_needed(io.BytesIO(data))
    except Exception:
        return set()
    return {s for s in needed if s not in _BASE_IMAGE_SONAMES and s not in provided}


def _dt_needed(fileobj: io.BytesIO) -> set[str]:
    """``DT_NEEDED`` sonames from one ELF via pyelftools; empty set on non-ELF."""
    out: set[str] = set()
    try:
        elf = ELFFile(fileobj)
    except Exception:
        return out
    for section in elf.iter_sections():
        if not isinstance(section, DynamicSection):
            continue
        for tag in section.iter_tags():
            if getattr(tag, "entry", None) is not None and tag.entry.d_tag == "DT_NEEDED":
                out.add(tag.needed)
    return out


# === wheel_oracle: wheel-vs-sdist build-from-source oracle ===
# A CPython interpreter tag: ``cp310`` -> (3, 10); ``cp39`` -> (3, 9). The minor
# component is multi-digit (``cp310``), the major single-digit, so we can't just
# read two characters.
_CP_TAG_RE = re.compile(r"^cp(\d)(\d+)$")


def _cp_version(tag: str) -> tuple[int, int] | None:
    """``"cp310"`` -> ``(3, 10)``; non-CPython tags -> ``None``."""
    m = _CP_TAG_RE.match(tag)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _python_tag_ok(py_tag: str, abi_tag: str, target_python: str) -> bool:
    """Whether a wheel's ``{py_tag}-{abi_tag}`` is installable on ``target_python``.

    ``target_python`` is a ``"3.10"``-style minor. Compressed tag SETS are
    dot-separated (``py2.py3``, ``cp39.cp310``) and match when ANY member does.
    Rules (PEP 425, the cases that occur in practice):

    * ``py3`` / ``py{major}`` / ``py{major}{minor}`` — pure-python, any CPython
      of that major (or exact minor) — installable.
    * ``cp{X}{Y}`` — CPython ABI wheel — installable only on the SAME ``X.Y``…
    * …UNLESS the abi tag is ``abi3`` (stable ABI): a ``cpXY-abi3`` wheel is
      forward-compatible — installable on any CPython ``>= X.Y`` (never earlier).
    * anything else (``pp*`` PyPy, ``graalpy*`` …) — not a CPython-target match.
    """
    try:
        maj_s, min_s = target_python.split(".")[:2]
        target = (int(maj_s), int(min_s))
    except (ValueError, IndexError):
        return True  # unparseable target -> don't over-reject (legacy-safe)

    for t in py_tag.split("."):
        if t in ("py3", f"py{target[0]}", f"py{target[0]}{target[1]}"):
            return True
        cp = _cp_version(t)
        if cp is None:
            continue
        if cp == target:
            return True
        # Stable-ABI wheels install on any interpreter at or above their floor.
        if abi_tag == "abi3" and cp <= target:
            return True
    return False


def _wheel_python_tags(filename: str) -> tuple[str, str] | None:
    """``(python_tag, abi_tag)`` of a ``.whl`` filename, or ``None`` if unparseable.

    A wheel name is ``{dist}-{version}(-{build})?-{py}-{abi}-{platform}.whl``.
    The distribution name and version never contain ``-`` (they are escaped to
    ``_``), so the LAST three ``-``-separated fields are always py/abi/platform.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) < 3:
        return None
    return parts[-3], parts[-2]


def _artifact_filename(artifact: dict) -> str | None:
    """Filename of an sdist/wheel lock entry (explicit, or derived from url)."""
    if not isinstance(artifact, dict):
        return None
    name = artifact.get("filename")
    if name:
        return name
    url = artifact.get("url")
    if url:
        return url.rsplit("/", 1)[-1]
    return None


def _wheel_matches_platform(
    filename: str | None,
    target_platform: str,
    target_python: str | None = None,
) -> bool:
    """True when ``filename`` is installable on the target env.

    Universal wheels (``...-none-any.whl``) match every platform. Otherwise the
    target's arch token (e.g. ``x86_64`` / ``aarch64``) must appear in a *linux*
    platform tag; macOS/Windows wheels never match a linux target.

    When ``target_python`` (a ``"3.10"``-style minor) is given, the wheel's
    INTERPRETER tag must also be compatible — a ``cp311`` wheel does NOT match a
    py3.10 target even though its arch does (Fix A: onnxruntime 1.24.3 ships only
    ``cp311+`` wheels yet its ``requires-python`` metadata claims ``>=3.10``, so a
    universal ``uv lock`` pins it onto py3.10 with no installable artifact). When
    ``target_python`` is ``None`` the check is platform-only (legacy behavior).
    """
    if not filename:
        return False
    low = filename.lower()
    if not low.endswith(".whl"):
        return False
    if low.endswith("-none-any.whl"):
        return True  # pure-python, cross-platform, cross-interpreter.
    arch = (target_platform.split("-", 1)[0] if target_platform else "").lower()
    if not arch:
        return False
    if "linux" not in low:  # the target is linux; skip macosx_/win_ wheels.
        return False
    if arch not in low:
        return False
    if target_python is not None:
        tags = _wheel_python_tags(filename)
        if tags is not None and not _python_tag_ok(tags[0], tags[1], target_python):
            return False
    return True


def risk_from_packages(
    raw_packages: list[dict],
    target_platform: str,
    target_python: str | None = None,
) -> dict[str, dict]:
    """Map ``package name -> {build_from_source, artifact, hash, installable}`` for
    already fork-resolved, already LOCAL-SOURCE-FILTERED ``[[package]]`` TOML
    entries (one entry per name — the caller, ``resolve_lock.native_risk_from_lock``,
    is responsible for BOTH selecting the target-applicable entry when a lock
    forks a package across resolution markers, AND filtering out local-source
    entries with its own ``_is_local_source`` before ever calling this
    function; this function only decides wheel-vs-sdist per entry and has no
    concept of "local source" at all).

    A package that ships an ``sdist`` but no wheel matching the target
    (platform AND ``target_python`` interpreter tag) must be built from source
    on the target. The chosen artifact is the matching wheel when one exists,
    else the sdist.

    ``installable`` (Fix A) is the honest "can this pin be installed on the
    target at all" signal: True when a target-matching wheel exists OR there is
    an sdist to build; **False** when the resolved version has NEITHER (its only
    wheels target a different interpreter/platform and there is no sdist — e.g.
    onnxruntime 1.24.3's ``cp311+``-only wheels pinned onto py3.10). A
    ``installable=False`` pin must not be emitted as a ``pip install`` line — it
    can only fail — so the caller marks the node UNRESOLVED instead of shipping a
    doomed ``--no-deps name==version``.
    """
    risk: dict[str, dict] = {}
    for pkg in raw_packages:
        name = pkg.get("name")
        if not name:
            continue
        sdist = pkg.get("sdist")
        wheels = pkg.get("wheels", []) or []

        matching_wheel = next(
            (
                w
                for w in wheels
                if _wheel_matches_platform(
                    _artifact_filename(w), target_platform, target_python
                )
            ),
            None,
        )
        has_sdist = isinstance(sdist, dict) and bool(sdist)
        build_from_source = has_sdist and matching_wheel is None
        installable = matching_wheel is not None or has_sdist

        if matching_wheel is not None:
            chosen = matching_wheel
        elif has_sdist:
            chosen = sdist
        elif wheels:
            chosen = wheels[0]
        else:
            chosen = None

        risk[name] = {
            "build_from_source": build_from_source,
            "artifact": _artifact_filename(chosen) if chosen else None,
            "hash": chosen.get("hash") if isinstance(chosen, dict) else None,
            "installable": installable,
        }
    return risk


# === wheel_preflight: pre-install SystemLib priors from target wheel ===
def wheel_preflight_probe(
    graph: DepGraph, host_executor: Executor, target_env: TargetEnv
) -> DepGraph:
    """Seed RESOLVER/UNKNOWN SystemLib priors from each package's target wheel."""
    platform_tag = pip_wheel_platform_tag(target_env)
    py_version = target_env.python_version
    abi = "cp" + py_version.replace(".", "")

    new = graph
    # §1: inspect ONLY packages the artifact map classified as wheels
    # (build_from_source is False, stamped in build.py before this stage). A
    # failed download below is then a soname-read miss, NOT an sdist signal —
    # sdist/unknown packages are skipped (their -dev build deps come from
    # seed_build_deps; their run-time libs from ldd_probe post-install).
    for pkg in [
        n
        for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.version and n.build_from_source is False
    ]:
        try:
            with tempfile.TemporaryDirectory() as dest:
                wheel = download_target_wheel(
                    pkg.name,
                    pkg.version,
                    platform_tag=platform_tag,
                    py_version=py_version,
                    abi=abi,
                    dest=dest,
                    executor=host_executor,
                )
                if wheel is None:
                    continue
                sonames = inspect_wheel_sonames(wheel)
        except Exception:
            continue

        for soname in sorted(sonames):  # sorted -> deterministic node/edge order
            sid = syslib_id(soname)
            if new.get(sid) is None:
                cands = resolve(ObservedNeed("soname", soname, context="runtime"), None)
                apt = cands[0].package if cands else None
                new = new.with_node(
                    make_syslib_node(
                        soname,
                        discovered_by=DiscoveredBy.RESOLVER,
                        state=State.UNKNOWN,
                        apt=apt,
                        provenance=f"wheel:{pkg.name}",
                    )
                )
            new = new.with_edge(
                Edge(src=pkg.id, dst=sid, relation=EdgeType.REQUIRES, origin="resolver")
            )
    versioned = [n for n in graph.nodes if n.type is NodeType.PACKAGE and n.version]
    skipped_sdist = sum(1 for n in versioned if n.build_from_source is True)
    skipped_unknown = sum(1 for n in versioned if n.build_from_source is None)
    inspected = sum(1 for n in versioned if n.build_from_source is False)
    logger.info(
        "wheel_preflight: inspected=%d skipped_sdist=%d skipped_unknown=%d",
        inspected, skipped_sdist, skipped_unknown,
    )
    return new
