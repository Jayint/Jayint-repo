from __future__ import annotations
from typing import Any, Callable, Tuple

from src.envstate.ledger import ActionLedger
from src.envstate.types import EnvStateSnapshot

# executor(action) -> (success, observation)   — raw execution (Sandbox.execute)
Executor = Callable[[str], Tuple[bool, str]]
# observer(snapshot, task_spec, step, action, success, observation) -> new_snapshot
#   This is where the §6 loop closes: per executed action the host advances the
#   revision, runs the Maintainer, runs probe_requests, and certifies facts via the
#   ACL. It MUST return the new (immutable) snapshot, which the orchestrator threads.
Observer = Callable[..., EnvStateSnapshot]


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
        snapshot: EnvStateSnapshot,
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
