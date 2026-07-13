"""Wheel-vs-sdist build-from-source oracle (construction-enrichment cluster 1a).

Extracted from ``resolve_lock.py`` as a pure refactor — behavior preserved
exactly for the wheel/sdist tag-matching decision. Self-contained (stdlib
only) so ``resolve_lock.py -> wheel_oracle.py`` is the only import edge (no
cycle). This module has NO concept of "local source" (the synthetic resolve
root, or a path/editable dependency) at all — ``resolve_lock.py`` keeps a thin
``native_risk_from_lock`` wrapper that does the uv.lock TOML parse, the
target-aware fork selection (shared with ``parse_uv_lock``, so it must stay
there), AND filters out local-source entries with its OWN, already-existing
``_is_local_source`` BEFORE calling :func:`risk_from_packages` here.
"""

from __future__ import annotations

import re

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
