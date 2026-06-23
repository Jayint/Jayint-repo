"""Stage orchestrator — repo path in, host-certified ``DepGraph`` out.

Wires the pipeline of
``docs/DESIGN-static-probe-certified-dependency-graph.md`` /
``docs/superpowers/specs/2026-06-23-uv-enriched-depgraph.md`` in order:

    1. scan      static import scan          -> Import + Test nodes   (cycle 1)
    2. map       roots.select_roots          -> resolver roots
    3. resolve   uv.lock closure (HOST)      -> Package nodes/edges   (cycle 2)
    3b. seed     predicted native nodes      -> Tool/SystemLib        (cycle 2)
    4. probe     install + import (CONTAINER)-> SystemLib/Tool nodes  (cycle 3)
    5. certify   host check_commands (CONTAINER) -> node ``state``    (cycle 4)

**Executor split (spec "Architecture change"):** resolution is HOST-side — ``uv``
cross-platform resolves the container target without a container interpreter — so
it runs through ``host_executor``.  Install/probe/certify must observe the real
target environment, so they run through ``container_executor``.  Both default-safe
for unit tests (a single ``FakeExecutor`` can be injected for both).

Discovery order and execution order differ (design 3.3 / 10.10): probing
discovers a SystemLib *after* installing the pip package that needs it, but
certification then runs in execution layer order (system before pip).  Every
stage returns a NEW immutable graph; this function only ever rebinds ``graph``.
"""

from __future__ import annotations

from dataclasses import replace

from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.executor import Executor, LocalSubprocessExecutor
from python_deps.depgraph.probe import import_probe, install_closure
from python_deps.depgraph.resolve import (
    DEFAULT_TARGET_PLATFORM,
    link_imports_to_packages,
    resolve_closure,
)
from python_deps.depgraph.roots import select_roots
from python_deps.depgraph.scan import scan_to_nodes
from python_deps.depgraph.schema import DepGraph
from python_deps.depgraph.seed import seed_predicted_native

# discovered_cycle stamps, one per discovery stage (design 5.2 example uses 3 for
# probe-discovered SystemLibs).
_SCAN_CYCLE = 1
_RESOLVER_CYCLE = 2
_PROBE_CYCLE = 3
_CERTIFY_CYCLE = 4

# uname -m arch token -> modern manylinux target. NEVER manylinux2014 (it silently
# downgrades wheels, e.g. numpy); the 2_28 baseline matches Debian bookworm slim.
_ARCH_TO_PLATFORM: dict[str, str] = {
    "x86_64": "x86_64-manylinux_2_28",
    "amd64": "x86_64-manylinux_2_28",
    "aarch64": "aarch64-manylinux_2_28",
    "arm64": "aarch64-manylinux_2_28",
}


def _restamp(graph: DepGraph, node_ids: set[str], cycle: int) -> DepGraph:
    """Return a new graph with ``discovered_cycle = cycle`` on the named nodes."""
    new = graph
    for node_id in node_ids:
        node = new.get(node_id)
        if node is not None:
            new = new.with_node(replace(node, discovered_cycle=cycle))
    return new


def _detect_target_platform(container_executor: Executor) -> str:
    """Probe the container's arch once and map it to a manylinux target.

    Falls back to ``DEFAULT_TARGET_PLATFORM`` when the probe fails or the arch is
    unrecognized (never ``manylinux2014``).
    """
    result = container_executor.run("uname -m")
    arch = (result.stdout or "").strip().lower() if result.ok else ""
    return _ARCH_TO_PLATFORM.get(arch, DEFAULT_TARGET_PLATFORM)


def build_dep_graph(
    repo_path: str,
    container_executor: Executor,
    *,
    host_executor: Executor | None = None,
    target_python: str = "3.11",
    target_platform: str | None = None,
    exclude_newer: str | None = None,
) -> DepGraph:
    """Build a host-certified dependency graph for ``repo_path``.

    ``container_executor`` runs install/probe/certify inside the target container;
    ``host_executor`` (default :class:`LocalSubprocessExecutor`) runs the
    host-side ``uv`` resolve.  ``target_platform`` defaults to the container's arch
    (detected once via ``uname -m``).  See the module docstring for the staged
    pipeline.  Returns the final immutable ``DepGraph``; certificates produced
    here are provisional (scratch-container scope) per design section 4.6.
    """
    host_executor = host_executor or LocalSubprocessExecutor()

    # Stage 1 — static import scan -> Import + Test nodes.
    graph = scan_to_nodes(repo_path)
    graph = _restamp(graph, {n.id for n in graph.nodes}, _SCAN_CYCLE)

    # Stage 2 — manifest-first, scan-gap-filled, filtered resolver roots.
    roots = select_roots(repo_path, graph)

    # Stage 3 — HOST-side uv resolve, targeted at the container.
    platform = target_platform or _detect_target_platform(container_executor)
    pkg_nodes, pkg_edges = resolve_closure(
        roots,
        host_executor,
        target_python=target_python,
        target_platform=platform,
        exclude_newer=exclude_newer,
    )
    pre_resolve_ids = {n.id for n in graph.nodes}
    for node in pkg_nodes:
        graph = graph.with_node(node)
    for edge in pkg_edges:
        graph = graph.with_edge(edge)

    # Stage 3a — reconcile: link EVERY Import to its resolved Package (covers
    # manifest-declared deps whose root carried import_id=None, which would
    # otherwise leave the scanned Import node orphaned from its Package).
    graph = link_imports_to_packages(graph)

    # Stage 3b — predicted native Tool/SystemLib nodes (resolver-origin).
    graph = seed_predicted_native(graph)
    resolver_ids = {n.id for n in graph.nodes} - pre_resolve_ids
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)

    # Stage 4 — CONTAINER probe: install once (build-time gaps -> Tool) then
    # import-probe (run-time gaps -> SystemLib); predictions reconcile in place.
    pre_probe_ids = {n.id for n in graph.nodes}
    graph = install_closure(graph, container_executor)
    graph = import_probe(graph, container_executor)
    probe_ids = {n.id for n in graph.nodes} - pre_probe_ids
    graph = _restamp(graph, probe_ids, _PROBE_CYCLE)

    # Stage 5 — host certification in the container (layer-ordered; flips state).
    graph = certify_all(graph, container_executor, cycle=_CERTIFY_CYCLE)

    return graph
