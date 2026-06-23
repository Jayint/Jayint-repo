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

import os
import re
import tomllib
from dataclasses import replace

from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.executor import Executor, LocalSubprocessExecutor
from python_deps.depgraph.ids import TEST_NODE_ID, project_id
from python_deps.depgraph.probe import import_probe, install_closure
from python_deps.depgraph.resolve import (
    DEFAULT_TARGET_PLATFORM,
    link_imports_to_packages,
    resolve_closure,
)
from python_deps.depgraph.roots import select_roots
from python_deps.depgraph.scan import scan_to_nodes
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from python_deps.depgraph.seed import seed_predicted_native
from python_deps.evidence import collect_python_dependency_evidence
from python_deps.import_mapping import normalize_package_name

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


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _project_name(repo_path: str) -> str:
    """Project name from ``[project].name`` in pyproject.toml, else dir basename."""
    pyproject = os.path.join(repo_path, "pyproject.toml")
    try:
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        name = (data.get("project") or {}).get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return os.path.basename(repo_path.rstrip("/\\")) or "project"


def _add_project_node(graph: DepGraph, repo_path: str) -> DepGraph:
    """Add a Project hub node and connect declared direct deps to it.

    The repo under test is otherwise only reachable through the Test->Import
    chain, so its declared direct dependencies have no shared parent (e.g.
    ``certifi`` had no incoming Package->Package edge).  This node makes "what
    does the project directly require" a single explorable subtree:

    * ``Test --requires--> Project``
    * ``Project --requires--> <runtime declared dep Package>``  (kind=dependency)
    * ``Test --requires--> <test/optional declared dep Package>`` (kind=optional)

    Runtime vs test classification reuses ``evidence`` (kind ``dependency`` vs
    ``optional_dependency``); no new parsing.  Transitive deps still hang off
    their parents, and Import->Package reconciliation is unchanged.
    """
    name = _project_name(repo_path)
    proj_id = project_id(name)
    graph = graph.with_node(
        Node(
            id=proj_id,
            type=NodeType.PROJECT,
            name=name,
            layer=Layer.PIP,
            discovered_by=DiscoveredBy.STATIC_SCAN,
            state=State.UNKNOWN,
            provenance=os.path.join(repo_path, "pyproject.toml"),
        )
    )
    graph = graph.with_edge(
        Edge(src=TEST_NODE_ID, dst=proj_id, relation=EdgeType.REQUIRES, origin="project")
    )

    canon_to_pkg = {
        _canon(n.name): n.id for n in graph.nodes if n.type is NodeType.PACKAGE
    }
    evidence = collect_python_dependency_evidence(repo_path)
    for req in evidence.declared_dependencies:
        if getattr(req, "kind", "dependency") == "constraint":
            continue
        pkg_id = canon_to_pkg.get(_canon(normalize_package_name(req.name)))
        if pkg_id is None:
            continue
        # runtime deps hang off the Project; test/optional deps off the Test goal.
        src = (
            TEST_NODE_ID
            if getattr(req, "kind", "dependency") == "optional_dependency"
            else proj_id
        )
        graph = graph.with_edge(
            Edge(src=src, dst=pkg_id, relation=EdgeType.REQUIRES, origin="project")
        )
    return graph


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

    # Stage 3a' — Project hub: connect declared direct deps to a Project node so
    # the package layer is fully connected (runtime deps off Project, test deps
    # off the Test goal).
    graph = _add_project_node(graph, repo_path)

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
