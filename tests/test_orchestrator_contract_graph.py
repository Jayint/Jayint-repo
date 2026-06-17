from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node
from src.envstate.ledger import ActionLedger
from src.envstate.orchestrator import run_v1
from src.envstate.snapshot import EnvSnapshot
from src.envstate.world_model import (
    CommandRecord, Fact, PlannerDecision, Task, TaskReport, TransitionProposal,
    initial_map, merge_map,
)


class _Planner:
    def __init__(self, decisions): self._q = list(decisions)
    def decide(self, m): return self._q.pop(0)


class _BuildAgent:
    def __init__(self, reports): self._q = list(reports)
    def run(self, task, sandbox_execute, ledger, step_offset=0): return self._q.pop(0)


class _Maintainer:
    """Pass-through maintainer: returns the map unchanged (graph already host-refreshed)."""
    def update(self, m, report): return m


def _initial():
    m = initial_map("img", "/r", "python 3.12", "pip", ("requirements.txt",))
    return merge_map(m, required=(Fact("torch", ""),), installed=(Fact("torch", "2.1.0"),))


def test_graph_is_refreshed_and_transition_committed():
    proposal = TransitionProposal("install_python_package", "torch", "install torch", ("pip install torch",))
    task = Task("install", "pytest runs", "deps", (),
                target_node_ids=("contract:python_package_importable:torch",), transition_proposal=proposal)
    planner = _Planner([PlannerDecision("task", task=task), PlannerDecision("giveup", reason="stop")])
    ba = _BuildAgent([TaskReport("install", "done", (CommandRecord("pip install torch", 0, "ok"),), "")])
    ledger = ActionLedger()
    final_map, reason = run_v1(
        planner, ba, _Maintainer(), _initial(), ledger, sandbox_execute=lambda c: (True, "ok"),
        max_cycles=2, probe=lambda: EnvSnapshot(installed=(Fact("torch", "2.1.0"),)),
        manifest=type("M", (), {"required": (Fact("torch", ""),), "build_system": "pip"})(),
        exec_readonly=lambda c: (0, ""), enable_contract_graph=True,
    )
    g = final_map.contract_graph
    assert g.node("contract:goal:repo_tests_run") is not None         # host template seeded
    assert g.node("transition:install_python_package:torch") is not None  # transition committed
    assert any(e.type == "targets" for e in g.edges)


def test_advisory_done_confirmed_when_ready():
    # planner emits done; orchestrator runs verification (sandbox returns a passing pytest),
    # host marks goal satisfied -> loop stops with planner_done.
    planner = _Planner([PlannerDecision("done", satisfied_goal_contract_ids=("contract:goal:repo_tests_run",))])
    ledger = ActionLedger()

    def sandbox(cmd):
        return (True, "collected 3 items\n3 passed in 0.1s")  # VERIFY_TEST_CMD passes

    final_map, reason = run_v1(
        planner, _BuildAgent([]), _Maintainer(), _initial(), ledger, sandbox_execute=sandbox,
        max_cycles=2, probe=lambda: EnvSnapshot(installed=(Fact("torch", "2.1.0"),)),
        manifest=type("M", (), {"required": (Fact("torch", ""),), "build_system": "pip"})(),
        exec_readonly=lambda c: (0, ""), enable_contract_graph=True,
    )
    assert reason == "planner_done"
    assert final_map.done_flag is True
