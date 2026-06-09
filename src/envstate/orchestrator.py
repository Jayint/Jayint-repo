"""EnvState v1 orchestrator loop.

run_v1() is the new three-role loop (spec §4):
    initial_map → planner.decide → (done/giveup → break)
                → build_agent.run → maintainer.update
                → (done_flag → break)
                → repeat up to max_cycles

The legacy EnvStateOrchestrator class is kept below run_v1 unchanged
for Arms A/B/C back-compat.
"""
from __future__ import annotations

from typing import Any, Callable, Tuple

from src.envstate.ledger import ActionLedger
from src.envstate.world_model import (
    PlannerDecision,
    TaskReport,
    WorldModelMap,
)

# Sentinel type aliases (readable names only, no runtime cost).
Executor = Callable[[str], Tuple[bool, str]]

# Module-level constants (spec §8).
MAX_CYCLES: int = 12
LOCAL_BUDGET: int = 8

# Canonical collect-only command — referenced everywhere instead of inline strings.
COLLECT_ONLY_CMD: str = "pytest --collect-only -q --disable-warnings"


def run_v1(
    planner: Any,
    build_agent: Any,
    maintainer: Any,
    initial_world_map: WorldModelMap,
    ledger: ActionLedger,
    sandbox_execute: Callable[[str], tuple[bool, str]],
    max_cycles: int = MAX_CYCLES,
    local_budget: int = LOCAL_BUDGET,
    on_cycle: (
        Callable[[int, WorldModelMap, PlannerDecision, TaskReport | None], None] | None
    ) = None,
) -> tuple[WorldModelMap, str]:
    """Top-level v1 orchestrator loop.

    Returns ``(final_map, stop_reason)`` where ``stop_reason`` is one of:
      ``'done_flag'``     — maintainer set WorldModelMap.done_flag=True
      ``'planner_done'``  — planner emitted action='done'
      ``'planner_giveup'``— planner emitted action='giveup'
      ``'max_cycles'``    — loop ran for max_cycles without terminating

    The loop terminates the instant done_flag is set — it does NOT wait
    for the next planner.decide call (structural fix for the 'reached gate
    but never committed' failure mode).
    """
    current_map: WorldModelMap = initial_world_map

    for cycle in range(1, max_cycles + 1):
        # ── 1. Planner decides what to do next ──────────────────────────────
        decision: PlannerDecision = planner.decide(current_map)

        if decision.action == "done":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, "planner_done"

        if decision.action == "giveup":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, "planner_giveup"

        # ── 2. BuildAgent executes the task ──────────────────────────────────
        assert decision.task is not None, (
            f"PlannerDecision action='task' but .task is None (cycle {cycle})"
        )
        report: TaskReport = build_agent.run(
            decision.task,
            sandbox_execute,
            ledger,
            step_offset=(cycle - 1) * local_budget,
        )

        # ── 3. Maintainer updates the world model ────────────────────────────
        current_map = maintainer.update(current_map, report)

        # ── 4. Notify caller (optional telemetry hook) ───────────────────────
        if on_cycle is not None:
            on_cycle(cycle, current_map, decision, report)

        # ── 5. Hard-stop on done_flag — do NOT re-enter planner ──────────────
        if current_map.done_flag:
            return current_map, "done_flag"

    # Exhausted all cycles without termination.
    return current_map, "max_cycles"


# ---------------------------------------------------------------------------
# Legacy Arms A/B/C orchestrator (kept for back-compat — do NOT modify)
# ---------------------------------------------------------------------------

# EnvStateSnapshot (v0) removed with types.py; use Any for legacy type hints.

# observer(snapshot, task_spec, step, action, success, observation) -> new_snapshot
#   This is where the §6 loop closes: per executed action the host advances the
#   revision, runs the Maintainer, runs probe_requests, and certifies facts via the
#   ACL. It MUST return the new (immutable) snapshot, which the orchestrator threads.
Observer = Callable[..., Any]


class EnvStateOrchestrator:
    """Supervisor -> Worker -> (per-action) Observer loop (design §6).

    Collaborators are injected so this is unit-testable without Docker/LLM:
      supervisor.next_task(snapshot, ledger, budget) -> (task_spec|None, usage)
      worker.run_task(task_spec, step_fn) -> WorkerReport
      executor(action) -> (success, observation)
      observer(snapshot, task_spec, step, action, success, observation) -> new_snapshot
    """

    def __init__(
        self,
        supervisor,
        worker,
        snapshot: Any,
        ledger: ActionLedger,
        executor: Executor,
        observer: Observer,
        max_tasks: int = 20,
        on_usage=None,
        global_action_budget: int = None,
    ):
        self.supervisor = supervisor
        self.worker = worker
        self.snapshot = snapshot
        self.ledger = ledger
        self.executor = executor
        self.observer = observer
        self.max_tasks = max_tasks
        self.on_usage = on_usage
        self.global_action_budget = global_action_budget
        self._step = 0
        self._actions_executed = 0

    def _make_step_fn(self, task_spec):
        """Per-task execution closure handed to the Worker. Executes ONE action,
        then observes it into the EnvState snapshot (advance revision, Maintainer,
        probes, ACL certification). Threads the new snapshot back onto self."""
        def step_fn(action):
            self._step += 1
            self._actions_executed += 1
            success, observation = self.executor(action)
            self.snapshot = self.observer(
                self.snapshot, task_spec, self._step, action, success, observation
            )
            return success, observation
        return step_fn

    def run(self) -> dict[str, Any]:
        tasks_completed = 0
        reports = []
        stop_reason = "no_more_tasks"
        while True:
            if tasks_completed >= self.max_tasks:
                stop_reason = "max_tasks"
                break
            budget = {"steps_remaining": self.max_tasks - tasks_completed}
            task_spec, usage = self.supervisor.next_task(self.snapshot, self.ledger, budget)
            if self.on_usage is not None:
                self.on_usage(usage)
            if not task_spec:
                stop_reason = "no_more_tasks"
                break
            report = self.worker.run_task(task_spec, self._make_step_fn(task_spec))
            reports.append(report)
            tasks_completed += 1
            # Shared global executed-action cap (§3.5 / C2): behavior-preserving
            # when global_action_budget is None (Arm B default stays unbounded).
            if (self.global_action_budget is not None
                    and self._actions_executed >= self.global_action_budget):
                stop_reason = "global_action_budget"
                break
        return {
            "tasks_completed": tasks_completed,
            "stop_reason": stop_reason,
            "reports": reports,
            "final_revision": self.snapshot.revision,
        }
