# tests/test_orchestrator_v1.py
"""Unit tests for the run_v3 orchestrator loop (src/envstate/orchestrator.py).

The legacy three-role planner-driven loop was retired in Phase 0 of the src/ stage-refactor;
what remains here is the run_v3 graph-scheduler smoke coverage (clean-graph giveup, real-replay
planner_done, the collect-only anti-hollow guard) plus the shared module-constant / termination
guards. All collaborators are faked — no LLM calls, no Docker containers.

(Filename kept for Phase 0's no-rename constraint; the file moves to orchestrate/ in Phase 2.)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Put <repo>/src on sys.path so python_deps.* resolves when the graph scheduler's lazy import fires.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.envstate.ledger import ActionLedger
from src.envstate.world_model import (
    TaskReport,
    WorldModelMap,
    initial_map,
    merge_map,
)
from src.sandbox import InstallResult

from src.envstate.orchestrator import run_v3  # noqa: E402
from src.envstate.deterministic_maintainer import DeterministicMaintainer  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

# run_v3 is fresh-replay-only (Phase 4): reset_to_base/run_install_script are
# mandatory. These v3 smoke tests use dep_graph=None (_dep_emit_phase
# short-circuits before ever touching them), so no-op fakes just satisfy the
# guard.
def _noop_reset_to_base() -> None:
    pass


def _noop_run_install_script(script: str) -> InstallResult:
    return InstallResult(rc=0, failing_command=None, lineno=None, stderr="")


def _base_map() -> WorldModelMap:
    return initial_map(
        base_image="python:3.11-slim",
        workdir="/app",
        language="python 3.11",
        build_system="pip",
        repo_layout=("src/", "tests/", "pyproject.toml"),
    )


def _world_map_with_clean_dep_graph() -> WorldModelMap:
    """WorldModelMap with dep_graph=None: scheduler frontier is empty.

    next_decision(None, run_tests=passing) → 'done' immediately.
    """
    return _base_map()


class _NoopBuildAgent:
    """build_agent stub — never called when next_decision returns 'done' immediately."""
    def run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget=None):
        raise AssertionError("build_agent.run must not be called when scheduler yields done")

    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        raise AssertionError("build_agent.run_recipe must not be called here")


class _NoopMaintainer:
    """Passes world_map through unchanged — never called on the done path."""
    def update(self, world_map, report):
        return world_map


class _PassiveBuildAgent:
    """build_agent stub that can be called but returns a blocked report with no commands.

    Used in anti-hollow tests where next_decision dispatches a discover task
    (because _run_tests_verified rejects hollow output) but the agent produces
    no evidence of a real test pass, so the maintainer done-gate stays False.
    """
    def run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget=None):
        return TaskReport("discover", "blocked", (), "no commands ran")

    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        return TaskReport("recipe", "blocked", (), "no commands ran")


# ---------------------------------------------------------------------------
# Shared module-constant + termination guards (loop-agnostic)
# ---------------------------------------------------------------------------

def test_default_max_cycles_constant_is_12():
    """MAX_CYCLES module constant must equal 12 per spec §8."""
    from src.envstate import orchestrator
    assert orchestrator.MAX_CYCLES == 12


def test_collect_only_cmd_constant_present():
    """COLLECT_ONLY_CMD module constant must be defined in orchestrator."""
    from src.envstate import orchestrator
    assert hasattr(orchestrator, "COLLECT_ONLY_CMD")
    assert "--collect-only" in orchestrator.COLLECT_ONLY_CMD


def test_termination_reason_maps_to_legacy_strings():
    from src.envstate.orchestrator import TerminationReason, _to_stop_reason
    assert _to_stop_reason(TerminationReason.DONE) == "planner_done"
    assert _to_stop_reason(TerminationReason.DONE_FLAG) == "done_flag"
    assert _to_stop_reason(TerminationReason.GIVEUP_RESIDUAL) == "planner_giveup"
    assert _to_stop_reason(TerminationReason.GIVEUP_BUDGET) == "planner_giveup"
    assert _to_stop_reason(TerminationReason.GIVEUP_STUCK) == "planner_giveup"
    assert _to_stop_reason(TerminationReason.MAX_CYCLES) == "max_cycles"


# ---------------------------------------------------------------------------
# v3 smoke: graph-scheduler reaches planner_done on a clean graph
# ---------------------------------------------------------------------------

def test_v3_graph_scheduler_giveup_replay_when_dep_graph_none_never_replayed(monkeypatch):
    """Phase 7 (installability gate binding): with dep_graph=None, `_dep_emit_phase`
    short-circuits before ever calling `_binding_emit` (`current_map.dep_graph is
    None` guard, R3(c)) — so no fresh-from-base replay ever runs and
    `_last_replay_result` stays None. Even though the scheduler's frontier is
    trivially empty and tests pass (`next_decision` would say 'done'), run_v3 must
    NOT report 'planner_done' on an environment that was never actually replayed
    from base — there is no proof it builds. This closes the same hollow-success
    hole as `test_v3_collect_only_does_not_finalize_as_done` below: a passing
    scheduler decision alone is not sufficient, the binding replay proof is
    required too.

    Renamed from `test_v3_graph_scheduler_reaches_planner_done_on_clean_graph`
    (pre-Phase-7 characterization, which asserted the opposite — 'planner_done' —
    precisely because no replay was ever required for "done"; see
    `test_v3_graph_scheduler_reaches_planner_done_with_real_replay` below for the
    equivalent scenario WITH a real replay, which still reaches 'planner_done').
    """
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "0")
    # clean dep-graph (dep_graph=None → empty frontier) + tests pass, but
    # _dep_emit_phase never runs (no dep_graph) → no replay ever happens.
    world = _world_map_with_clean_dep_graph()

    def passing_exec(cmd):
        return (True, "1 passed")

    final_map, stop = run_v3(
        _NoopBuildAgent(), _NoopMaintainer(),
        world, ActionLedger(), passing_exec, max_cycles=3,
        enable_dep_emit=True, enable_runtime_feedback=True,
        reset_to_base=_noop_reset_to_base, run_install_script=_noop_run_install_script,
    )
    assert stop == "planner_giveup"


def test_v3_graph_scheduler_reaches_planner_done_with_real_replay(monkeypatch):
    """Companion to the giveup case above: an EMPTY (but not None) dep_graph +
    a real exec_readonly DOES drive `_dep_emit_phase` through `_binding_emit`
    (reset_to_base + run_install_script), so `_last_replay_result` is a real
    rc=0 InstallResult by the time the scheduler decides 'done' — the binding
    replay guard (Phase 7) is satisfied and the run terminates 'planner_done'.
    """
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "0")
    from graph.schema import DepGraph
    world = merge_map(_base_map(), dep_graph=DepGraph())

    def passing_exec(cmd):
        return (True, "1 passed")

    def noop_exec_readonly(cmd):
        return (0, "")

    final_map, stop = run_v3(
        _NoopBuildAgent(), _NoopMaintainer(),
        world, ActionLedger(), passing_exec, max_cycles=3,
        exec_readonly=noop_exec_readonly,
        enable_dep_emit=True, enable_runtime_feedback=True,
        reset_to_base=_noop_reset_to_base, run_install_script=_noop_run_install_script,
    )
    assert stop == "planner_done"


# ---------------------------------------------------------------------------
# v3 anti-hollow: collect-only must not set done_flag
# ---------------------------------------------------------------------------

def test_v3_collect_only_does_not_finalize_as_done():
    """A pytest --collect-only rc=0 must NOT set done_flag / terminate run_v3 as done.

    The v3 done-gate requires a REAL verified test pass (_verified_test_run_passed).
    Characterization of the anti-hollow guarantee:

    Flow:
      collect_only_exec always returns (True, "collected 5 items") — rc=0, but the
      output contains no execution evidence ("N passed" or "[100%]"). Two guards
      block the hollow path:

      1. SCHEDULER gate (_run_tests_verified): runs sandbox_execute(VERIFY_TEST_CMD)
         → (True, "collected 5 items").  _gate_passed rejects this because
         _shows_execution("collected 5 items") is False (no "N passed" / "Ran N tests")
         and _shows_pytest_completion is False (no "[100%]").  run_tests() returns
         False, so next_decision returns a discover task instead of 'done'.

      2. MAINTAINER gate (DeterministicMaintainer v3_only=True): the discover-task
         build_agent returns a TaskReport with no commands.  _verified_test_run_passed
         finds no rc=0 test-command record → done_flag stays False.

    Proof it can fail: replace collect_only_exec with one that returns
    (True, "3 passed") for VERIFY_TEST_CMD — _run_tests_verified returns True,
    next_decision returns 'done', and the loop exits immediately as 'planner_done'.
    (The _PassiveBuildAgent never gets called on the done path, so no done_flag is
    set via the maintainer either, but the scheduler gate IS breached — that is the
    hollow-success anti-pattern this test closes off.)
    """
    world = _world_map_with_clean_dep_graph()

    def collect_only_exec(cmd):
        # Any command (including VERIFY_TEST_CMD) returns rc=0 but only hollow
        # collect-only output — no actual test execution evidence.
        return (True, "collected 5 items")

    final_map, stop = run_v3(
        _PassiveBuildAgent(), DeterministicMaintainer(v3_only=True),
        world, ActionLedger(), collect_only_exec, max_cycles=2,
        reset_to_base=_noop_reset_to_base, run_install_script=_noop_run_install_script,
    )
    assert final_map.done_flag is not True  # collect-only must not finalize
    assert stop not in ("planner_done", "done_flag")  # hollow scheduler gate is also caught
