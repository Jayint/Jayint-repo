"""Curated + declared build-time system-dependency priors (capability-keyed).

Folds the three build-dep leaves into one concept file (3c-3); the Debian
``Build-Depends`` reader stays split in ``debian_builddeps`` (R3, ceiling):
  * ``build_deps`` — the curated ``PACKAGE_TO_BUILD_NEEDS`` table + ``seed_build_deps``
    / ``build_dep_prior`` assembly (table + flavor overrides + Debian + PEP 725);
  * ``pep725`` — the PEP 725 ``[external]`` reader (``needs_from_pyproject`` /
    ``pep725_external``), fetched off the sdist and mapped to capability needs;
  * ``seed`` — the wheel-oracle ``build-essential`` FLOOR prior for from-source
    packages (``seed_wheel_oracle_prior`` / ``_build_essential_node``).
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, replace
from typing import NamedTuple

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from graph.contracts.executor import Executor
from graph.model import apt_build_id, tool_id
from graph.model import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from graph.python.native.apt import ObservedNeed, capability_id, resolve
from graph.python.native.debian_builddeps import debian_build_deps
from graph.python.util.import_mapping import normalize_package_name

logger = logging.getLogger(__name__)


# === build_deps: curated PACKAGE_TO_BUILD_NEEDS table + seed_build_deps ===
# Canonical package name -> the capability need(s) its from-source build reports.
# Confident, PoC/well-known-verified subset (see plan Scope note). resolve()
# maps each capability -> apt via PROVIDER_TABLE at construction (table-only).
PACKAGE_TO_BUILD_NEEDS: dict[str, tuple[ObservedNeed, ...]] = {
    "psycopg2": (ObservedNeed("binary", "pg_config", context="build", strength="curated"),),
    "mysqlclient": (ObservedNeed("binary", "mysql_config", context="build", strength="curated"),),
    "pycairo": (ObservedNeed("pkgconfig", "cairo", context="build", strength="curated"),),
    "pyaudio": (ObservedNeed("header", "portaudio.h", context="build", strength="curated"),),
    "pyodbc": (ObservedNeed("header", "sql.h", context="build", strength="curated"),),
    "python-ldap": (
        ObservedNeed("header", "ldap.h", context="build", strength="curated"),
        ObservedNeed("header", "sasl/sasl.h", context="build", strength="curated"),
    ),
    "dbus-python": (
        ObservedNeed("pkgconfig", "dbus-1", context="build", strength="curated"),
        ObservedNeed("pkgconfig", "glib-2.0", context="build", strength="curated"),
    ),
    "python-snappy": (ObservedNeed("header", "snappy.h", context="build", strength="curated"),),
}
# pygobject is deliberately EXCLUDED — its girepository dev pkg + pkgconfig
# module differ by Debian release (bookworm: libgirepository1.0-dev /
# gobject-introspection-1.0; trixie: libgirepository-2.0-dev /
# girepository-2.0) and by pygobject version, so a single curated entry would
# be wrong on one base. Left to observe-time apt-file + apt_verify.

@dataclass(frozen=True)
class FlavorOverride:
    """Curated correction for a multi-backend native lib.

    ``needs`` are forced, highest-priority CORE capability needs that pin the
    backend the sdist actually expects (overriding the distro's default — e.g.
    Debian builds pycurl against gnutls but the openssl backend is what most
    stacks want). ``apt_directives`` are forced apt names for corrections that are
    best expressed as an apt package rather than a capability (empty for pycurl;
    available for future libs). ``build_env`` are build-time env vars stamped onto
    the owner package node's ``data["build_env"]`` (rendering the ``export`` before
    the pip build is a populate.py follow-up, out of Part 3 scope). ``suppress_apt``
    are Debian apt names this flavor's backend pin makes conflicting/unwanted (e.g.
    pycurl's Debian Build-Depends hard-lists the gnutls curl backend even though the
    flavor forces openssl; both landing in one ``apt-get install`` breaks it since
    they are mutually ``Conflicts:``) — dropped from the Debian directives.
    """
    needs: tuple[ObservedNeed, ...]
    build_env: tuple[tuple[str, str], ...] = ()
    apt_directives: tuple[str, ...] = ()
    suppress_apt: tuple[str, ...] = ()


FLAVOR_OVERRIDES: dict[str, FlavorOverride] = {
    # pycurl links whatever curl-config points at; Debian's default curl is gnutls,
    # but the openssl backend is the common expectation. Force the openssl -dev pair
    # (curl-config -> libcurl4-openssl-dev, openssl/ssl.h -> libssl-dev via
    # PROVIDER_TABLE) and pin the backend with PYCURL_SSL_LIBRARY=openssl.
    "pycurl": FlavorOverride(
        needs=(
            ObservedNeed("binary", "curl-config", context="build", strength="curated"),
            ObservedNeed("header", "openssl/ssl.h", context="build", strength="curated"),
        ),
        build_env=(("PYCURL_SSL_LIBRARY", "openssl"),),
        # Debian's real Build-Depends hard-lists the gnutls curl backend; suppress
        # it (+ the gnutls lib it pulls) so it doesn't land alongside the
        # flavor-forced libcurl4-openssl-dev (mutually Conflicts: at apt-install
        # time). libssh2-1-dev is a legitimate non-conflicting build dep — kept.
        suppress_apt=("libcurl4-gnutls-dev", "libgnutls28-dev"),
    ),
}


def build_env_for(pkg_name: str) -> dict[str, str]:
    """Build-time env vars a package's flavor override pins (``{}`` when none)."""
    flavor = FLAVOR_OVERRIDES.get(normalize_package_name(pkg_name))
    return dict(flavor.build_env) if flavor and flavor.build_env else {}


@dataclass(frozen=True)
class BuildDepPlan:
    """The assembled build-time prior for one sdist package.

    ``capability_needs`` — precise, capability-keyed needs (curated/flavor/PEP 725),
      ALWAYS seeded as capability nodes (they reconcile with the observe path).
    ``apt_directives`` — Debian (+ forced-flavor) apt package names to seed as
      apt-keyed ``aptdep:`` nodes that pre-satisfy the build.
    """
    capability_needs: list[ObservedNeed]
    apt_directives: list[str]


def _resolved_apt(need: ObservedNeed, executor: Executor) -> str | None:
    """The apt package a capability need resolves to: table first (no container),
    else the container's apt-file. ``None`` when unresolved (then it covers no
    Debian directive). Used only for de-duplicating the Debian apt list."""
    cands = resolve(need, executor=None)
    if not cands and executor is not None:
        cands = resolve(need, executor)
    return cands[0].package if cands else None


def _apt_installable(pkgs: list[str], executor: Executor) -> bool:
    """True iff ``apt-get install -s`` (SIMULATE — no download/compile) resolves the
    set cleanly. The apt=0 guard against a Debian source whose full Build-Depends
    conflict or don't exist (uWSGI's per-plugin -devs). Empty set is installable."""
    if not pkgs:
        return True
    quoted = " ".join(shlex.quote(p) for p in pkgs)
    return executor.run(f"apt-get install -s {quoted}").returncode == 0


def _dedup(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def build_dep_prior(
    pkg_name: str, version: str | None, executor: Executor
) -> BuildDepPlan:
    """Assemble the build-time ``-dev`` prior for one sdist package.

    Capability needs (priority union flavor > PEP 725 > curated, deduped by
    ``capability_id``, all ``context="build"``/``strength="curated"``) are the
    precise, always-seeded part. Debian ``Build-Depends`` are apt install
    directives: any name a capability node already resolves to is dropped
    (dedup at the resolved-apt level); the remaining names are always seeded
    as ``apt_directives`` (no threshold). A flavor's forced apt names are
    always directives. Empty plan when no source contributes.
    """
    canonical = normalize_package_name(pkg_name)
    flavor = FLAVOR_OVERRIDES.get(canonical)
    forced_needs = list(flavor.needs) if flavor else []
    forced_apt = list(flavor.apt_directives) if flavor else []
    pep = list(pep725_external(canonical, version, executor))
    curated = list(PACKAGE_TO_BUILD_NEEDS.get(canonical, ()))

    # Precise capability needs: priority union, deduped by capability_id.
    capability_needs: list[ObservedNeed] = []
    seen_caps: set[str] = set()
    for need in (*forced_needs, *pep, *curated):  # priority order
        cid = capability_id(need)
        if cid in seen_caps:
            continue
        seen_caps.add(cid)
        capability_needs.append(replace(need, context="build", strength="curated"))

    # Apt names already covered by a capability node (+ forced flavor apt).
    covered: set[str] = set(forced_apt)
    if flavor:
        covered.update(flavor.suppress_apt)
    for need in capability_needs:
        apt = _resolved_apt(need, executor)
        if apt:
            covered.add(apt)
    # seed_build_deps always baseline-seeds binary:pkg-config (-> apt:pkgconf),
    # so a Debian source that explicitly lists pkg-config/pkgconf is already
    # covered — drop it here rather than seed a redundant second aptdep node.
    covered.update(("pkg-config", "pkgconf"))

    # Debian directives minus anything already covered by a capability node.
    raw_debian = debian_build_deps(canonical, executor)
    covered_debian = [n for n in raw_debian if n in covered]
    debian = [n for n in raw_debian if n not in covered]
    if debian and not _apt_installable(debian, executor):
        logger.info("build_dep_prior: %s debian set not apt-installable, dropping %s",
                    canonical, debian)
        debian = []
    apt_directives = _dedup(forced_apt + debian)
    logger.info(
        "build_dep_prior: %s caps=%d debian_apt=%d dedup_dropped=%s",
        canonical, len(capability_needs), len(debian), covered_debian,
    )
    return BuildDepPlan(capability_needs, apt_directives)


# build-time capability -> NodeType.TOOL / Layer.TOOLCHAIN (renders before pip).

_PKG_CONFIG_NEED = ObservedNeed("binary", "pkg-config", context="build", strength="curated")


def _capability_node(need: ObservedNeed, executor: Executor) -> Node:
    """A capability-keyed RESOLVER/UNKNOWN node; chosen_fix resolved via table
    then, on a table miss, the container's apt-file (now that seed has an
    executor). Reconciles with the observe path on ``capability_id``."""
    cands = resolve(need, executor)
    top = cands[0] if cands else None
    fix = f"apt:{top.package}" if top else None
    return Node(
        id=capability_id(need),
        type=NodeType.TOOL,
        name=need.name,
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.UNKNOWN,
        check_command=top.check_command if top else None,
        fix_candidates=(fix,) if fix else (),
        chosen_fix=fix,
        provenance="curated build-dep prediction",
        data={
            "kind": need.kind,
            "context": need.context,
            "observation_strength": need.strength,
            "resolution_status": "resolved" if fix else "unresolved",
        },
    )


def _apt_build_node(name: str) -> Node:
    """An apt-keyed Debian build directive node (separate ``aptdep:`` id space).

    The apt name IS the fix, so ``chosen_fix=apt:<name>`` -> it renders (emit
    installs it, TOOLCHAIN tier before pip) and its ``dpkg`` check certifies
    (installed? SATISFIED : MISSING). Seeded UNKNOWN; certify flips it, and a
    MISSING apt: TOOL is emittable/reciped. Pre-satisfies the build, so unlike a
    capability node it never reconciles with an observation.
    """
    check = (
        f"dpkg-query -W -f='${{Status}}' {name} "
        f"2>/dev/null | grep -q 'install ok installed'"
    )
    return Node(
        id=apt_build_id(name),
        type=NodeType.TOOL,
        name=name,
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.UNKNOWN,
        check_command=check,
        fix_candidates=(f"apt:{name}",),
        chosen_fix=f"apt:{name}",
        provenance="debian build-dep",
        data={"source": "debian"},
    )


def _eligible_for_build_deps(graph: DepGraph) -> list[Node]:
    """Source-built packages with a version — the exact filter the loop used inline."""
    return [
        n for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.version and n.build_from_source is not False
    ]


def seed_build_deps_for(
    graph: DepGraph, pkg: Node, executor: Executor
) -> tuple[DepGraph, int, int, int]:
    """Seed ONE package's build-time prior. The verbatim body of ``seed_build_deps``'s loop.

    Returns ``(graph, pkgs_delta, cap_nodes_delta, aptdep_nodes_delta)``.

    THE COUNTERS ARE NOT DECORATION. ``tests/depgraph/test_build_deps.py``
    (``test_seed_build_deps_logs_aggregate``) asserts the log line contains ``pkgs=``,
    ``cap_nodes=`` AND ``aptdep_nodes=``. Drop them and that test fails. Note ``pkgs`` counts
    only packages with a NON-EMPTY plan (the original increments it AFTER the early-continue),
    while cap/aptdep count nodes actually INSERTED.

    Extracted so the react arm can expand a single runtime-discovered node (``discovery_expand``)
    without re-running the whole graph-level pass, which would re-hit the network for every
    source-built package already seeded.
    """
    new = graph
    cap_nodes = aptdep_nodes = 0
    pc_id = capability_id(_PKG_CONFIG_NEED)

    # B3: baseline pkg-config for EVERY source-built package (Debian omits it; slim images
    # lack it). Reuses the binary:pkg-config capability node (os_resolver -> apt:pkgconf),
    # deduped once, plan-independent.
    if new.get(pc_id) is None:
        new = new.with_node(_capability_node(_PKG_CONFIG_NEED, executor))
        cap_nodes += 1
    new = new.with_edge(
        Edge(src=pkg.id, dst=pc_id, relation=EdgeType.REQUIRES, origin="resolver")
    )

    plan = build_dep_prior(pkg.name, pkg.version, executor)
    if not (plan.capability_needs or plan.apt_directives):
        return new, 0, cap_nodes, 0          # pkgs is NOT incremented — matches the original

    # Stamp flavor build_env on the owner package node.
    env = build_env_for(pkg.name)
    if env:
        current = new.get(pkg.id)
        new = new.with_node(replace(current, data={**current.data, "build_env": env}))

    # Capability needs -> capability node + edge.
    for need in plan.capability_needs:
        node_id = capability_id(need)
        if new.get(node_id) is None:
            new = new.with_node(_capability_node(need, executor))
            cap_nodes += 1
        new = new.with_edge(
            Edge(src=pkg.id, dst=node_id, relation=EdgeType.REQUIRES, origin="resolver")
        )

    # Debian apt directives -> apt-keyed aptdep: node + edge.
    for name in plan.apt_directives:
        node_id = apt_build_id(name)
        if new.get(node_id) is None:
            new = new.with_node(_apt_build_node(name))
            aptdep_nodes += 1
        new = new.with_edge(
            Edge(src=pkg.id, dst=node_id, relation=EdgeType.REQUIRES, origin="resolver")
        )
    return new, 1, cap_nodes, aptdep_nodes


def seed_build_deps(graph: DepGraph, executor: Executor) -> DepGraph:
    """Seed the multi-source build-time prior for source-built packages.

    For each source-built Package (``build_from_source`` not False, real version),
    every such package first gets a baseline ``binary:pkg-config`` capability
    node + edge (Debian omits it from Build-Depends as buildd-assumed, and
    slim images lack it — see B3). ``build_dep_prior`` then assembles a
    ``BuildDepPlan``. Capability needs become capability-keyed nodes
    (``binary:``/``header:``/``pkgconfig:``) with a ``requires`` edge (always
    seeded; reconcile with the observe path). Debian apt directives become
    apt-keyed ``aptdep:`` nodes with a ``requires`` edge (pre-satisfy, render
    before pip). A flavor override's ``build_env`` is stamped on the Package
    node. Pure; a no-op when no target package contributes a prior (though
    the baseline pkg-config seed is unconditional for every source-built
    package). ``executor`` lets Debian lookups and off-table capability
    resolution use the target container.

    Graph-level pass — now just the loop over ``seed_build_deps_for``. Behaviour is
    UNCHANGED, including the three-counter log line the existing test asserts on.
    """
    new = graph
    pkgs = cap_nodes = aptdep_nodes = 0
    for pkg in _eligible_for_build_deps(graph):
        new, dp, dc, da = seed_build_deps_for(new, pkg, executor)
        pkgs += dp
        cap_nodes += dc
        aptdep_nodes += da
    logger.info(
        "seed_build_deps: pkgs=%d cap_nodes=%d aptdep_nodes=%d",
        pkgs, cap_nodes, aptdep_nodes,
    )
    return new


# === pep725: PEP 725 [external] reader (was pep725.py) ===
# DepURL ``generic/<name>`` -> (ObservedNeed.kind, capability name). Confident,
# apt-resolvable subset only: each resolves via os_resolver.PROVIDER_TABLE or apt-file,
# preserving the "every emitted need is apt-resolvable" invariant. Unknown generics are
# SKIPPED (never fabricated). Keyed by the lower-cased canonical DepURL name (these
# become PEP 804 registry names once that lands). apt target shown for review:
#   openssl    -> libssl-dev            (PROVIDER_TABLE header openssl/ssl.h)
#   libffi     -> libffi-dev            (apt-file   header ffi.h)
#   zlib       -> zlib1g-dev            (apt-file   header zlib.h)
#   libjpeg    -> libjpeg-dev           (apt-file   header jpeglib.h)
#   pkg-config -> pkgconf               (PROVIDER_TABLE binary pkg-config)
#   cairo      -> libcairo2-dev         (PROVIDER_TABLE pkgconfig cairo)
#   glib[-2.0] -> libglib2.0-dev        (PROVIDER_TABLE pkgconfig glib-2.0)
#   freetype   -> libfreetype-dev       (apt-file   pkgconfig freetype2)
#   libxml2    -> libxml2-dev           (apt-file   pkgconfig libxml-2.0)
#   libcurl    -> libcurl4-openssl-dev  (PROVIDER_TABLE binary curl-config)
_GENERIC_TO_CAPABILITY: dict[str, tuple[str, str]] = {
    "openssl": ("header", "openssl/ssl.h"),
    "libffi": ("header", "ffi.h"),
    "zlib": ("header", "zlib.h"),
    "libjpeg": ("header", "jpeglib.h"),
    "pkg-config": ("binary", "pkg-config"),
    "cairo": ("pkgconfig", "cairo"),
    "glib": ("pkgconfig", "glib-2.0"),
    "glib-2.0": ("pkgconfig", "glib-2.0"),
    "freetype": ("pkgconfig", "freetype2"),
    "libxml2": ("pkgconfig", "libxml-2.0"),
    "libcurl": ("binary", "curl-config"),
}

# The build tier we seed (PEP 725 key names, verbatim). ``dependencies`` (runtime) and
# every ``optional-*`` table are out of scope for V1.
_BUILD_TIER_KEYS = ("build-requires", "host-requires")

# Linux/glibc target for marker evaluation (spec §4 scope). Start from the host's
# default_environment() so python_version/etc. are populated, then force the platform axes.
_TARGET_MARKER_OVERRIDES = {
    "os_name": "posix",
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
}


class DepURLParts(NamedTuple):
    type: str
    namespace: str
    name: str


def parse_depurl(depurl: str) -> DepURLParts | None:
    """Parse a DepURL body into (type, namespace, name); None if not a DepURL.

    Accepts the ``dep:`` scheme (PEP 725) and the ``pkg:`` PURL alias. Strips the
    optional ``@version`` / ``?qualifiers`` / ``#subpath`` tail. Any trailing PEP 508
    marker must already be removed by the caller (see ``_split_marker``).
    """
    s = depurl.strip()
    for scheme in ("dep:", "pkg:"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    else:
        return None
    for sep in ("@", "?", "#"):
        cut = s.find(sep)
        if cut != -1:
            s = s[:cut]
    segments = [seg for seg in s.strip().strip("/").split("/") if seg]
    if len(segments) < 2:
        return None
    return DepURLParts(segments[0], "/".join(segments[1:-1]), segments[-1])


def _split_marker(spec: str) -> tuple[str, str]:
    """Split ``"dep:...; marker"`` into (depurl, marker); marker is "" when absent."""
    depurl, _, marker = spec.partition(";")
    return depurl.strip(), marker.strip()


def _marker_selects_target(marker: str) -> bool:
    """True if ``marker`` applies to the Linux target (or is absent/unparseable).

    Unparseable marker / packaging absent -> True (include): never silently drop a
    declared build dep over a marker we could not evaluate.
    """
    if not marker:
        return True
    try:
        from packaging.markers import Marker, default_environment

        env = dict(default_environment())
        env.update(_TARGET_MARKER_OVERRIDES)
        return bool(Marker(marker).evaluate(env))
    except Exception:
        return True


def _need_for(parts: DepURLParts, source: str) -> ObservedNeed | None:
    """Map one DepURL to a build ``ObservedNeed``; None to SKIP (precise-only).

    - ``virtual/...``  -> skip: compilers are the closure-level toolchain
      (build-essential/cargo); ``interface/blas|lapack`` are flavor-divergent
      (handled by the flavor-override table), not a single apt ``-dev``.
    - non-``generic`` PURL types (pypi/cargo/golang/cran/github/...) -> skip:
      language-ecosystem packages or VCS refs, not OS ``-dev`` libraries.
    - ``generic/<name>`` -> curated capability, or skip when unknown.
    """
    if parts.type != "generic":
        return None
    cap = _GENERIC_TO_CAPABILITY.get(parts.name.lower())
    if cap is None:
        return None
    kind, name = cap
    return ObservedNeed(
        kind,
        name,
        context="build",
        strength="curated",
        evidence=f"pep725:{source}" if source else "pep725",
    )


def parse_external_table(pyproject_text: str) -> list[str]:
    """Raw DepURL strings from ``[external] build-requires`` + ``host-requires``.

    ``[]`` when there is no ``[external]`` table (the common case today) or the TOML is
    malformed — degrade silently.
    """
    try:
        doc = tomllib.loads(pyproject_text)
    except Exception:
        return []
    external = doc.get("external")
    if not isinstance(external, dict):
        return []
    specs: list[str] = []
    for key in _BUILD_TIER_KEYS:
        values = external.get(key)
        if isinstance(values, list):
            specs.extend(v for v in values if isinstance(v, str))
    return specs


def needs_from_pyproject(pyproject_text: str, *, source: str = "") -> list[ObservedNeed]:
    """Confident build ``ObservedNeed``s from a pyproject's ``[external]`` table.

    Marker-filters to the Linux target, maps ``generic/<name>`` DepURLs to curated
    capabilities, dedups by ``capability_id``, preserves order. ``[]`` when there is no
    ``[external]`` table.
    """
    needs: list[ObservedNeed] = []
    seen: set[str] = set()
    for spec in parse_external_table(pyproject_text):
        depurl, marker = _split_marker(spec)
        if not _marker_selects_target(marker):
            continue
        parts = parse_depurl(depurl)
        if parts is None:
            continue
        need = _need_for(parts, source)
        if need is None:
            continue
        cid = capability_id(need)
        if cid in seen:
            continue
        seen.add(cid)
        needs.append(need)
    return needs


def _shallowest_pyproject(names: list[str]) -> str | None:
    """The top-level ``<root>/pyproject.toml`` archive member, if any (shallowest wins)."""
    candidates = [n for n in names if n.rsplit("/", 1)[-1] == "pyproject.toml"]
    if not candidates:
        return None
    return min(candidates, key=lambda n: (n.count("/"), len(n)))


def read_sdist_archive(path: str) -> str | None:
    """``pyproject.toml`` text from an sdist ``.tar.gz``/``.tgz``/``.zip``; None on miss.

    Reads the member in-process (no extraction to disk). None on any error or an sdist
    that ships no ``pyproject.toml`` (legacy ``setup.py``-only source).
    """
    try:
        if path.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                member = _shallowest_pyproject(zf.namelist())
                return zf.read(member).decode("utf-8", "replace") if member else None
        with tarfile.open(path) as tf:  # .tar.gz / .tgz
            member = _shallowest_pyproject(tf.getnames())
            if member is None:
                return None
            handle = tf.extractfile(member)
            return handle.read().decode("utf-8", "replace") if handle else None
    except Exception:
        return None


def fetch_sdist_pyproject(
    pypi_name: str, version: str | None, executor: Executor
) -> str | None:
    """Download the sdist (no deps, no wheel) and return its ``pyproject.toml`` text.

    ``pip download --no-deps --no-binary :all:`` forces the sdist — the only artifact
    that carries ``[external]`` (a wheel ships no ``pyproject.toml``; PyPI JSON exposes
    only core metadata, which omits ``[external]``). Bounded (300 s) and
    failure-tolerant: any download / listing / read failure returns None. ``executor``
    must share the host filesystem (host executor), like ``wheel_preflight``.
    """
    spec = f"{pypi_name}=={version}" if version else pypi_name
    try:
        with tempfile.TemporaryDirectory() as dest:
            cmd = (
                f"{shlex.quote(sys.executable)} -m pip download "
                "--no-deps --no-binary :all: "
                f"--dest {shlex.quote(dest)} {shlex.quote(spec)}"
            )
            try:
                result = executor.run(cmd, timeout=300)
            except Exception:
                return None
            if not result.ok:
                return None
            try:
                archives = sorted(
                    f
                    for f in os.listdir(dest)
                    if f.endswith((".tar.gz", ".tgz", ".zip"))
                )
            except OSError:
                return None
            if not archives:
                return None
            return read_sdist_archive(os.path.join(dest, archives[0]))
    except Exception:
        return None


def pep725_external(
    pypi_name: str, version: str | None, executor: Executor
) -> list[ObservedNeed]:
    """PEP 725 ``[external]`` build/host requirements of ``pypi_name`` as build needs.

    Fetches the sdist's ``pyproject.toml``, reads ``[external]``, maps confident DepURLs
    to capability ``ObservedNeed``s. Returns ``[]`` when the package has no ``[external]``
    table (the near-universal case today) or on any failure — MUST degrade silently.
    Part 3 consumes this (source-priority #1) and wires the edges by ``capability_id``.
    """
    text = fetch_sdist_pyproject(pypi_name, version, executor)
    canonical = normalize_package_name(pypi_name)
    if text is None:
        logger.debug("pep725: %s external=absent needs=0", canonical)
        return []
    needs = needs_from_pyproject(text, source=canonical)
    if parse_external_table(text):
        logger.info("pep725: %s external=present needs=%d", canonical, len(needs))
    else:
        logger.debug("pep725: %s external=absent needs=0", canonical)
    return needs


# === seed: wheel-oracle build-essential FLOOR prior (was seed.py) ===
_BUILD_ESSENTIAL_APT = "build-essential"
_BUILD_ESSENTIAL_ID = tool_id(_BUILD_ESSENTIAL_APT)


def _build_essential_node() -> Node:
    fix = f"apt:{_BUILD_ESSENTIAL_APT}"
    return Node(
        id=_BUILD_ESSENTIAL_ID,
        type=NodeType.TOOL,
        name=_BUILD_ESSENTIAL_APT,
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.UNKNOWN,
        check_command=f"dpkg -s {_BUILD_ESSENTIAL_APT}",
        fix_candidates=(fix,),
        chosen_fix=fix,
        provenance="wheel-oracle (build_from_source)",
    )


def seed_wheel_oracle_prior(graph: DepGraph) -> DepGraph:
    """Emit ONE ``tool:build-essential`` FLOOR node for every from-source or unknown Package.

    This is the generic floor only: every source-built package needs a compiler.
    Specific ``-dev`` priors are seeded separately by ``build_deps.seed_build_deps``
    and union with this node (distinct ids — neither erases the other).

    For each ``Package`` with ``build_from_source`` not False (i.e., True or None —
    known from-source builds or unknown build mode from degraded resolution), add a
    ``requires`` edge to the single, deduped ``build-essential`` Tool node (created
    once, on first need). Only packages with a confirmed matching wheel
    (``build_from_source=False``) skip the prediction. Unresolved diagnostic packages
    (``version=None``, representing resolver failures) are always excluded. Returns
    a NEW graph; a no-op when no package needs a source build.
    """
    new = graph
    packages = [
        n for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.version and n.build_from_source is not False
    ]
    if not packages:
        return new
    if new.get(_BUILD_ESSENTIAL_ID) is None:
        new = new.with_node(_build_essential_node())
    for pkg in packages:
        new = new.with_edge(
            Edge(src=pkg.id, dst=_BUILD_ESSENTIAL_ID, relation=EdgeType.REQUIRES, origin="resolver")
        )
    return new
