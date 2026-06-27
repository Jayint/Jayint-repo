# tests/test_orchestrator_v1.py
"""Unit tests for run_v1 orchestrator loop (src/envstate/orchestrator.py).

All collaborators are faked — no LLM calls, no Docker containers.
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass
from typing import Callable

from src.envstate.ledger import ActionLedger
from src.envstate.world_model import (
    CommandRecord,
    Fact,
    OpenProblem,
    PlannerDecision,
    Task,
    TaskReport,
    WorldModelMap,
    initial_map,
    merge_map,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_map() -> WorldModelMap:
    return initial_map(
        base_image="python:3.11-slim",
        workdir="/app",
        language="python 3.11",
        build_system="pip",
        repo_layout=("src/", "tests/", "pyproject.toml"),
    )


def _task() -> Task:
    return Task(
        goal="install deps",
        done_when="pip install exits 0",
        layer="deps",
        facts=(),
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakePlanner:
    """Emits PlannerDecision objects from a pre-loaded queue."""

    def __init__(self, decisions: list[PlannerDecision]) -> None:
        self._queue = list(decisions)

    def decide(self, current_map: WorldModelMap) -> PlannerDecision:
        assert self._queue, "FakePlanner.decide called more times than expected"
        return self._queue.pop(0)


class FakeBuildAgent:
    """Returns TaskReport objects from a pre-loaded queue."""

    def __init__(self, reports: list[TaskReport]) -> None:
        self._queue = list(reports)

    def run(
        self,
        task: Task,
        sandbox_execute: Callable[[str], tuple[bool, str]],
        ledger: ActionLedger,
        step_offset: int = 0,
        check=None,
        budget: int | None = None,
    ) -> TaskReport:
        assert self._queue, "FakeBuildAgent.run called more times than expected"
        return self._queue.pop(0)


class FakeMaintainer:
    """Applies a series of map transformations from a pre-loaded queue."""

    def __init__(self, maps: list[WorldModelMap]) -> None:
        self._queue = list(maps)

    def update(self, current_map: WorldModelMap, report: TaskReport) -> WorldModelMap:
        assert self._queue, "FakeMaintainer.update called more times than expected"
        return self._queue.pop(0)


def _noop_sandbox(cmd: str) -> tuple[bool, str]:
    return True, "ok"


# ---------------------------------------------------------------------------
# Import target (will fail until orchestrator.py is rewritten)
# ---------------------------------------------------------------------------

from src.envstate.orchestrator import run_v1  # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunV1DoneFlagTerminates:
    """done_flag=True in the map breaks the loop immediately."""

    def test_done_flag_after_one_cycle_returns_done_flag_reason(self):
        world_map = _base_map()
        done_map = merge_map(world_map, done_flag=True)

        planner = FakePlanner([
            PlannerDecision(action="task", task=_task()),
        ])
        build_agent = FakeBuildAgent([
            TaskReport(
                task_goal="install deps",
                status="done",
                commands=(CommandRecord(cmd="pip install .", rc=0, output="ok"),),
                learning="deps installed",
            ),
        ])
        maintainer = FakeMaintainer([done_map])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
        )

        assert stop_reason == "done_flag"
        assert final_map.done_flag is True

    def test_done_flag_does_not_call_planner_again(self):
        """After done_flag is set the loop must break before the next planner.decide."""
        world_map = _base_map()
        done_map = merge_map(world_map, done_flag=True)

        # Planner only has ONE decision; a second call would raise AssertionError.
        planner = FakePlanner([
            PlannerDecision(action="task", task=_task()),
        ])
        build_agent = FakeBuildAgent([
            TaskReport(task_goal="g", status="done", commands=(), learning=""),
        ])
        maintainer = FakeMaintainer([done_map])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
        )

        assert stop_reason == "done_flag"


class TestRunV1PlannerDone:
    """planner.decide returning action='done' should terminate cleanly."""

    def test_planner_done_returns_planner_done_reason(self):
        world_map = _base_map()

        planner = FakePlanner([
            PlannerDecision(action="done", reason="all layers verified"),
        ])
        # build_agent and maintainer should NOT be called
        build_agent = FakeBuildAgent([])
        maintainer = FakeMaintainer([])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
        )

        assert stop_reason == "planner_done"
        assert final_map is world_map  # map unchanged when planner says done

    def test_planner_giveup_returns_planner_giveup_reason(self):
        world_map = _base_map()

        planner = FakePlanner([
            PlannerDecision(action="giveup", reason="irrecoverable conflict"),
        ])
        build_agent = FakeBuildAgent([])
        maintainer = FakeMaintainer([])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
        )

        assert stop_reason == "planner_giveup"


class TestRunV1MaxCycles:
    """Exhausting max_cycles without done_flag returns 'max_cycles'."""

    def test_max_cycles_exhaustion_stop_reason(self):
        world_map = _base_map()
        # Each cycle: planner says "task", agent returns blocked, maintainer returns same map.
        n = 3
        planner = FakePlanner([
            PlannerDecision(action="task", task=_task()) for _ in range(n)
        ])
        build_agent = FakeBuildAgent([
            TaskReport(task_goal="install deps", status="blocked", commands=(), learning="still blocked")
            for _ in range(n)
        ])
        maintainer = FakeMaintainer([world_map for _ in range(n)])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=n,
        )

        assert stop_reason == "max_cycles"
        assert final_map.done_flag is False

    def test_default_max_cycles_constant_is_12(self):
        """MAX_CYCLES module constant must equal 12 per spec §8."""
        from src.envstate import orchestrator
        assert orchestrator.MAX_CYCLES == 12

    def test_collect_only_cmd_constant_present(self):
        """COLLECT_ONLY_CMD module constant must be defined in orchestrator."""
        from src.envstate import orchestrator
        assert hasattr(orchestrator, "COLLECT_ONLY_CMD")
        assert "--collect-only" in orchestrator.COLLECT_ONLY_CMD


class TestRunV1TwoCycleRun:
    """Full 2-cycle run: cycle 1 task+blocked, cycle 2 task+done_flag."""

    def test_two_cycle_run_succeeds_on_second_cycle(self):
        world_map = _base_map()
        map_after_cycle1 = merge_map(world_map, notes=("deps partially installed",))
        map_after_cycle2 = merge_map(map_after_cycle1, done_flag=True)

        planner = FakePlanner([
            PlannerDecision(action="task", task=_task()),  # cycle 1
            PlannerDecision(action="task", task=_task()),  # cycle 2
        ])
        build_agent = FakeBuildAgent([
            TaskReport(task_goal="install deps", status="blocked", commands=(), learning="network error"),
            TaskReport(
                task_goal="install deps",
                status="done",
                commands=(CommandRecord(cmd="pip install .", rc=0, output="Successfully installed"),),
                learning="deps installed",
            ),
        ])
        maintainer = FakeMaintainer([map_after_cycle1, map_after_cycle2])

        on_cycle_calls: list[tuple[int, str]] = []

        def on_cycle(cycle_num, current_map, decision, report):
            on_cycle_calls.append((cycle_num, decision.action))

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
            on_cycle=on_cycle,
        )

        assert stop_reason == "done_flag"
        assert final_map.done_flag is True
        assert len(on_cycle_calls) == 2
        assert on_cycle_calls[0] == (1, "task")
        assert on_cycle_calls[1] == (2, "task")


class TestRunV1ReturnType:
    """run_v1 always returns a (WorldModelMap, str) tuple."""

    def test_return_is_tuple_of_map_and_str(self):
        world_map = _base_map()
        planner = FakePlanner([PlannerDecision(action="done", reason="ok")])
        result = run_v1(
            planner=planner,
            build_agent=FakeBuildAgent([]),
            maintainer=FakeMaintainer([]),
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        final_map, stop_reason = result
        assert isinstance(final_map, WorldModelMap)
        assert isinstance(stop_reason, str)
