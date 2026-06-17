from src.envstate.contracts.nodes import Node, Edge
from src.envstate.contracts.graph import (
    ContractGraph, project_status, depends_on_closure, frontier_by_layer, goal_ready,
)

def _g():
    nodes = (
        Node("contract:goal:repo_tests_pass", "Contract",
             {"level": "goal", "required": True, "layer": "tests", "kind": "tests_pass"}),
        Node("contract:goal:repo_imports_work", "Contract",
             {"level": "goal", "required": True, "layer": "deps", "kind": "imports_work"}),
        Node("contract:python_import:cv2", "Contract",
             {"level": "atomic", "layer": "deps", "kind": "python_import", "subject": "cv2"}),
        Node("blocker:importerror-libgl", "Blocker",
             {"signature": "ImportError: libGL.so.1", "kind": "missing_system_library",
              "layer": "system", "active": True, "summary": "libGL missing"}),
    )
    edges = (
        Edge("contract:goal:repo_tests_pass", "depends_on", "contract:goal:repo_imports_work"),
        Edge("contract:goal:repo_imports_work", "depends_on", "contract:python_import:cv2"),
        Edge("blocker:importerror-libgl", "violates", "contract:python_import:cv2"),
    )
    return ContractGraph(nodes=nodes, edges=edges)

def test_project_status_violated_from_active_blocker():
    g = _g()
    assert project_status(g, "contract:python_import:cv2", frozenset()) == "violated"

def test_project_status_satisfied_from_host_set():
    g = _g()
    assert project_status(g, "contract:python_import:cv2",
                          frozenset({"contract:python_import:cv2"})) == "satisfied"

def test_project_status_unknown_when_no_evidence():
    g = _g()
    assert project_status(g, "contract:goal:repo_imports_work", frozenset()) == "unknown"

def test_depends_on_closure_reaches_atomic():
    g = _g()
    cl = depends_on_closure(g, "contract:goal:repo_tests_pass")
    assert "contract:python_import:cv2" in cl

def test_frontier_by_layer_groups_unsatisfied():
    g = _g()
    fr = frontier_by_layer(g, frozenset())
    assert "contract:python_import:cv2" in fr["deps"]

def test_goal_ready_false_until_all_satisfied():
    g = _g()
    assert goal_ready(g, frozenset()) is False
    everything = frozenset(n.id for n in g.nodes if n.type == "Contract")
    assert goal_ready(g, everything) is True

def test_diagnostic_notes_roundtrip_capped():
    g = ContractGraph(diagnostic_notes=tuple(str(i) for i in range(15)))
    assert ContractGraph.from_dict(g.to_dict()).diagnostic_notes == g.diagnostic_notes
