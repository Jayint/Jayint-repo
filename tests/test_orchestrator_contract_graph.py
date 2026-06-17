from src.envstate.contracts.goals import evaluate_goal_readiness, GOAL_TESTS_RUN
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node
from src.envstate.ledger import ActionEvent, ActionLedger
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


# ---------------------------------------------------------------------------
# Bug 2 regression tests
# ---------------------------------------------------------------------------

class _DoneMaintainer:
    """Maintainer that unconditionally sets done_flag=True on update."""
    def update(self, m, report):
        return merge_map(m, done_flag=True)


class _BuildAgentWithLedger:
    """BuildAgent that appends a real pytest event to the ledger, then returns a report."""
    def __init__(self, cmd: str, stdout: str, rc: int = 0):
        self._cmd = cmd
        self._stdout = stdout
        self._rc = rc

    def run(self, task, sandbox_execute, ledger, step_offset=0):
        ledger.append(ActionEvent(
            step=1, cmd=self._cmd, rc=self._rc,
            stdout=self._stdout,
            env_revision_before=0, env_revision_after=0,
        ))
        return TaskReport("run tests", "done",
                          (CommandRecord(self._cmd, self._rc, self._stdout),), "")


def test_maintainer_done_flag_marks_goal_satisfied():
    """run_v1 with maintainer-driven done_flag → goal contract gets satisfied.

    Before the fix, _host_refresh() ran BEFORE maintainer.update(), so done_flag
    was still False during the refresh and the goal-satisfied event was never emitted.
    After the fix a second _host_refresh() fires AFTER maintainer.update().
    """
    task = Task("run tests", "pytest passes", "tests", ())
    planner = _Planner([PlannerDecision("task", task=task),
                        PlannerDecision("giveup", reason="stop")])
    ledger = ActionLedger()

    final_map, reason = run_v1(
        planner,
        _BuildAgentWithLedger("python -m pytest -q", "5 passed in 0.1s"),
        _DoneMaintainer(),
        _initial(),
        ledger,
        sandbox_execute=lambda c: (True, "5 passed in 0.1s"),
        max_cycles=2,
        probe=lambda: EnvSnapshot(installed=(Fact("torch", "2.1.0"),)),
        manifest=type("M", (), {"required": (Fact("torch", ""),), "build_system": "pip"})(),
        exec_readonly=lambda c: (0, ""),
        enable_contract_graph=True,
    )

    assert reason == "done_flag"
    assert final_map.done_flag is True

    g = final_map.contract_graph
    ev = g.latest_status(GOAL_TESTS_RUN)
    assert ev is not None, "Goal contract must have a status event after maintainer sets done_flag"
    assert ev.status == "satisfied", f"Expected 'satisfied', got {ev.status!r}"
    assert "cmd:001" in ev.evidence_ids, (
        f"Evidence must point to the CommandExecution node; got {ev.evidence_ids!r}"
    )
    assert evaluate_goal_readiness(g) is True


def test_collect_only_does_not_satisfy_goal():
    """rc=0 with zero tests passed (or --collect-only output) must NOT satisfy the goal.

    Hardening: projection._verified_test_command_id must require >=1 passed in
    stdout, not merely rc=0, so that a pure collection run cannot gate-crash the
    goal-satisfied event.
    """
    scenarios = [
        # (cmd, stdout) — rc=0 in both cases, but no "N passed" evidence
        ("python -m pytest -q", "collected 3 items\n"),
        ("pytest --collect-only -q", "collected 5 items\n"),
    ]
    for cmd, stdout in scenarios:
        task = Task("run tests", "pytest passes", "tests", ())
        planner = _Planner([PlannerDecision("task", task=task),
                            PlannerDecision("giveup", reason="stop")])
        ledger = ActionLedger()

        final_map, _ = run_v1(
            planner,
            _BuildAgentWithLedger(cmd, stdout),
            _DoneMaintainer(),
            _initial(),
            ledger,
            sandbox_execute=lambda c: (True, stdout),
            max_cycles=2,
            probe=lambda: EnvSnapshot(installed=(Fact("torch", "2.1.0"),)),
            manifest=type("M", (), {"required": (Fact("torch", ""),), "build_system": "pip"})(),
            exec_readonly=lambda c: (0, ""),
            enable_contract_graph=True,
        )

        g = final_map.contract_graph
        ev = g.latest_status(GOAL_TESTS_RUN)
        assert ev is None or ev.status != "satisfied", (
            f"Goal must NOT be satisfied for cmd={cmd!r}, stdout={stdout!r}; "
            f"got status_event={ev!r}"
        )
        assert evaluate_goal_readiness(g) is False, (
            f"evaluate_goal_readiness must be False for cmd={cmd!r}"
        )
