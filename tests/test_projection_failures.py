from src.envstate.contracts import projection
from src.envstate.ledger import ActionEvent
from src.envstate.world_model import OpenProblem


def test_open_problem_nodes_1to1():
    ops = (OpenProblem("ModuleNotFoundError: torch", "torch missing", "deps", False),)
    nodes = projection.project_open_problems(ops)
    n = nodes[0]
    assert n.id == "openproblem:modulenotfounderror-torch" and n.type == "OpenProblem"
    assert n.data["layer"] == "deps" and n.data["out_of_scope"] is False


def test_failures_from_failing_commands_with_observed_in():
    events = [
        ActionEvent(step=7, cmd="python -c 'import torch'", rc=1, stdout="ModuleNotFoundError: torch"),
        ActionEvent(step=8, cmd="pip install torch", rc=0, stdout="ok"),
    ]
    nodes, edges = projection.project_failures(events)
    assert len(nodes) == 1 and nodes[0].type == "Failure"
    assert nodes[0].data["command_id"] == "cmd:007"
    assert any(e.type == "observed_in" and e.target == "cmd:007" for e in edges)


def test_failure_summary_is_redacted():
    events = [ActionEvent(step=1, cmd="x", rc=1, stdout="boom TOKEN=ghp_aaaabbbbccccdddd")]
    nodes, _ = projection.project_failures(events)
    assert "ghp_aaaabbbbccccdddd" not in nodes[0].data["summary"]
