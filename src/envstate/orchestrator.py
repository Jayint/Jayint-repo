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

import dataclasses
from typing import Any, Callable, Tuple

from src.envstate.contracts import attempts as _attempts
from src.envstate.contracts.apply import apply_patch as _apply_patch
from src.envstate.contracts.graph import goal_ready as _graph_ready
from src.envstate.contracts.patch import GraphPatch
from src.envstate.contracts.projection import refresh_host_graph as _refresh_graph
from src.envstate.contracts.validation import validate_patch as _validate_patch
from src.envstate.contracts.validators import derive_attempt_outcome as _derive_outcome
from src.envstate.ledger import ActionLedger, make_action_event as _make_event
from src.envstate.maintainer import _verified_test_run_passed as _gate_passed
from src.envstate.world_model import (
    CommandRecord,
    PlannerDecision,
    RecipePatch,
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


def run_v1(
    planner,
    build_agent,
    maintainer,
    initial_world_map: WorldModelMap,
    ledger: ActionLedger,
    sandbox_execute: Executor,
    max_cycles: int = MAX_CYCLES,
    local_budget: int = LOCAL_BUDGET,
    on_cycle=None,
    *,
    probe=None,
    manifest=None,
    exec_readonly=None,
    enable_contract_graph: bool = False,
    enable_dep_emit: bool = False,
    enable_runtime_feedback: bool = False,
    enable_graph_scheduler: bool = False,
    graph_scheduler_attempt_cap: int = 3,
):
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
      exec_readonly         — callable(cmd) -> (rc: int, out: str) for read-only probes
      enable_contract_graph — when True, runs the per-cycle host graph refresh and
                              enforces the advisory-done readiness gate (Phase 5).
    """
    current_map: WorldModelMap = initial_world_map
    # Monotonic step counter for ledger offsets (avoids cycle-based aliasing).
    global_step: int = 0
    # Runtime-feedback high-water mark: index into ledger.events() up to which we
    # have already ingested. Starts at 0 so the first ingest captures every event
    # not yet ingested — including any written before the loop began (e.g. a
    # pre-seeded failure). _runtime_ingest_phase advances it.
    _rt_mark: int = 0
    _residual_giveup: str | None = None   # set when a residual is non-env / divergent (spec §3 G3, §8)
    _handed: dict[str, int] = {}       # graph-scheduler: per-obligation hand-out counts
    _sched_stuck: int = 0              # consecutive discover cycles with no new obligations
    _sched_last_nodes: int = -1        # dep-graph node count at the last discover cycle
    _repaired_ids: set[str] = set()   # nodes given a host-first repair this run (one each)
    _repair_turns: int = max_cycles      # LLM-repair budget (NOT mechanical installs)
    _budget_exhausted: bool = False

    def _current_revision() -> int:
        evs = ledger.events()
        return evs[-1].env_revision_after if evs else 0

    def _host_refresh() -> None:
        nonlocal current_map
        if not enable_contract_graph:
            return
        from src.envstate.snapshot import EnvSnapshot
        snap = probe() if probe is not None else EnvSnapshot()
        current_map = _refresh_graph(
            current_map, ledger, snap, exec_readonly, _current_revision()
        )

    def _dep_emit_phase(cycle: int) -> None:
        nonlocal current_map, global_step, _repaired_ids, _repair_turns, _budget_exhausted
        if not enable_dep_emit or current_map.dep_graph is None:
            return
        if exec_readonly is None:                      # R3(c): no certify path -> no emit
            return
        from python_deps.depgraph.schema import NodeType, State
        from src.envstate.world_model import Fact
        from src.envstate.depgraph_live import certify_refresh, emit_drain, ensure_python_shim
        from python_deps.depgraph.advise import render_depgraph_planner
        # Make a bare `python` resolve to python3 before any check runs, else a
        # python3-only base fails every `python -m pip show` and nothing certifies.
        ensure_python_shim(sandbox_execute)
        graph = certify_refresh(current_map.dep_graph, exec_readonly, cycle)
        # Certify still runs (populating the frontier); emit_drain runs as a
        # deterministic prefix regardless of whether the graph scheduler is active.
        # The scheduler then receives only the irreducible residual (non-emittable MISSING
        # nodes). global_step is advanced here only if emit_drain consumed steps, so
        # LLM turns are NOT counted.
        graph, _reports, steps = emit_drain(
            graph, build_agent, sandbox_execute, ledger, exec_readonly,
            step_offset=global_step, cycle=cycle,
        )
        if steps:
            global_step += steps
        # Host-first repair of reciped nodes the batch wave could not certify (the
        # broken-bridge fix). Gated to the graph-scheduler arm so the off path and
        # legacy arms stay byte-identical.
        if enable_graph_scheduler:
            from src.envstate.depgraph_live import repair_failed_nodes
            graph, repair_steps, _repaired_n = repair_failed_nodes(
                graph, build_agent, sandbox_execute, ledger, exec_readonly,
                step_offset=global_step, cycle=cycle, repaired_ids=_repaired_ids,
            )
            if repair_steps:
                global_step += repair_steps
            if _repaired_n:
                _repair_turns -= _repaired_n
                if _repair_turns <= 0:
                    _budget_exhausted = True
        # Fold emit-certified packages into installed so the synthesizer's closure
        # recipe includes them even when the planner finalizes immediately.
        sat = tuple(Fact(n.name, n.version or "") for n in graph.nodes
                    if n.type is NodeType.PACKAGE and n.state is State.SATISFIED)
        have = {f.name for f in current_map.installed}
        installed = current_map.installed + tuple(f for f in sat if f.name not in have)
        # Under the graph scheduler the planner is bypassed, so the planner-facing
        # dep_advisory render is dead work; reuse the prior advisory and skip the
        # compute. The dep_graph + installed merge is UNCHANGED — the scheduler reads
        # current_map.dep_graph, so it must still be merged every cycle.
        advisory = current_map.dep_advisory if enable_graph_scheduler else render_depgraph_planner(graph)
        current_map = merge_map(
            current_map, dep_graph=graph, dep_advisory=advisory, installed=installed,
        )

    def _run_tests_verified() -> bool:
        """Run VERIFY_TEST_CMD and return True only if the full anti-hollow-success gate passes.

        Replaces the bare ``sandbox_execute(VERIFY_TEST_CMD)[0]`` rc=0 check used by
        the v1gs done-gate so the graph scheduler cannot finalize on a hollow pytest
        exit-code (e.g. zero tests collected, venv-wrapped run, all-skipped).
        """
        ok, out = sandbox_execute(VERIFY_TEST_CMD)
        verify_report = TaskReport(
            "sched-verify",
            "done" if ok else "blocked",
            (CommandRecord(VERIFY_TEST_CMD, 0 if ok else 1, (out or "")[-2000:]),),
            "scheduler test probe",
        )
        return _gate_passed(verify_report)

    def _runtime_ingest_phase() -> None:
        # Gated: enable_graph_scheduler enables the divergence + out-of-scope give-up path
        # (spec §3 G3, §8); _residual_giveup is written only inside that gate.
        nonlocal current_map, _rt_mark, _residual_giveup
        if not enable_runtime_feedback or current_map.dep_graph is None:
            return
        try:
            from python_deps.depgraph.advise import render_depgraph_planner
            from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
            from python_deps.depgraph.runtime_classify import classify_observation
            events = ledger.events()
            new_events = events[_rt_mark:]
            obs = [(e.cmd, e.stdout) for e in new_events]
            if not obs:
                return
            pre_graph = current_map.dep_graph
            _out_of_scope: list[tuple[str, str]] = []   # non-env diagnoses; Task 6 reads this

            # Deterministic regex tier always runs; the temp-0 LLM tier is appended
            # ONLY under the graph-scheduler arm and only when a client exists
            # (spec §6 cascade). Off this gate the call is byte-identical to before.
            classifiers = (classify_observation,)
            if enable_graph_scheduler and getattr(build_agent, "client", None) is not None:
                from src.envstate.llm_classifier import make_llm_classifier
                from src.envstate.llm_response import complete_with_retry
                from src.envstate.jsonutil import extract_json_object

                def _complete(messages):
                    text, _usage, _resp = complete_with_retry(
                        build_agent.client, build_agent.model, messages,
                        accept=lambda t: extract_json_object(t) is not None,
                        temperature=0, max_attempts=2,
                    )
                    return text

                _llm = make_llm_classifier(
                    _complete,
                    note_out_of_scope=lambda c, r: _out_of_scope.append((c, r)),
                )
                # Bound LLM fan-out: spec §6 is "LLM only on the misses", but a cycle
                # with 30 failed events would fire 30 synchronous temp-0 calls. Classify
                # each UNIQUE error tail at most once, capped per cycle.
                _seen_errs: set[str] = set()
                _MAX_LLM_PER_CYCLE = 5

                def _bounded_llm(cmd, out):
                    key = (out or "")[-500:]
                    if key in _seen_errs or len(_seen_errs) >= _MAX_LLM_PER_CYCLE:
                        return None
                    _seen_errs.add(key)
                    return _llm(cmd, out)

                classifiers = (classify_observation, _bounded_llm)

            new_graph, found = ingest_runtime_failures(pre_graph, obs, classifiers=classifiers)
            # Advance the mark ONLY after a successful ingest call returns — so an
            # exception mid-ingest does not permanently drop those events (they are
            # re-read next cycle). (spec §11; C4 event-loss fix.)
            _rt_mark = len(events)
            # Honest give-up (spec §3 G3 / §8): a residual that maps to an already-
            # SATISFIED node (divergence) or that the LLM judged non-env, when the
            # deterministic frontier is clean, is not fixable by adding nodes. Record
            # the reason; the main loop returns planner_giveup. done_flag is NEVER set.
            if enable_graph_scheduler:
                import logging
                from python_deps.depgraph.runtime_ingest import diverged_node_ids
                from python_deps.depgraph.emit import partition
                diverged = diverged_node_ids(pre_graph, found)
                if (diverged or _out_of_scope) and not partition(new_graph).emittable:
                    _residual_giveup = (
                        f"graph-scheduler: residual not an environment obligation "
                        f"(diverged={list(diverged)}, out_of_scope={len(_out_of_scope)})"
                    )
                    logging.getLogger(__name__).info(
                        "residual-handler give-up: %s", _residual_giveup
                    )
            if not found:
                return
            advisory = render_depgraph_planner(new_graph)
            current_map = merge_map(current_map, dep_graph=new_graph, dep_advisory=advisory)
        except Exception as exc:  # noqa: BLE001 — must never break the run (spec §11)
            import logging
            logging.getLogger(__name__).warning(
                "runtime_ingest_phase: exception suppressed: %s", exc
            )

    if probe is not None and manifest is not None:
        current_map = apply_deterministic(current_map, probe(), manifest)
    _host_refresh()

    for cycle in range(1, max_cycles + 1):
        # ── 0. Graph-first: certify + emit the certified closure ────────────
        _dep_emit_phase(cycle)
        if enable_graph_scheduler and _budget_exhausted:
            # graph-scheduler: LLM turn budget exhausted — bounded repair gave up
            return current_map, "planner_giveup"
        # ── 0b. Runtime feedback: ingest ledger failures from the PREVIOUS cycle
        #        into the live dep-graph. Runs once per cycle before any branch so
        #        it fires regardless of which branch returns (I2 done-path fix).
        _runtime_ingest_phase()
        if enable_graph_scheduler and _residual_giveup is not None:
            return current_map, "planner_giveup"
        # ── 1. Decide what to do next ───────────────────────────────────────
        if enable_graph_scheduler:
            from src.envstate.graph_scheduler import next_decision
            decision, chosen = next_decision(
                current_map.dep_graph,
                _run_tests_verified,
                handed=_handed,
                attempt_cap=graph_scheduler_attempt_cap,
            )
            if chosen is not None:
                _handed[chosen] = _handed.get(chosen, 0) + 1
                _sched_stuck = 0
            elif decision.action == "task":          # discover task → sufficiency-stuck
                n_nodes = len(current_map.dep_graph.nodes) if current_map.dep_graph else 0
                _sched_stuck = _sched_stuck + 1 if n_nodes <= _sched_last_nodes else 0
                _sched_last_nodes = n_nodes
                if _sched_stuck >= 2:                  # consecutive discover rounds revealed no new obligations (bounded anyway by max_cycles)
                    decision = PlannerDecision(
                        action="giveup",
                        reason="graph-scheduler: no new obligations after 2 sufficiency rounds",
                    )
        else:
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
            ready = (not enable_contract_graph) or _graph_ready(
                current_map.contract_graph, current_map.host_satisfied
            )
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, verify_report)
            if current_map.done_flag and ready:
                return current_map, "planner_done"
            continue  # advisory done not confirmed; keep working (bounded by max_cycles)

        if decision.action == "giveup":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, "planner_giveup"

        # ── 2. Recipe patch branch ───────────────────────────────────────────
        # The planner's prompt is recipe-based, so it emits apply_recipe_patch
        # regardless of the contract-graph flag.  Recipe EXECUTION therefore runs
        # in both arms; only the GRAPH BOOKKEEPING (attempt tracking, outcome
        # write-back, host graph render/blockers) is gated on enable_contract_graph
        # (BUG-11).  With the graph off this is just: run the commands, refresh
        # deterministic facts, run the maintainer, and honor the honest done-gate.
        if decision.action == "apply_recipe_patch":
            recipe: RecipePatch | None = decision.recipe_patch
            if recipe is None or not recipe.steps:
                # Empty recipe — nothing to execute; let maintainer decide.
                empty_report = TaskReport("recipe", "done", (), "empty recipe")
                current_map = maintainer.update(current_map, empty_report)
                _host_refresh()  # self-no-ops when the graph is off
                if on_cycle is not None:
                    on_cycle(cycle, current_map, decision, empty_report)
                if current_map.done_flag:
                    return current_map, "done_flag"
                continue

            # Commit one Attempt node per step BEFORE execution (graph only).
            attempt_ids: list[str] = []
            if enable_contract_graph:
                graph = current_map.contract_graph
                for step in recipe.steps:
                    attempt_patch = _attempts.commit_attempt(graph, step, proposed_by="planner")
                    errs = _validate_patch(graph, attempt_patch, scope="host")
                    if not errs:
                        graph = _apply_patch(graph, attempt_patch)
                    # Derive the attempt id from the step (mirrors attempt_node logic).
                    node = _attempts.attempt_node(step, "planner")
                    attempt_ids.append(node.id)
                current_map = merge_map(current_map, contract_graph=graph)

            # Execute the whole recipe as a single unified run.
            report: TaskReport = build_agent.run_recipe(
                recipe,
                sandbox_execute,
                ledger,
                step_offset=global_step,
            )
            global_step += len(report.commands)

            # Host refresh ONCE after the recipe (before the maintainer).
            if probe is not None and manifest is not None:
                current_map = apply_deterministic(current_map, probe(), manifest)
            _host_refresh()

            # Derive each Attempt's outcome from its OWN step (BUG-10).  run_recipe
            # reports how many leading steps completed successfully via
            # report.completed_steps; outcomes must be attributed PER-STEP, not by
            # a single recipe-level failure flag (which mislabeled successful
            # install steps as 'failed' the instant any later step blocked).
            # Graph-only bookkeeping — skipped entirely when the graph is off.
            if enable_contract_graph:
                completed = report.completed_steps
                updated_nodes: list = []
                for i, attempt_id in enumerate(attempt_ids):
                    if completed is None:
                        # Older/fake reports without per-step counts: preserve the
                        # original recipe-level behavior for every attempt.
                        step_failed = report.status != "done"
                    elif i < completed:
                        step_failed = False           # this step's command succeeded
                    elif i == completed and report.status != "done":
                        step_failed = True            # the step that failed/blocked
                    else:
                        # i > completed: this step never ran — leave its committed
                        # 'pending' outcome untouched.
                        continue
                    outcome = _derive_outcome(
                        current_map.contract_graph,
                        attempt_id,
                        current_map.host_satisfied,
                        step_failed,
                    )
                    node = current_map.contract_graph.node(attempt_id)
                    if node is not None:
                        new_data = {**node.data, "outcome": outcome}
                        updated_nodes.append(dataclasses.replace(node, data=new_data))

                # Write outcomes back to the graph via a host update_attempts patch.
                if updated_nodes:
                    outcomes_patch = GraphPatch(update_attempts=tuple(updated_nodes))
                    current_map = merge_map(
                        current_map,
                        contract_graph=_apply_patch(current_map.contract_graph, outcomes_patch),
                    )

            # ── 3. Maintainer updates the world model ─────────────────────
            current_map = maintainer.update(current_map, report)

            # ── 3b. Post-update graph refresh ─────────────────────────────
            _host_refresh()

            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, report)

            # ── 4. Hard-stop on done_flag — do NOT check mid-recipe ───────
            if current_map.done_flag:
                return current_map, "done_flag"

            continue

        # ── 3. Legacy build-agent flow (action == "task") ────────────────────
        assert decision.task is not None, (
            f"PlannerDecision action='task' but .task is None (cycle {cycle})"
        )
        task = decision.task

        report = build_agent.run(
            task,
            sandbox_execute,
            ledger,
            step_offset=global_step,
            check=(task.done_when if enable_graph_scheduler else None),
            budget=(5 if enable_graph_scheduler else LOCAL_BUDGET),
        )
        if enable_graph_scheduler:
            _repair_turns -= 1
            if _repair_turns <= 0:
                # graph-scheduler: LLM turn budget exhausted — bounded repair gave up
                return current_map, "planner_giveup"
        # Scheduler mode: a passing host check can return zero commands; floor the
        # advance at 1 so the ledger step offset never aliases across cycles. Off the
        # flag this is byte-identical to the legacy `+= len(report.commands)`.
        global_step += (
            max(len(report.commands), 1) if enable_graph_scheduler else len(report.commands)
        )

        # ── 3b. Deterministic facts (read-only probe, OFF the ledger) ────────
        if probe is not None and manifest is not None:
            current_map = apply_deterministic(current_map, probe(), manifest)
        _host_refresh()  # refresh graph after commands

        # ── 4. Maintainer updates the world model ────────────────────────────
        current_map = maintainer.update(current_map, report)

        # ── 4b. Post-update graph refresh ────────────────────────────────────
        # maintainer.update() may have just set done_flag=True.  Refresh the
        # host graph NOW so refresh_host_graph sees done_flag=True and emits
        # the goal-satisfied ContractStatusEvent.  The pre-update call above
        # already created CommandExecution nodes; this call is idempotent on
        # those nodes and only adds the goal-satisfaction event.
        _host_refresh()

        # ── 5. Notify caller (optional telemetry hook) ───────────────────────
        if on_cycle is not None:
            on_cycle(cycle, current_map, decision, report)

        # ── 6. Hard-stop on done_flag — do NOT re-enter planner ──────────────
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
