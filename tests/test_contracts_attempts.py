# tests/test_contracts_attempts.py
from src.envstate.contracts.attempts import commit_attempt, attempt_node
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node


def _g():
    return ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),))


def test_commit_attempt_adds_node_and_addresses_edges():
    step = type("S", (), {"id": "step:1", "kind": "python_install",
                          "command": "pip install opencv-python",
                          "target_node_ids": ("contract:python_import:cv2",)})()
    patch = commit_attempt(_g(), step, proposed_by="planner")
    assert any(n.type == "Attempt" for n in patch.add_contracts) or any(
        n.type == "Attempt" for n in patch.add_blockers) or patch.add_edges
    g2 = __import__("src.envstate.contracts.apply", fromlist=["apply_patch"]).apply_patch(_g(), patch)
    a = next(n for n in g2.nodes if n.type == "Attempt")
    assert a.data["proposed_by"] == "planner" and a.data["outcome"] == "pending"
    assert any(e.type == "addresses" and e.target == "contract:python_import:cv2" for e in g2.edges)
