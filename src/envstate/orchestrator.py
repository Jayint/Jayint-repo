"""EnvState orchestrator loops.

run_v1() — three-role planner-driven loop (spec §4):
    initial_map → planner.decide → (done/giveup → break)
                → build_agent.run → maintainer.update
                → (done_flag → break)
                → repeat up to max_cycles

run_v3() — graph-scheduler loop (no planner):
    initial_map → dep-graph scheduler (next_decision) → build_agent.run
                → maintainer.update → (done_flag/giveup/budget → break)
                → repeat up to max_cycles
"""
from __future__ import annotations

import enum
import os
from typing import Any, Callable, Tuple

from src.envstate._loop_common import host_refresh_facts
from src.envstate.constants import VERIFY_TEST_CMD  # re-exported for back-compat
from src.envstate.ledger import ActionLedger
from src.envstate.done_gate import _verified_test_run_passed as _gate_passed
from src.envstate.repair_loop import run_structured_repair
from src.envstate.world_model import (
    CommandRecord,
    PlannerDecision,
    RecipePatch,
    TaskReport,
    WorldModelMap,
    merge_map,
)

# Sentinel type aliases (readable names only, no runtime cost).
Executor = Callable[[str], Tuple[bool, str]]

# Module-level constants (spec §8).
MAX_CYCLES: int = 12
LOCAL_BUDGET: int = 8
# Per-obligation LLM turn budget for the v3 graph-scheduler task branch. Each
# scheduled obligation is host-checked, so this bounds how many ReAct turns the
# BuildAgent gets to satisfy one node before the scheduler moves on.
SCHEDULER_TASK_BUDGET: int = 5

# Canonical collect-only command — kept for back-compat (some tests/modules import it).
COLLECT_ONLY_CMD: str = "pytest --collect-only -q --disable-warnings"

# VERIFY_TEST_CMD now lives in src.envstate.constants and is imported above; it is
# re-exported from this module so existing ``from ...orchestrator import VERIFY_TEST_CMD``
# call sites keep working.


# ---------------------------------------------------------------------------
# Internal termination taxonomy (v3 / graph-scheduler arm)
# ---------------------------------------------------------------------------

class TerminationReason(enum.Enum):
    """Typed termination cause for the v1/v3 loops.

    Maps to the external stop_reason strings via ``_to_stop_reason``. Introduced
    so *internal* code (logging, future extension) can distinguish the three
    logically-distinct giveup causes. The external contract still collapses all
    three to ``"planner_giveup"`` — callers observing stop_reason strings cannot
    tell them apart, by design.
    """
    DONE            = "done"
    DONE_FLAG       = "done_flag"
    GIVEUP_RESIDUAL = "giveup_residual"   # LLM giveup or runtime divergence
    GIVEUP_BUDGET   = "giveup_budget"     # LLM repair turns exhausted
    GIVEUP_STUCK    = "giveup_stuck"      # no new graph obligations (discover rounds)
    MAX_CYCLES      = "max_cycles"


_TERMINATION_TO_STOP_REASON: dict[TerminationReason, str] = {
    TerminationReason.DONE:            "planner_done",
    TerminationReason.DONE_FLAG:       "done_flag",
    TerminationReason.GIVEUP_RESIDUAL: "planner_giveup",
    TerminationReason.GIVEUP_BUDGET:   "planner_giveup",
    TerminationReason.GIVEUP_STUCK:    "planner_giveup",
    TerminationReason.MAX_CYCLES:      "max_cycles",
}


def _to_stop_reason(reason: TerminationReason) -> str:
    """Map an internal ``TerminationReason`` to the external stop-reason string.

    The returned string is identical to the literal that was previously
    hard-coded at each return site — callers observing stop_reason strings
    are unaffected.
    """
    return _TERMINATION_TO_STOP_REASON[reason]


# ---------------------------------------------------------------------------
# run_v1 — three-role planner-driven loop
# ---------------------------------------------------------------------------

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
    enable_dep_emit: bool = False,
    enable_runtime_feedback: bool = False,
):
    """Top-level v1 three-role orchestrator loop (planner-driven).

    Returns ``(final_map, stop_reason)`` where ``stop_reason`` is one of:
      ``'done_flag'``     — maintainer set WorldModelMap.done_flag=True
      ``'planner_done'``  — planner emitted action='done'
      ``'planner_giveup'``— planner emitted action='giveup'
      ``'max_cycles'``    — loop ran for max_cycles without terminating

    The loop terminates the instant done_flag is set — it does NOT wait
    for the next planner.decide call (structural fix for the 'reached gate
    but never committed' failure mode).

    New optional kwargs (all default off — every existing test and the A1 arm
    are byte-for-byte unchanged):
      exec_readonly         — callable(cmd) -> (rc: int, out: str) for read-only probes
    """
    current_map: WorldModelMap = initial_world_map
    # Monotonic step counter for ledger offsets (avoids cycle-based aliasing).
    global_step: int = 0
    # Runtime-feedback high-water mark: index into ledger.events() up to which we
    # have already ingested. Starts at 0 so the first ingest captures every event
    # not yet ingested — including any written before the loop began (e.g. a
    # pre-seeded failure). _runtime_ingest_phase advances it.
    _rt_mark: int = 0

    def _dep_emit_phase(cycle: int) -> None:
        nonlocal current_map, global_step
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
        # deterministic prefix regardless of arm so the LLM only sees the
        # irreducible non-emittable residual. global_step is advanced here only
        # if emit_drain consumed steps, so LLM turns are NOT counted.
        graph, _reports, steps = emit_drain(
            graph, build_agent, sandbox_execute, ledger, exec_readonly,
            step_offset=global_step, cycle=cycle,
        )
        if steps:
            global_step += steps
        # Fold emit-certified packages into installed so the synthesizer's closure
        # recipe includes them even when the planner finalizes immediately.
        sat = tuple(Fact(n.name, n.version or "") for n in graph.nodes
                    if n.type is NodeType.PACKAGE and n.state is State.SATISFIED)
        have = {f.name for f in current_map.installed}
        installed = current_map.installed + tuple(f for f in sat if f.name not in have)
        advisory = render_depgraph_planner(graph)
        current_map = merge_map(
            current_map, dep_graph=graph, dep_advisory=advisory, installed=installed,
        )

    def _runtime_ingest_phase() -> None:
        nonlocal current_map, _rt_mark
        if not enable_runtime_feedback or current_map.dep_graph is None:
            return
        try:
            from python_deps.depgraph.advise import render_depgraph_planner
            from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
            from python_deps.depgraph.runtime_classify import classify_observation
            events = ledger.events()
            new_events = events[_rt_mark:]
            obs = [(e.cmd, e.stdout) for e in new_events if e.rc != 0]
            if not obs:
                return
            pre_graph = current_map.dep_graph

            # Deterministic regex classifier only — no LLM tier in the v1 arm.
            classifiers = (classify_observation,)

            new_graph, found = ingest_runtime_failures(pre_graph, obs, classifiers=classifiers)
            # Advance the mark ONLY after a successful ingest call returns — so an
            # exception mid-ingest does not permanently drop those events (they are
            # re-read next cycle). (spec §11; C4 event-loss fix.)
            _rt_mark = len(events)
            if not found:
                return
            advisory = render_depgraph_planner(new_graph)
            current_map = merge_map(current_map, dep_graph=new_graph, dep_advisory=advisory)
        except (NameError, AttributeError, ImportError, TypeError) as exc:
            # Programming errors are always bugs, never operational. Still don't
            # break the run (spec §11), but log LOUDLY with a traceback — a bare
            # suppress here once hid a missing `import os` that silently disabled
            # the runtime-feedback loop for an entire benchmark arm.
            import logging
            logging.getLogger(__name__).error(
                "runtime_ingest_phase: PROGRAMMING BUG suppressed: %s", exc,
                exc_info=True,
            )
        except Exception as exc:  # noqa: BLE001 — operational errors must never break the run (spec §11)
            import logging
            logging.getLogger(__name__).warning(
                "runtime_ingest_phase: exception suppressed: %s", exc
            )

    current_map = host_refresh_facts(current_map, probe, manifest)

    for cycle in range(1, max_cycles + 1):
        # ── 0. Graph-first: certify + emit the certified closure ────────────
        _dep_emit_phase(cycle)
        # ── 0b. Runtime feedback: ingest ledger failures from the PREVIOUS cycle
        #        into the live dep-graph. Runs once per cycle before any branch so
        #        it fires regardless of which branch returns (I2 done-path fix).
        _runtime_ingest_phase()
        # ── 1. Planner decides what to do next ──────────────────────────────
        decision: PlannerDecision = planner.decide(current_map)

        if decision.action == "done":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, _to_stop_reason(TerminationReason.DONE)

        if decision.action == "giveup":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, _to_stop_reason(TerminationReason.GIVEUP_RESIDUAL)

        # ── 2. Recipe patch branch ───────────────────────────────────────────
        if decision.action == "apply_recipe_patch":
            recipe: RecipePatch | None = decision.recipe_patch
            if recipe is None or not recipe.steps:
                # Empty recipe — nothing to execute; let maintainer decide.
                empty_report = TaskReport("recipe", "done", (), "empty recipe")
                current_map = maintainer.update(current_map, empty_report)
                if on_cycle is not None:
                    on_cycle(cycle, current_map, decision, empty_report)
                if current_map.done_flag:
                    return current_map, _to_stop_reason(TerminationReason.DONE_FLAG)
                continue

            # Execute the whole recipe as a single unified run.
            report: TaskReport = build_agent.run_recipe(
                recipe,
                sandbox_execute,
                ledger,
                step_offset=global_step,
            )
            global_step += len(report.commands)

            # Deterministic facts after recipe (before the maintainer).
            current_map = host_refresh_facts(current_map, probe, manifest)

            # ── 3. Maintainer updates the world model ─────────────────────
            current_map = maintainer.update(current_map, report)

            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, report)

            # ── 4. Hard-stop on done_flag — do NOT check mid-recipe ───────
            if current_map.done_flag:
                return current_map, _to_stop_reason(TerminationReason.DONE_FLAG)

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
            check=None,
            budget=local_budget,
        )
        global_step += len(report.commands)

        # ── 3b. Deterministic facts (read-only probe, OFF the ledger) ────────
        current_map = host_refresh_facts(current_map, probe, manifest)

        # ── 4. Maintainer updates the world model ────────────────────────────
        current_map = maintainer.update(current_map, report)

        # ── 5. Notify caller (optional telemetry hook) ───────────────────────
        if on_cycle is not None:
            on_cycle(cycle, current_map, decision, report)

        # ── 6. Hard-stop on done_flag — do NOT re-enter planner ──────────────
        if current_map.done_flag:
            return current_map, _to_stop_reason(TerminationReason.DONE_FLAG)

    # Exhausted all cycles without termination.
    return current_map, _to_stop_reason(TerminationReason.MAX_CYCLES)


# ---------------------------------------------------------------------------
# run_v3 — graph-scheduler loop (no planner)
# ---------------------------------------------------------------------------

def _build_install_evidence(result, failed_id, cycle):
    """Wrap a FAILED binding-install InstallResult as a single-item EvidenceBundle so the
    repair proposer sees the install stderr (RepairScope.failed_output) AND can cite it
    (PatchGate requires every proposed requirement/script-patch to reference a known evidence
    id). The install runs from a fresh-from-base container = 'fresh_replay'.

    ``failed_id`` is whatever localize_install_failure resolved — a #@node id OR a #@block id —
    so it is written to BOTH Evidence.node_id and Evidence.block_id. run_structured_repair looks
    the id up as a block_id, and build_repair_scope copies stderr only when
    ``ev.block_id == failed_block.block_id``; setting block_id keeps the stderr visible on a
    #@block failure, while node_id stays correct for the common graph-node case.
    """
    from python_deps.depgraph.evidence_log import Evidence, EvidenceBundle
    ev = Evidence(
        evidence_id=f"install.{cycle}.{failed_id or 'unknown'}",
        container_kind="fresh_replay",
        command=result.failing_command or "(install script)",
        rc=result.rc,
        output_excerpt=(result.stderr or "")[-2000:],
        cycle=cycle,
        node_id=failed_id,
        block_id=failed_id,
    )
    return EvidenceBundle().with_item(ev)


def run_v3(
    build_agent,
    maintainer,
    initial_world_map: WorldModelMap,
    ledger: ActionLedger,
    sandbox_execute: Executor,
    max_cycles: int = MAX_CYCLES,
    on_cycle=None,
    *,
    probe=None,
    manifest=None,
    exec_readonly=None,
    enable_dep_emit: bool = True,
    enable_runtime_feedback: bool = True,
    graph_scheduler_attempt_cap: int = 3,
    enable_script_materialization: bool = True,
    enable_gate_observability: bool = False,   # Stage 1 — observability only, byte-identical off
    gate_observer=None,                        # Callable[[tuple[GateResult, GateResult]], None] | None
    enable_binding_install: bool = False,      # Stage 2 — binding dep-spine install; byte-identical off
    reset_to_base=None,                        # Callable[[], None] | None  (Sandbox.reset_to_base)
    run_install_script=None,                   # Callable[[str], InstallResult] | None
):
    """Top-level v3 graph-scheduler orchestrator loop (no planner).

    Returns ``(final_map, stop_reason)`` where ``stop_reason`` is one of:
      ``'done_flag'``      — maintainer set WorldModelMap.done_flag=True
      ``'planner_done'``   — scheduler returned action='done'
      ``'planner_giveup'`` — scheduler gave up (budget/residual/stuck)
      ``'max_cycles'``     — loop ran for max_cycles without terminating

    Planner is ABSENT: ``next_decision`` from the dep-graph scheduler drives
    every cycle. dep_emit and runtime_feedback are on by default (their params
    are kept for caller/test compatibility — the bodies treat them as always-on
    once the guards below are passed).

    The graph scheduler replaces the three-role LLM planner with a pure-function
    certify → emit → decide pipeline. LLM turns are bounded by ``_repair_turns``
    (seeded to ``max_cycles``).
    """
    from src.envstate.graph_scheduler import next_decision
    # The binding-install path is a specialization of script materialization; it cannot run
    # without it. Reject the contradictory combination instead of silently no-opping (which
    # would fall through to emit_drain and look like a clean run). Only fires when the new
    # flag is on, so enable_binding_install=False stays byte-identical.
    if enable_binding_install and not enable_script_materialization:
        raise ValueError(
            "enable_binding_install=True requires enable_script_materialization=True")
    current_map: WorldModelMap = initial_world_map
    # Monotonic step counter for ledger offsets (avoids cycle-based aliasing).
    global_step: int = 0
    # Runtime-feedback high-water mark (same semantics as in run_v1).
    _rt_mark: int = 0
    _residual_giveup: str | None = None   # set when a residual is non-env / divergent (spec §3 G3, §8)
    _handed: dict[str, int] = {}          # per-obligation hand-out counts
    _sched_stuck: int = 0                 # consecutive discover cycles with no new obligations
    _sched_last_nodes: int = -1           # dep-graph node count at the last discover cycle
    _repaired_ids: set[str] = set()       # nodes given a host-first repair this run (one each)
    _repair_turns: int = max_cycles       # LLM-repair budget (NOT mechanical installs)
    _budget_exhausted: bool = False
    _known_invalid: set[str] = set()
    _manual_blocks: tuple = ()            # persists ScriptPatch blocks across cycles
    MAX_REPAIRS_PER_BLOCK: int = 5

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

    def _finish(reason):
        # Stage 1 two-gate observability: fires once on the way out (any exit path).
        # Reads existing signals, writes nothing. OFF -> no-op (byte-identical result).
        if enable_gate_observability:
            from src.envstate.gates import evaluate_gates
            _g = evaluate_gates(current_map.dep_graph, _run_tests_verified)
            if gate_observer is not None:
                gate_observer(_g)
        _stop = _to_stop_reason(reason)
        return current_map, _stop

    def _dep_emit_phase(cycle: int) -> None:
        nonlocal current_map, global_step, _repaired_ids, _repair_turns, _budget_exhausted, _manual_blocks, _known_invalid
        if not enable_dep_emit or current_map.dep_graph is None:
            return
        if exec_readonly is None:                      # R3(c): no certify path -> no emit
            return
        from python_deps.depgraph.schema import NodeType, State
        from src.envstate.world_model import Fact
        from src.envstate.depgraph_live import certify_refresh, emit_drain, ensure_python_shim
        from python_deps.depgraph.advise import render_depgraph_planner
        # Make a bare `python` resolve to python3 before any check runs.
        ensure_python_shim(sandbox_execute)
        graph = certify_refresh(current_map.dep_graph, exec_readonly, cycle)
        # Certify still runs (populating the frontier); emit_drain runs as a
        # deterministic prefix so the LLM only sees the irreducible residual.
        # global_step is advanced here only if emit_drain consumed steps, so
        # LLM turns are NOT counted.
        def _binding_emit(graph, manual_blocks, cycle):
            from python_deps.depgraph.build_script import render_build_script
            from python_deps.depgraph.emit import _is_reciped
            from src.envstate.install_localizer import localize_install_failure, certify_reciped_only
            # Defense-in-depth: a repair proposal must not add a reciped node that can't be certified.
            _missing = [n.id for n in graph.nodes if _is_reciped(n) and not n.check_command]
            if _missing:
                raise ValueError(
                    f"binding-install repair: reciped nodes lack a check_command: {_missing}")
            script = render_build_script(graph, manual_blocks)
            reset_to_base()
            result = run_install_script(script)
            graph, unsat = certify_reciped_only(graph, exec_readonly, cycle)
            if result.rc != 0:
                _node = (localize_install_failure(script, result.failing_command).node_id
                         or (unsat[0] if unsat else None))
                # Carry the fresh install stderr as evidence into the next repair scope.
                return graph, _build_install_evidence(result, _node, cycle), _node
            return graph, None, (unsat[0] if unsat else None)

        if enable_script_materialization and enable_binding_install:
            # Stage 2: binding dep-spine install — render whole script, reset to base,
            # install, certify reciped nodes. Repair reuses run_structured_repair.
            from python_deps.depgraph.build_script import render_build_script
            from python_deps.depgraph.emit import _is_reciped
            from src.envstate.install_localizer import localize_install_failure, certify_reciped_only
            if reset_to_base is None or run_install_script is None:
                raise ValueError(
                    "binding-install: reset_to_base and run_install_script are required "
                    "when enable_binding_install=True")
            # Consumer fail-fast: a reciped node with no check_command cannot be certified.
            missing_check = [n.id for n in graph.nodes if _is_reciped(n) and not n.check_command]
            if missing_check:
                raise ValueError(
                    f"binding-install: reciped nodes lack a check_command: {missing_check}")
            script = render_build_script(graph, _manual_blocks)
            reset_to_base()
            result = run_install_script(script)
            graph, _unsat = certify_reciped_only(graph, exec_readonly, cycle)
            install_ok = result.rc == 0
            _failed_node = None
            if not install_ok:
                _failed_node = (localize_install_failure(script, result.failing_command).node_id
                                or (_unsat[0] if _unsat else None))
            elif _unsat:
                _failed_node = _unsat[0]
            if _failed_node is not None and getattr(build_agent, "client", None) is not None:
                # On an install failure, seed the repair with the install stderr as citable
                # evidence; on an rc0-but-unsatisfied node there is no install command failure,
                # so the scope falls back to the node's requirement slice (bundle None).
                _bundle = _build_install_evidence(result, _failed_node, cycle) if not install_ok else None
                _out = run_structured_repair(
                    graph, _failed_node, _bundle, cycle,
                    propose=lambda s, **k: build_agent.propose(s, exec_readonly, **k),
                    emit=lambda g, mb: _binding_emit(g, mb, cycle),
                    manual_blocks=_manual_blocks, known_invalid=_known_invalid,
                    max_repairs=MAX_REPAIRS_PER_BLOCK, repair_budget=_repair_turns,
                    # _failed_node is a NODE id (from certify/localize), not a block id; the
                    # binding path has no block-keyed bundle, so seed target_hint so the repair
                    # scope still resolves the unsatisfied node's requirement slice.
                    target_hint=_failed_node,
                    cap_failed_id=True)
                graph = _out.graph
                _manual_blocks = _out.manual_blocks
                _known_invalid = set(_out.known_invalid)
                _repair_turns -= _out.turns_spent
                if _out.budget_exhausted or _repair_turns <= 0:
                    _budget_exhausted = True
        elif enable_script_materialization:
            # Slice A: deterministic block run replaces emit_drain on v3 (design §5.1).
            # No LLM and no host-repair here — a failed block ends the wave (Slice B adds
            # the repair loop). compose_script handles the emittable wave; certify writes state.
            # NOTE: global_step is intentionally not advanced here (block_emit owns no LLM
            # turns); the repair loop's step accounting is wired in Slice B.
            from src.envstate.block_emit import block_emit
            graph, _bundle, _failed = block_emit(
                graph, sandbox_execute, exec_readonly, ledger, cycle,
                manual_blocks=_manual_blocks)
            if _failed is not None and getattr(build_agent, "client", None) is not None:
                _out = run_structured_repair(
                    graph, _failed, _bundle, cycle,
                    propose=lambda s, **k: build_agent.propose(s, exec_readonly, **k),
                    emit=lambda g, mb: block_emit(g, sandbox_execute, exec_readonly,
                                                  ledger, cycle, manual_blocks=mb),
                    manual_blocks=_manual_blocks, known_invalid=_known_invalid,
                    max_repairs=MAX_REPAIRS_PER_BLOCK, repair_budget=_repair_turns)
                graph = _out.graph
                _manual_blocks = _out.manual_blocks
                _known_invalid = set(_out.known_invalid)
                _repair_turns -= _out.turns_spent
                if _out.budget_exhausted or _repair_turns <= 0:
                    _budget_exhausted = True
        else:
            graph, _reports, steps = emit_drain(
                graph, build_agent, sandbox_execute, ledger, exec_readonly,
                step_offset=global_step, cycle=cycle,
            )
            if steps:
                global_step += steps
            # Host-first repair of reciped nodes the batch wave could not certify (the
            # broken-bridge fix). Only the graph-scheduler arm performs host repair.
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
        # Final re-certify after the emit: the start-of-cycle certify (above) ran
        # BEFORE any install this cycle, and run_blocks only certifies after each
        # block it ran (and skips the pass after a failed block). A node whose
        # install completes during THIS cycle's emit/repair must be reflected before
        # the scheduler's done-decision — otherwise it stays MISSING because the next
        # cycle's certify never runs once the done-gate finalizes the run. Idempotent.
        graph = certify_refresh(graph, exec_readonly, cycle)
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
        advisory = current_map.dep_advisory
        current_map = merge_map(
            current_map, dep_graph=graph, dep_advisory=advisory, installed=installed,
        )

    def _runtime_ingest_phase() -> None:
        nonlocal current_map, _rt_mark, _residual_giveup
        if not enable_runtime_feedback or current_map.dep_graph is None:
            return
        try:
            from python_deps.depgraph.advise import render_depgraph_planner
            from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
            from python_deps.depgraph.runtime_classify import classify_observation
            events = ledger.events()
            new_events = events[_rt_mark:]
            obs = [(e.cmd, e.stdout) for e in new_events if e.rc != 0]
            if not obs:
                return
            pre_graph = current_map.dep_graph
            _out_of_scope: list[tuple[str, str]] = []   # non-env diagnoses; Task 6 reads this

            # Deterministic regex tier always runs; the temp-0 LLM tier is appended
            # when a client exists (spec §6 cascade).
            classifiers = (classify_observation,)
            if getattr(build_agent, "client", None) is not None:
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
            import logging
            from python_deps.depgraph.runtime_ingest import diverged_node_ids
            from python_deps.depgraph.emit import partition
            from python_deps.depgraph.schedule import scheduler_frontier
            diverged = diverged_node_ids(pre_graph, found)
            # Match the real scheduler call (next_decision): the give-up
            # frontier must see Service/binding obligations on-arm, or it
            # reports "no actionable" while a binding is genuinely pending
            # and the agent gives up prematurely (spurious give-up).
            _allow_services = (
                os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"
            )
            no_actionable = not scheduler_frontier(
                new_graph, allow_services=_allow_services
            )
            if diverged and no_actionable and not partition(new_graph).emittable:
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
        except (NameError, AttributeError, ImportError, TypeError) as exc:
            # Programming errors are always bugs, never operational. Still don't
            # break the run (spec §11), but log LOUDLY with a traceback — a bare
            # suppress here once hid a missing `import os` that silently disabled
            # the runtime-feedback loop for an entire benchmark arm.
            import logging
            logging.getLogger(__name__).error(
                "runtime_ingest_phase: PROGRAMMING BUG suppressed: %s", exc,
                exc_info=True,
            )
        except Exception as exc:  # noqa: BLE001 — operational errors must never break the run (spec §11)
            import logging
            logging.getLogger(__name__).warning(
                "runtime_ingest_phase: exception suppressed: %s", exc
            )

    # Refresh contract — why each host_refresh_facts call is necessary and not
    # redundant with the next _dep_emit_phase certify:
    #
    #   host_refresh_facts  → probe() → apply_deterministic()
    #     Updates: installed (full set), env, language, system_installed,
    #              import_results, build_system, required, open_problems, progress.
    #
    #   _dep_emit_phase certify → certify_refresh() → merge_map()
    #     Updates: dep_graph (node states) + installed (PACKAGE nodes that reached
    #              State.SATISFIED).  Does NOT call probe(); never touches env,
    #              language, system_installed, import_results, build_system,
    #              required, open_problems, or progress.
    #
    # ① Pre-loop (here): initial_world_map may be stale from the caller; the
    #   first cycle's _dep_emit_phase certify does NOT call probe(), so it
    #   cannot fill in these fields.  Required.
    #
    # ② Post-task (inside task branch): build_agent just mutated the container.
    #   The NEXT cycle's certify won't call probe(), and maintainer.update in
    #   THIS cycle needs the fresh probe facts.  Required.
    #
    # Conclusion: both sites are the minimal set — neither can be removed
    # without leaving the maintainer or the scheduler with stale probe data.
    current_map = host_refresh_facts(current_map, probe, manifest)

    for cycle in range(1, max_cycles + 1):
        # ── 0. Graph-first: certify + emit the certified closure ────────────
        _dep_emit_phase(cycle)
        if _budget_exhausted:
            # graph-scheduler: LLM turn budget exhausted — bounded repair gave up
            return _finish(TerminationReason.GIVEUP_BUDGET)
        # ── 0b. Runtime feedback: ingest ledger failures from the PREVIOUS cycle
        #        into the live dep-graph. Runs once per cycle before any branch so
        #        it fires regardless of which branch returns (I2 done-path fix).
        _runtime_ingest_phase()
        if _residual_giveup is not None:
            return _finish(TerminationReason.GIVEUP_RESIDUAL)
        # ── 1. Graph-scheduler decides what to do next ──────────────────────
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
            if _sched_stuck >= 2:                # consecutive discover rounds revealed no new obligations
                if on_cycle is not None:
                    on_cycle(cycle, current_map, decision, None)
                return _finish(TerminationReason.GIVEUP_STUCK)

        if decision.action == "done":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return _finish(TerminationReason.DONE)

        if decision.action == "giveup":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return _finish(TerminationReason.GIVEUP_RESIDUAL)

        # ── 3. Graph-scheduler task branch (action == "task") ────────────────
        assert decision.task is not None, (
            f"PlannerDecision action='task' but .task is None (cycle {cycle})"
        )
        task = decision.task
        _targets = getattr(task, "target_node_ids", ()) or ()
        if (_targets and enable_script_materialization
                and exec_readonly is not None
                and getattr(build_agent, "client", None) is not None):
            from src.envstate.block_emit import block_emit
            from python_deps.depgraph.patch_gate import compose_script
            from python_deps.depgraph.evidence_log import EvidenceBundle
            _g = current_map.dep_graph
            _blocks = compose_script(_g, _manual_blocks) if _g is not None else ()
            _tid = _targets[0]
            _fb = next((b.block_id for b in _blocks if _tid in b.target_node_ids), None)
            _propose = lambda s, **k: build_agent.propose(s, exec_readonly, **k)
            _emit = lambda g, mb: block_emit(g, sandbox_execute, exec_readonly,
                                             ledger, cycle, manual_blocks=mb)
            if _fb is None:
                _out = run_structured_repair(
                    _g, _tid, EvidenceBundle(), cycle, target_hint=_tid,
                    propose=_propose, emit=_emit, manual_blocks=_manual_blocks,
                    known_invalid=_known_invalid, max_repairs=MAX_REPAIRS_PER_BLOCK,
                    repair_budget=_repair_turns)
            else:
                _g, _b2, _f2 = _emit(_g, _manual_blocks)
                _out = (run_structured_repair(
                            _g, _f2, _b2, cycle, target_hint=_tid, propose=_propose, emit=_emit,
                            manual_blocks=_manual_blocks, known_invalid=_known_invalid,
                            max_repairs=MAX_REPAIRS_PER_BLOCK, repair_budget=_repair_turns)
                        if _f2 is not None else None)
            if _out is not None:
                _g = _out.graph
                _manual_blocks = _out.manual_blocks
                _known_invalid = set(_out.known_invalid)
                _repair_turns -= _out.turns_spent
                if _out.budget_exhausted:
                    _budget_exhausted = True
            current_map = merge_map(current_map, dep_graph=_g)
            report = TaskReport(task.goal, "done", (), "structured-repair task")
        else:
            report = build_agent.run(
                task, sandbox_execute, ledger, step_offset=global_step,
                check=task.done_when, budget=SCHEDULER_TASK_BUDGET)
            _repair_turns -= 1          # free-text path: one LLM unit (typed path already charged)
        if _budget_exhausted or _repair_turns <= 0:
            return _finish(TerminationReason.GIVEUP_BUDGET)
        # Scheduler mode: a passing host check can return zero commands; floor the
        # advance at 1 so the ledger step offset never aliases across cycles.
        global_step += max(len(report.commands), 1)

        # ② Probe refresh after task — see refresh contract above.
        # Task mutated the container; next cycle's certify won't call probe(),
        # and maintainer.update below needs fresh probe facts (OFF the ledger).
        current_map = host_refresh_facts(current_map, probe, manifest)

        # ── 4. Maintainer updates the world model ────────────────────────────
        current_map = maintainer.update(current_map, report)

        # ── 5. Notify caller (optional telemetry hook) ───────────────────────
        if on_cycle is not None:
            on_cycle(cycle, current_map, decision, report)

        # ── 6. Hard-stop on done_flag — do NOT re-enter scheduler ────────────
        if current_map.done_flag:
            return _finish(TerminationReason.DONE_FLAG)

    # Exhausted all cycles without termination.
    return _finish(TerminationReason.MAX_CYCLES)
