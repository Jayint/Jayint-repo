from src.envstate.contracts.render import render_graph_for_planner, serialize_graph_for_maintainer
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge


def _g() -> ContractGraph:
    return ContractGraph(
        nodes=(Node("contract:goal:repo_tests_pass", "Contract",
                    {"level": "goal", "required": True, "layer": "tests", "kind": "tests_pass"}),
               Node("contract:python_import:cv2", "Contract",
                    {"level": "atomic", "layer": "deps", "kind": "python_import", "subject": "cv2"}),
               Node("blocker:libgl", "Blocker", {"signature": "ImportError: libGL.so.1",
                    "active": True, "root_or_downstream": "root", "summary": "libGL missing", "layer": "system"})),
        edges=(Edge("contract:goal:repo_tests_pass", "depends_on", "contract:python_import:cv2"),
               Edge("blocker:libgl", "violates", "contract:python_import:cv2")))


def test_planner_render_has_three_sections_and_root_blocker():
    out = render_graph_for_planner(_g(), frozenset())
    assert "Repair Map" in out and "Repair Frontier" in out
    assert "blocker:libgl" in out and "violated" in out


def test_maintainer_serializer_has_no_status_events():
    d = serialize_graph_for_maintainer(_g())
    assert set(d) == {"contracts", "blockers", "attempts", "edges"}
