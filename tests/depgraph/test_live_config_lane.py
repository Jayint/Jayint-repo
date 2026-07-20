"""THE FLIP — the config lane runs LIVE in two halves around Phase A.

``_route_config_lane`` runs BEFORE the Phase-A fixpoint: it attaches the edge-less
internal ``Module`` nodes and stamps ``routed_provider='module'`` on first-party
Import nodes, and returns the ``LaneRouting`` (so the caller threads
``routing.deferred`` into the fixpoint's lane-aware filter). ``_finalize_config_lane``
runs AFTER ``_add_project_node``: it wires the goal spine (``project->module->import``,
the flat ``Test->Import`` hub's replacement) and records the routing partition AS
GRAPH DATA on the Project node. Both halves are additive to render: Module/Import
nodes and the spine edges between them carry no recipe, so the emitted ``setup.sh``
stays byte-identical, and any exception must never fail construction.
"""
import json

from conftest import FakeExecutor, SequencedFakeExecutor, make_result  # type: ignore

from graph.compile.build_script import render_build_script
from graph.contracts.executor import CommandResult
from graph.core.orchestrate import build_dep_graph
from graph.model import NodeType, EdgeType, import_id, project_id
from graph.python.pipeline import _route_config_lane, _finalize_config_lane
from graph.python.read.scan import scan_to_nodes
from graph.python.route.classify import classify, probe_target_stdlib, module_id
from graph.python.skeleton import _add_project_node


def _collision_repo(tmp_path):
    """Three lanes at once: a local top-level (``mypkg`` -> internal), a clear
    external (``requests``), and a stem collision (``items`` -> deferred). Mirrors
    ``test_classify``'s collision fixture so all of internal/external/deferred fire."""
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "app.py").write_text(
        "import requests\nimport items\nfrom mypkg import helpers\n"
    )
    (tmp_path / "mypkg" / "helpers.py").write_text("")
    (tmp_path / "mypkg" / "tutorial001").mkdir()
    (tmp_path / "mypkg" / "tutorial001" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "tutorial001" / "items.py").write_text("")
    return str(tmp_path)


def _stdlib_exec():
    """A FakeExecutor whose one-shot stdlib probe answers with a real JSON list."""
    return FakeExecutor(
        responses={"stdlib_module_names": make_result(stdout=json.dumps(["os", "sys"]))}
    )


def _routed(tmp_path):
    """(repo, routed pre-Phase-A graph, routing) — the pipeline state after the
    classify pass but before ``_add_project_node``."""
    repo = _collision_repo(tmp_path)
    graph = scan_to_nodes(repo)
    graph, routing = _route_config_lane(graph, repo, _stdlib_exec(), declared=frozenset())
    return repo, graph, routing


def _finalized(tmp_path):
    """(repo, graph after route -> add_project -> finalize, routing) — the full
    live-lane graph effect in construction order."""
    repo, graph, routing = _routed(tmp_path)
    graph = _add_project_node(graph, repo)
    graph = _finalize_config_lane(graph, routing)
    return repo, graph, routing


def test_route_lane_attaches_internal_module_node(tmp_path):
    _repo, graph, _routing = _routed(tmp_path)
    node = graph.get(module_id("mypkg"))
    assert node is not None and node.type is NodeType.MODULE


def test_route_lane_returns_deferred_for_the_fixpoint_filter(tmp_path):
    _repo, _graph, routing = _routed(tmp_path)
    # ``items`` is the collision name the caller threads into the fixpoint so it
    # never reaches the dist-guesser.
    assert "items" in routing.deferred


def test_finalize_stamps_routing_data_on_project_node(tmp_path):
    _repo, graph, _routing = _finalized(tmp_path)
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert "mypkg" in proj.data["routing_internal"]     # internal top recorded
    assert "items" in proj.data["routing_deferred"]     # deferred set preserved


def test_finalize_wires_the_project_module_spine_edge(tmp_path):
    _repo, graph, _routing = _finalized(tmp_path)
    pid = project_id(next(n.name for n in graph.nodes if n.type is NodeType.PROJECT))
    req = {(e.src, e.dst, e.origin) for e in graph.edges if e.relation is EdgeType.REQUIRES}
    assert (pid, module_id("mypkg"), "contains") in req


def test_finalize_wires_the_module_import_spine_edge(tmp_path):
    _repo, graph, _routing = _finalized(tmp_path)
    req = {(e.src, e.dst, e.origin) for e in graph.edges if e.relation is EdgeType.REQUIRES}
    # mypkg/app.py imports requests -> module(mypkg) --imports--> import(requests).
    assert (module_id("mypkg"), import_id("requests"), "imports") in req


def test_route_matches_classifier(tmp_path):
    _repo, _graph, routing = _routed(tmp_path)
    stdlib = probe_target_stdlib(_stdlib_exec())
    ref = classify(str(tmp_path), target_stdlib=stdlib, declared=frozenset())
    assert {top for top, _ in routing.internal} == {top for top, _ in ref.internal}
    assert set(routing.deferred) == set(ref.deferred)


def test_flip_render_is_byte_identical(tmp_path):
    """The whole live-lane graph effect adds ZERO rendered lines: Module/Import
    nodes and the spine edges between them carry no recipe, and the project routing
    data is inert to the renderer."""
    repo = _collision_repo(tmp_path)
    # Baseline: the same scan -> _add_project_node graph WITHOUT the config-lane
    # modules/spine/routing data.
    before = render_build_script(_add_project_node(scan_to_nodes(repo), repo))
    # Flipped: scan -> route -> add_project -> finalize (modules + spine + data).
    graph, routing = _route_config_lane(scan_to_nodes(repo), repo, _stdlib_exec(), declared=frozenset())
    graph = _finalize_config_lane(_add_project_node(graph, repo), routing)
    assert render_build_script(graph) == before


def test_route_lane_is_fail_open(tmp_path):
    class _Boom:
        def run(self, *_a, **_k):
            raise RuntimeError("probe blew up")

    repo = _collision_repo(tmp_path)
    graph = scan_to_nodes(repo)
    out, routing = _route_config_lane(graph, repo, _Boom(), declared=frozenset())
    assert out is graph and routing is None   # any exception swallowed; graph untouched


def test_build_dep_graph_lands_module_nodes_and_routing(tmp_path):
    """The rebind, end-to-end: ``build_dep_graph`` returns a graph carrying the
    internal Module node AND the routing partition on the Project node — proving the
    live lane runs inside real construction, not just as an isolated helper."""
    def _r(rc=0, stdout="", stderr=""):
        return CommandResult(command="", returncode=rc, stdout=stdout, stderr=stderr)

    repo = _collision_repo(tmp_path)
    ex = SequencedFakeExecutor(
        responses={"stdlib_module_names": [_r(0, stdout=json.dumps(["os", "sys"]))]},
        default=_r(0),
    )
    graph = build_dep_graph(str(repo), ex, host_executor=ex)
    assert graph.get(module_id("mypkg")) is not None
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert "mypkg" in proj.data["routing_internal"]
