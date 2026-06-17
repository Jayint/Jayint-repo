# tests/test_planner_contract_graph.py
import json

from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node
from src.envstate.planner import parse_planner_decision, render_planning_view
from src.envstate.world_model import initial_map, merge_map


def test_render_includes_graph_when_non_empty():
    # v2: render_graph_for_planner outputs "## Repair Map" / "## Repair Frontier" sections,
    # not "Contract Graph".  The node id must still appear in the output.
    m = merge_map(
        initial_map("img", "/r", "py", "pip", ()),
        contract_graph=ContractGraph(nodes=(Node("contract:python_package_importable:torch", "Contract",
                                                  {"level": "atomic", "description": "torch importable"}),)),
    )
    view = render_planning_view(m, {"cycles_remaining": 5})
    assert "Repair Map" in view and "contract:python_package_importable:torch" in view


def test_render_omits_graph_when_empty():
    m = initial_map("img", "/r", "py", "pip", ())
    assert "Contract Graph" not in render_planning_view(m, {"cycles_remaining": 5})


def test_parse_task_with_transition_proposal_and_targets():
    text = json.dumps({
        "action": "task", "goal": "install torch", "done_when": "pytest runs", "layer": "deps",
        "facts": ["torch missing"],
        "target_node_ids": ["contract:python_package_importable:torch"],
        "transition_proposal": {"kind": "install_python_package", "target": "torch",
                                "intent": "install torch", "command_templates": ["pip install torch"]},
    })
    d = parse_planner_decision(text)
    assert d.action == "task"
    assert d.task.target_node_ids == ("contract:python_package_importable:torch",)
    assert d.task.transition_proposal.kind == "install_python_package"


def test_transition_proposal_without_target_is_rejected():
    text = json.dumps({"action": "task", "goal": "g", "done_when": "d", "layer": "deps", "facts": [],
                       "transition_proposal": {"kind": "x", "target": "y", "intent": "z"}})
    assert parse_planner_decision(text) is None  # ungrounded transition forbidden


def test_parse_advisory_done():
    text = json.dumps({"action": "done", "satisfied_goal_contract_ids": ["contract:goal:repo_tests_run"],
                       "rationale": "verified"})
    d = parse_planner_decision(text)
    assert d.action == "done" and d.satisfied_goal_contract_ids == ("contract:goal:repo_tests_run",)


def test_bare_done_without_goal_ids_rejected():
    assert parse_planner_decision(json.dumps({"action": "done"})) is None
