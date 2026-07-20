"""Stage C Task 1 — the config-lane classifier runs LIVE in real construction.

The shadow pass (``run_shadow_config_lane``) measured classify->cure->arbitrate and
DISCARDED the graph effect. This task rebinds only the classify step: the live pass
attaches the edge-less internal Module nodes and records the routing partition AS
GRAPH DATA on the Project node (``routing_internal`` tops, ``routing_deferred``
names) so Tasks 2-4 read it from the graph, not a side channel. It is additive:
Module nodes carry no install recipe and no edges, so the rendered ``setup.sh`` stays
byte-identical, and any exception from the live pass must never fail construction.
"""
import json

from conftest import FakeExecutor, SequencedFakeExecutor, make_result  # type: ignore

from graph.compile.build_script import render_build_script
from graph.contracts.executor import CommandResult
from graph.core.orchestrate import build_dep_graph
from graph.model import NodeType
from graph.python.pipeline import _apply_live_config_lane
from graph.python.read.scan import scan_to_nodes
from graph.python.route.classify import classify, probe_target_stdlib
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


def _seeded(tmp_path):
    """(repo, pre-lane graph with a Project node) — the pipeline state just before
    the live lane runs."""
    repo = _collision_repo(tmp_path)
    graph = scan_to_nodes(repo)
    graph = _add_project_node(graph, repo)
    return repo, graph


def test_live_lane_attaches_internal_module_node(tmp_path):
    repo, graph = _seeded(tmp_path)
    out = _apply_live_config_lane(graph, repo, _stdlib_exec(), declared=frozenset())
    node = out.get("module:mypkg")
    assert node is not None and node.type is NodeType.MODULE


def test_live_lane_stamps_routing_data_on_project_node(tmp_path):
    repo, graph = _seeded(tmp_path)
    out = _apply_live_config_lane(graph, repo, _stdlib_exec(), declared=frozenset())
    proj = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    assert "mypkg" in proj.data["routing_internal"]     # internal top recorded
    assert "items" in proj.data["routing_deferred"]     # deferred set preserved


def test_live_lane_routing_matches_classifier(tmp_path):
    repo, graph = _seeded(tmp_path)
    stdlib = probe_target_stdlib(_stdlib_exec())
    routing = classify(repo, target_stdlib=stdlib, declared=frozenset())
    out = _apply_live_config_lane(graph, repo, _stdlib_exec(), declared=frozenset())
    proj = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    assert set(proj.data["routing_internal"]) == {top for top, _ in routing.internal}
    assert set(proj.data["routing_deferred"]) == set(routing.deferred)


def test_live_lane_render_is_byte_identical(tmp_path):
    repo, graph = _seeded(tmp_path)
    before = render_build_script(graph)
    after = render_build_script(
        _apply_live_config_lane(graph, repo, _stdlib_exec(), declared=frozenset())
    )
    assert after == before      # Module nodes carry no recipe; project data is inert to render


def test_live_lane_is_fail_open(tmp_path):
    class _Boom:
        def run(self, *_a, **_k):
            raise RuntimeError("probe blew up")

    repo, graph = _seeded(tmp_path)
    out = _apply_live_config_lane(graph, repo, _Boom(), declared=frozenset())
    assert out is graph         # any exception is swallowed; construction is untouched


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
    assert graph.get("module:mypkg") is not None
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert "mypkg" in proj.data["routing_internal"]
