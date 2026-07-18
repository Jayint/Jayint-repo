"""Closure install stage (spec §4 probe, install-EXECUTION half).

Split (3c-4) from the former ``native/probe.py``: run ONE ``uv pip install`` of the
resolved closure, surface build-time toolchain gaps (each -> a ``Tool`` node via the
native ``_ingest_need``), and salvage survivors when a single package's wheel build
aborts the all-or-nothing install. The gap-INGEST half (``extract_needs`` -> node)
and the run-time import/test-gate probes stayed on the native side
(``native/system_libs``) and are imported here — install depends on native, never the
reverse.
"""

from __future__ import annotations

import re
from collections import defaultdict

from graph.contracts.executor import Executor
from graph.model import Attempt, DepGraph, Edge, EdgeType, Node, NodeType, State
from graph.python.native.system_libs import _ingest_need, extract_needs
from graph.python.native.tables import NATIVE_RISK_PACKAGES
from graph.python.util.import_mapping import normalize_package_name


# Timeout (seconds) for the one bulk closure install. A cold install of a large
# closure (downloads + any from-source build) routinely exceeds the executor's
# 300s default; a false timeout would mark the install failed and cascade the
# whole graph to MISSING at certification, so give it generous headroom.
INSTALL_TIMEOUT = 900

# Bounded rounds of "drop the build-failing packages, reinstall the survivors" so a
# single un-buildable package cannot starve the whole closure (and the probe/relink).
MAX_INSTALL_ROUNDS = 3

# A wheel-build failure prints the distribution being built; used to attribute a
# build-time toolchain gap to the package that triggered it.
_WHEEL_FOR_RE = re.compile(r"[Bb]uilding wheel for ([A-Za-z0-9_.][A-Za-z0-9_.-]*)")

# Stable pip "this distribution's wheel build failed" summaries (pip 21+). Used to
# drop only the un-buildable packages and reinstall the rest.
_FAILED_WHEEL_RE = re.compile(r"Failed building wheel for ([A-Za-z0-9_.][A-Za-z0-9_.-]*)")

_COULD_NOT_BUILD_RE = re.compile(r"Could not build wheels for ([A-Za-z0-9_.,\s-]+?),?\s+which")

_FAILED_TO_BUILD_RE = re.compile(
    r"Failed to build installable wheels for some pyproject\.toml based projects "
    r"\(([A-Za-z0-9_.,\s-]+)\)"
)

# uv (0.11+) build-failure markers. uv does NOT use pip's "Building wheel for X" /
# "Failed building wheel for X"; it prints "Building <name>==<version>" and
# "× Failed to build `<name>==<version>`". Parse both installers so attribution +
# survivor-drop are installer-independent (extract_needs already is — it keys off
# the forwarded compiler/linker text, not the pip/uv framing).
_UV_BUILDING_RE = re.compile(r"(?m)^\s*Building ([A-Za-z0-9_.][A-Za-z0-9_.-]*)==")

_UV_FAILED_RE = re.compile(r"Failed to build `([A-Za-z0-9_.][A-Za-z0-9_.-]*)==")


def install_closure(graph: DepGraph, executor: Executor) -> DepGraph:
    """Install the resolved closure once; surface build-time toolchain gaps.

    Returns a new graph with a ``Tool`` node + ``requires`` edge for every
    recognised build-time gap, and an install ``Attempt`` recorded on every
    installed ``Package`` node.

    Resolver-diagnosed ``MISSING`` packages (unresolvable / conflict placeholders
    with no real version) are excluded from the bulk install: adding one makes the
    single ``pip install`` fail and poisons the whole closure (every good package
    would then certify ``MISSING``), defeating per-root resilience.
    """
    packages = [
        n
        for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.state is not State.MISSING
    ]
    if not packages:
        return graph

    command = _install_cmd(" ".join(_spec(p) for p in _sorted(packages)))
    result = executor.run(command, timeout=INSTALL_TIMEOUT)
    outcome = "succeeded" if result.ok else "failed"

    new = graph
    install_attempt = Attempt(command=command, outcome=outcome)
    for pkg in packages:
        node = new.get(pkg.id)
        new = new.with_node(node.with_attempt(install_attempt))

    if result.ok:
        return new

    stderr = result.stderr or ""
    owners = _build_owners(packages, stderr)
    for need in extract_needs(stderr, context_hint="build"):
        new, node_id = _ingest_need(
            new, need, stderr=stderr, command=command, executor=executor
        )
        for src in owners:
            new = new.with_edge(
                Edge(src=src, dst=node_id, relation=EdgeType.REQUIRES, origin="probe")
            )

    # Salvage the survivors: a single un-buildable package (e.g. an off-platform
    # optional dep) aborts the all-or-nothing bulk install and leaves the whole
    # closure uninstalled, starving the import probe and the relink. Drop the
    # build-failing packages and reinstall the rest.
    new = _reinstall_survivors(new, packages, stderr, executor)
    return new


def _failed_build_packages(stderr: str) -> set[str]:
    """Canonical (PEP 503) names of distributions whose wheel build FAILED.

    Parses the stable pip failure summaries so the un-buildable packages can be
    dropped and the rest reinstalled. Returns an empty set when no build failure
    can be attributed (the caller then changes nothing — never worse than today).
    """
    names: set[str] = set()
    for match in _FAILED_WHEEL_RE.finditer(stderr):
        names.add(match.group(1))
    for match in _UV_FAILED_RE.finditer(stderr):  # NEW: uv format
        names.add(match.group(1))
    for pattern in (_COULD_NOT_BUILD_RE, _FAILED_TO_BUILD_RE):
        for match in pattern.finditer(stderr):
            names.update(part.strip() for part in match.group(1).split(",") if part.strip())
    return {normalize_package_name(name) for name in names}


def _requirers_of_failed(
    graph: DepGraph, packages: list[Node], failed: set[str]
) -> set[str]:
    """Package node ids that TRANSITIVELY require any failed-build package.

    Reinstalling such a package re-pulls the un-buildable dependency (its own
    install_requires), so the survivor salvage must drop them too — otherwise
    each round re-introduces the failure and the loop never converges. BFS over
    package->package ``requires`` edges, reversed (dst failed -> drop its srcs).
    """
    pkg_ids = {p.id for p in packages}
    by_name = {normalize_package_name(p.name): p.id for p in packages}
    failed_ids = {by_name[name] for name in failed if name in by_name}

    requirers: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if (edge.relation is EdgeType.REQUIRES
                and edge.src in pkg_ids and edge.dst in pkg_ids):
            requirers[edge.dst].add(edge.src)

    drop: set[str] = set()
    frontier = set(failed_ids)
    while frontier:
        nxt: set[str] = set()
        for fid in frontier:
            for src in requirers.get(fid, ()):
                if src not in drop and src not in failed_ids:
                    drop.add(src)
                    nxt.add(src)
        frontier = nxt
    return drop


def _reinstall_survivors(
    graph: DepGraph, packages: list[Node], stderr: str, executor: Executor
) -> DepGraph:
    """Reinstall the closure minus packages whose build failed AND anything that
    transitively requires them (a requirer re-pulls the un-buildable dep). Bounded
    rounds. Returns a NEW graph; a no-op when nothing is attributable/droppable.
    """
    failed = _failed_build_packages(stderr)
    drop_ids = _requirers_of_failed(graph, packages, failed)
    survivors = [
        p for p in packages
        if normalize_package_name(p.name) not in failed and p.id not in drop_ids
    ]
    if not failed or not survivors or len(survivors) == len(packages):
        return graph

    new = graph
    for _round in range(MAX_INSTALL_ROUNDS):
        command = _install_cmd(" ".join(_spec(p) for p in _sorted(survivors)))
        result = executor.run(command, timeout=INSTALL_TIMEOUT)
        attempt = Attempt(command=command, outcome="succeeded" if result.ok else "failed")
        for pkg in survivors:
            new = new.with_node(new.get(pkg.id).with_attempt(attempt))
        if result.ok:
            break
        more_failed = _failed_build_packages(result.stderr or "")
        more_drop = _requirers_of_failed(graph, packages, more_failed)
        next_survivors = [
            p for p in survivors
            if normalize_package_name(p.name) not in more_failed and p.id not in more_drop
        ]
        if not more_failed or not next_survivors or len(next_survivors) == len(survivors):
            break  # no further progress
        survivors = next_survivors
    return new


def _build_owners(packages: list[Node], stderr: str) -> set[str]:
    """Packages a build-time gap is attributable to.

    Prefer the distribution named in a "Building wheel for X" (pip) or
    "Building X==version" (uv) line; otherwise fall back to the native-risk
    packages present in the closure (the gap came from *some* compiled build,
    and those are the ones that compile). Matching is on the PEP 503
    canonical name (uv emits the canonical name; pip's "Building wheel for X"
    already is the distribution name) so both installers attribute the same.
    """
    by_name = {normalize_package_name(p.name): p.id for p in packages}
    owners = {
        by_name[normalize_package_name(m.group(1))]
        for rx in (_WHEEL_FOR_RE, _UV_BUILDING_RE)
        for m in rx.finditer(stderr)
        if normalize_package_name(m.group(1)) in by_name
    }
    if owners:
        return owners
    return {p.id for p in packages if p.name in NATIVE_RISK_PACKAGES}


# --------------------------------------------------------------------------- #
# Small pure helpers                                                          #
# --------------------------------------------------------------------------- #
def _install_cmd(specs: str) -> str:
    """The closure-install command. uv installs into the container's SYSTEM
    python (where pip installs today, so import_probe/ldd_probe find the .so),
    using hardlinks from its cache (fast on warm cache) + parallel downloads.
    Build-backend errors pass through to stderr identically to pip, so
    extract_needs still surfaces toolchain gaps. Cache stays ON (no --no-cache)."""
    return f"uv pip install --system {specs}"


def _spec(pkg: Node) -> str:
    return f"{pkg.name}=={pkg.version}" if pkg.version else pkg.name


def _sorted(packages: list[Node]) -> list[Node]:
    return sorted(packages, key=lambda n: n.name)
