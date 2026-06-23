"""Stage orchestrator — repo path in, host-certified ``DepGraph`` out.

Wires the five pipeline stages of
``docs/DESIGN-static-probe-certified-dependency-graph.md`` in order:

    1. scan      static import scan        -> Import + Test nodes   (cycle 1)
    2. map       import -> distribution     -> resolver roots
    3. resolve   uv pip compile closure     -> Package nodes/edges  (cycle 2)
    4. probe     install + import probing    -> SystemLib/Tool nodes (cycle 3)
    5. certify   host check_commands         -> node ``state``       (cycle 4)

Discovery order and execution order differ (design 3.3 / 10.10): probing
discovers a SystemLib *after* installing the pip package that needs it, but
certification then runs in execution layer order (system before pip).  Every
stage returns a NEW immutable graph; this function only ever rebinds ``graph``.

The resolver/install/probe/certify commands all flow through the injected
``Executor`` so the whole pipeline is testable with a ``FakeExecutor`` (no
Docker/network/uv).  For real use, inject a ``DockerExecutor`` started from the
``python:3.11-slim`` base image (locked decision 3).
"""

from __future__ import annotations

from dataclasses import replace

from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.executor import Executor
from python_deps.depgraph.naming import package_roots
from python_deps.depgraph.probe import import_probe, install_closure
from python_deps.depgraph.resolve import resolve_closure
from python_deps.depgraph.scan import scan_to_nodes
from python_deps.depgraph.schema import DepGraph

# discovered_cycle stamps, one per discovery stage (design 5.2 example uses 3 for
# probe-discovered SystemLibs).
_SCAN_CYCLE = 1
_RESOLVER_CYCLE = 2
_PROBE_CYCLE = 3
_CERTIFY_CYCLE = 4


def _restamp(graph: DepGraph, node_ids: set[str], cycle: int) -> DepGraph:
    """Return a new graph with ``discovered_cycle = cycle`` on the named nodes."""
    new = graph
    for node_id in node_ids:
        node = new.get(node_id)
        if node is not None:
            new = new.with_node(replace(node, discovered_cycle=cycle))
    return new


def build_dep_graph(
    repo_path: str,
    executor: Executor,
    *,
    base_python: str = "3.11",
) -> DepGraph:
    """Build a host-certified dependency graph for ``repo_path``.

    See module docstring for the staged pipeline.  Returns the final immutable
    ``DepGraph``; certificates produced here are provisional (scratch-container
    scope) per design section 4.6.
    """
    # Stage 1 — static import scan -> Import + Test nodes.
    graph = scan_to_nodes(repo_path)
    graph = _restamp(graph, {n.id for n in graph.nodes}, _SCAN_CYCLE)

    # Stage 2 — import -> distribution mapping (declared manifests not parsed in
    # this plan; the curated table + identity fallback drive the roots).
    roots = package_roots(graph)

    # Stage 3 — real resolver (uv) -> Package nodes + requires edges.
    pkg_nodes, pkg_edges = resolve_closure(roots, executor, base_python=base_python)
    new_pkg_ids: set[str] = set()
    for node in pkg_nodes:
        graph = graph.with_node(node)
        new_pkg_ids.add(node.id)
    for edge in pkg_edges:
        graph = graph.with_edge(edge)
    graph = _restamp(graph, new_pkg_ids, _RESOLVER_CYCLE)

    # Stage 4 — probe: install once (build-time gaps -> Tool) then import-probe
    # (run-time gaps -> SystemLib).
    pre_probe_ids = {n.id for n in graph.nodes}
    graph = install_closure(graph, executor)
    graph = import_probe(graph, executor)
    probe_ids = {n.id for n in graph.nodes} - pre_probe_ids
    graph = _restamp(graph, probe_ids, _PROBE_CYCLE)

    # Stage 5 — host certification (layer-ordered; host flips state only).
    graph = certify_all(graph, executor, cycle=_CERTIFY_CYCLE)

    return graph
