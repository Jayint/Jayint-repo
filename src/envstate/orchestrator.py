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

from src.envstate.contracts import ids as _cids
from src.envstate.contracts.apply import apply_patch as _apply_patch
from src.envstate.contracts.goals import evaluate_goal_readiness as _graph_ready
from src.envstate.contracts.projection import refresh_host_graph as _refresh_graph
from src.envstate.contracts.transitions import commit_transition_patch, executed_as_patch
from src.envstate.contracts.validation import validate_patch as _validate_patch
from src.envstate.ledger import ActionLedger, make_action_event as _make_event
from src.envstate.maintainer import _verified_test_run_passed as _gate_passed
from src.envstate.world_model import (
    CommandRecord,
    PlannerDecision,
    TaskReport,
    WorldModelMap,
    apply_deterministic,
    merge_map,
)

# Sentinel type aliases (readable names only, no runtime cost).
Executor = Callable[[str], Tuple[bool, str]]

# Module-level constants (spec §8).
MAX_CYCLES: int = 12
LOCAL_BUDGET: int = 8

# Canonical collect-only command — kept for back-compat (some tests/modules import it).
COLLECT_ONLY_CMD: str = "pytest --collect-only -q --disable-warnings"

# Canonical execution-verify command used by the Phase-1 execution gate.
# The gate requires a bare interpreter (no venv wrapper) and >=1 passed test.
VERIFY_TEST_CMD: str = "python -m pytest -q"


def run_v1(planner, build_agent, maintainer, initial_world_map, ledger, sandbox_execute,
           max_cycles=MAX_CYCLES, local_budget=LOCAL_BUDGET, on_cycle=None, *,
           probe=None, manifest=None, exec_readonly=None, enable_contract_graph=False):
    """Top-level v1 orchestrator loop.

    Returns ``(final_map, stop_reason)`` where ``stop_reason`` is one of:
      ``'done_flag'``     — maintainer set WorldModelMap.done_flag=True
      ``'planner_done'``  — planner emitted action='done'
      ``'planner_giveup'``— planner emitted action='giveup'
      ``'max_cycles'``    — loop ran for max_cycles without terminating

    The loop terminates the instant done_flag is set — it does NOT wait
    for the next planner.decide call (structural fix for the 'reached gate
    but never committed' failure mode).

    New optional kwargs (both default off — every existing test and the A1 arm
    are byte-for-byte unchanged):
      exec_readonly     — callable(cmd) -> (rc: int, out: str) for read-only probes
      enable_contract_graph — when True, runs the per-cycle host graph refresh and
                              enforces the advisory-done readiness gate (Phase 5).
    """
    current_map: WorldModelMap = initial_world_map

    def _current_revision():
        evs = ledger.events()
        return evs[-1].env_revision_after if evs else 0

    def _host_refresh():
        nonlocal current_map
        if not enable_contract_graph:
            return
        from src.envstate.snapshot import EnvSnapshot
        snap = probe() if probe is not None else EnvSnapshot()
        current_map = _refresh_graph(current_map, ledger, snap,
                                     exec_readonly, _current_revision())

    if probe is not None and manifest is not None:
        current_map = apply_deterministic(current_map, probe(), manifest)
    _host_refresh()

    for cycle in range(1, max_cycles + 1):
        # ── 1. Planner decides what to do next ──────────────────────────────
        decision: PlannerDecision = planner.decide(current_map)

        if decision.action == "done":
            if not enable_contract_graph:
                # original behavior: flag off means return immediately (byte-for-byte unchanged)
                if on_cycle is not None:
                    on_cycle(cycle, current_map, decision, None)
                return current_map, "planner_done"
            # advisory: run active verification, fold facts, confirm host gate + graph readiness
            ok, out = sandbox_execute(VERIFY_TEST_CMD)
            rev = _current_revision()
            ledger.append(_make_event(step=cycle * local_budget + 1, cmd=VERIFY_TEST_CMD, success=ok,
                                      stdout=(out or "")[-1500:], env_revision_before=rev,
                                      env_revision_after=rev, mutation_class=None, container_id=""))
            if probe is not None and manifest is not None:
                current_map = apply_deterministic(current_map, probe(), manifest)
            verify_report = TaskReport("final verification", "done" if ok else "blocked",
                                       (CommandRecord(VERIFY_TEST_CMD, 0 if ok else 1, (out or "")[-1500:]),),
                                       "planner requested done")
            done = current_map.done_flag or _gate_passed(verify_report)
            current_map = merge_map(current_map, done_flag=done)
            _host_refresh()  # marks goal satisfied when done + deps satisfied
            ready = (not enable_contract_graph) or _graph_ready(current_map.contract_graph)
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, verify_report)
            if current_map.done_flag and ready:
                return current_map, "planner_done"
            continue  # advisory done not confirmed; keep working (bounded by max_cycles)

        if decision.action == "giveup":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, "planner_giveup"

        # ── 2. BuildAgent executes the task ──────────────────────────────────
        assert decision.task is not None, (
            f"PlannerDecision action='task' but .task is None (cycle {cycle})"
        )
        task = decision.task

        # commit the planner's transition into the graph before execution
        if enable_contract_graph and task.transition_proposal is not None:
            patch = commit_transition_patch(current_map.contract_graph, task.transition_proposal, task.target_node_ids)
            if not patch.is_empty() and not _validate_patch(current_map.contract_graph, patch, scope="host"):
                current_map = merge_map(current_map, contract_graph=_apply_patch(current_map.contract_graph, patch))

        len_before = len(ledger.events())
        report: TaskReport = build_agent.run(
            task,
            sandbox_execute,
            ledger,
            step_offset=(cycle - 1) * local_budget,
        )
        new_steps = [ev.step for ev in ledger.events()[len_before:]]

        # ── 2b. Deterministic facts (read-only probe, OFF the ledger) ─────────
        if probe is not None and manifest is not None:
            current_map = apply_deterministic(current_map, probe(), manifest)
        _host_refresh()  # creates CommandExecution nodes for the new commands

        # link the committed transition to the commands it produced
        if enable_contract_graph and task.transition_proposal is not None and new_steps:
            tid = _cids.transition_id(task.transition_proposal.kind,
                                      _cids.slug(task.transition_proposal.target) or task.transition_proposal.target)
            ep = executed_as_patch(current_map.contract_graph, tid, new_steps)
            if not ep.is_empty() and not _validate_patch(current_map.contract_graph, ep, scope="host"):
                current_map = merge_map(current_map, contract_graph=_apply_patch(current_map.contract_graph, ep))

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
