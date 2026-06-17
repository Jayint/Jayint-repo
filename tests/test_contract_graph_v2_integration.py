# tests/test_contract_graph_v2_integration.py
"""Integration + coverage regression guard tests for the contract-graph v2 rewrite.

Also includes ported done-gate tests that were in the deleted test_orchestrator_contract_graph.py.
These guard against:
  1. libGL-style shared-library faults being promoted to system_library contracts.
  2. The v1 coverage regression: import-sweep failures must block goal satisfaction.
  3. Maintainer-driven done_flag must cause goal to be satisfied (not lost).
  4. collect-only output must NOT satisfy the goal (honest done-gate).
"""
from __future__ import annotations

from src.envstate.contracts.goals import GOAL_TESTS_PASS
from src.envstate.contracts.graph import ContractGraph, goal_ready
from src.envstate.contracts.projection import refresh_host_graph
from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.snapshot import EnvSnapshot
from src.envstate.world_model import (
    CommandRecord,
    Fact,
    PlannerDecision,
    Task,
    TaskReport,
    initial_map,
    merge_map,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _led(evs: list[ActionEvent]) -> ActionLedger:
    ledger = ActionLedger()
    for e in evs:
        ledger.append(e)
    return ledger


# ---------------------------------------------------------------------------
# Spec tests (as written in the plan)
# ---------------------------------------------------------------------------

def test_libgl_fault_localizes_to_system_library() -> None:
    """cv2 import fails on libGL -> a system_library contract is promoted under the graph."""
    m = initial_map(
        "python:3.11", "/repo", "python 3.11", "pip",
        ("tests/", "requirements.txt"),
        required=(Fact("opencv-python", ""),),
    )
    led = _led([ActionEvent(
        step=1,
        cmd="python -c 'import cv2'",
        rc=1,
        stdout="ImportError: libGL.so.1: cannot open shared object file",
        env_revision_before=0,
        env_revision_after=0,
        mutation_class=None,
    )])
    m = refresh_host_graph(m, led, snapshot=None, exec_readonly=None, current_revision=0)
    g = m.contract_graph
    assert any(n.data.get("kind") == "system_library" for n in g.contracts()), (
        "Expected a system_library Contract to be promoted from the libGL signature"
    )
    assert g.has_node(GOAL_TESTS_PASS), (
        "Backbone goal contract must be seeded by refresh_host_graph"
    )


def test_coverage_regression_guard_import_sweep_surfaces_missing_dep() -> None:
    """A dep imported only deep in tests is surfaced by the import sweep even with no failure
    signature yet. cv2 not importable -> GOAL_TESTS_PASS NOT satisfied (no false success)."""
    m = merge_map(
        initial_map(
            "python:3.11", "/repo", "python 3.11", "pip",
            ("tests/",),
            required=(Fact("opencv-python", ""),),
        ),
        import_results=(("cv2", False),),
    )
    from src.envstate.contracts.validators import host_satisfied_set
    from src.envstate.contracts.goals import seed_backbone

    nodes, edges = seed_backbone()
    g = ContractGraph(nodes=tuple(nodes), edges=tuple(edges))
    sat = host_satisfied_set(g, m, ledger_events=[])
    assert GOAL_TESTS_PASS not in sat, (
        "cv2 import fails -> GOAL_TESTS_PASS must NOT be in host_satisfied (no false success)"
    )


# ---------------------------------------------------------------------------
# Ported done-gate tests (from deleted test_orchestrator_contract_graph.py)
# These guard the honest done-gate: collect-only must never satisfy the goal.
# ---------------------------------------------------------------------------

class _DoneMaintainer:
    """Maintainer that unconditionally sets done_flag=True on update."""

    def update(self, m: object, report: object) -> object:
        return merge_map(m, done_flag=True)  # type: ignore[arg-type]


class _BuildAgentWithLedger:
    """BuildAgent that appends a real ActionEvent to the ledger, then returns a report."""

    def __init__(self, cmd: str, stdout: str, rc: int = 0) -> None:
        self._cmd = cmd
        self._stdout = stdout
        self._rc = rc

    def run(
        self,
        task: object,
        sandbox_execute: object,
        ledger: ActionLedger,
        step_offset: int = 0,
    ) -> TaskReport:
        ledger.append(ActionEvent(
            step=1,
            cmd=self._cmd,
            rc=self._rc,
            stdout=self._stdout,
            env_revision_before=0,
            env_revision_after=0,
        ))
        return TaskReport(
            "run tests",
            "done",
            (CommandRecord(self._cmd, self._rc, self._stdout),),
            "",
        )


def _initial_map() -> object:
    m = initial_map("img", "/r", "python 3.12", "pip", ("requirements.txt",))
    return merge_map(m, required=(Fact("torch", ""),), installed=(Fact("torch", "2.1.0"),))


def test_maintainer_done_flag_marks_goal_satisfied() -> None:
    """run_v1 with maintainer-driven done_flag -> GOAL_TESTS_PASS enters host_satisfied.

    Ported from test_orchestrator_contract_graph.py.  The v2 contract graph
    does not emit ContractStatusEvent objects; instead refresh_host_graph adds the
    goal id to host_satisfied when done_flag=True and a real test run was seen.
    """
    from src.envstate.orchestrator import run_v1

    class _Planner:
        def __init__(self, decisions: list[PlannerDecision]) -> None:
            self._q = list(decisions)

        def decide(self, m: object) -> PlannerDecision:
            return self._q.pop(0)

    task = Task("run tests", "pytest passes", "tests", ())
    planner = _Planner([
        PlannerDecision("task", task=task),
        PlannerDecision("giveup", reason="stop"),
    ])
    ledger = ActionLedger()

    final_map, reason = run_v1(
        planner,
        _BuildAgentWithLedger("python -m pytest -q", "5 passed in 0.1s"),
        _DoneMaintainer(),
        _initial_map(),
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
    assert GOAL_TESTS_PASS in final_map.host_satisfied, (
        "GOAL_TESTS_PASS must enter host_satisfied after maintainer sets done_flag "
        "and a real pytest execution (5 passed) is in the ledger"
    )
    assert goal_ready(g, final_map.host_satisfied), (
        "goal_ready must be True once GOAL_TESTS_PASS is satisfied"
    )


def test_collect_only_does_not_satisfy_goal() -> None:
    """rc=0 with zero tests passed (or --collect-only output) must NOT satisfy the goal.

    Ported from test_orchestrator_contract_graph.py.
    projection._verified_test_command_id requires >=1 passed in stdout so that a
    pure collection run cannot gate-crash the goal-satisfied certification.
    """
    from src.envstate.orchestrator import run_v1

    class _Planner:
        def __init__(self, decisions: list[PlannerDecision]) -> None:
            self._q = list(decisions)

        def decide(self, m: object) -> PlannerDecision:
            return self._q.pop(0)

    scenarios = [
        # (cmd, stdout) — rc=0 in both cases, but no "N passed" evidence
        ("python -m pytest -q", "collected 3 items\n"),
        ("pytest --collect-only -q", "collected 5 items\n"),
    ]
    for cmd, stdout in scenarios:
        task = Task("run tests", "pytest passes", "tests", ())
        planner = _Planner([
            PlannerDecision("task", task=task),
            PlannerDecision("giveup", reason="stop"),
        ])
        ledger = ActionLedger()

        final_map, _ = run_v1(
            planner,
            _BuildAgentWithLedger(cmd, stdout),
            _DoneMaintainer(),
            _initial_map(),
            ledger,
            sandbox_execute=lambda c: (True, stdout),
            max_cycles=2,
            probe=lambda: EnvSnapshot(installed=(Fact("torch", "2.1.0"),)),
            manifest=type("M", (), {"required": (Fact("torch", ""),), "build_system": "pip"})(),
            exec_readonly=lambda c: (0, ""),
            enable_contract_graph=True,
        )

        g = final_map.contract_graph
        assert GOAL_TESTS_PASS not in final_map.host_satisfied, (
            f"Goal must NOT be satisfied for cmd={cmd!r}, stdout={stdout!r}; "
            f"collect-only must not gate-crash host_satisfied"
        )
        assert not goal_ready(g, final_map.host_satisfied), (
            f"goal_ready must be False for cmd={cmd!r}"
        )
