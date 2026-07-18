"""Incremental-execution ABLATION baseline for v3 (R7 quarantine) — NOT the canonical path.

The canonical executor is fresh full-script replay (orchestrator._binding_emit inside
run_v3, Model B): render the WHOLE certified graph to one install-only script, reset to
base, replay every cycle. run_v3 never imports or calls anything here (grep-confirmed).
This module exists only for its own unit tests and a future incremental-vs-replay ablation.
Do NOT wire it back into run_v3.

Parked here together (3b-6, spec §4E R7): block_emit (graph-compiled block execution +
ledger dual-write) + emit_drain / repair_failed_nodes (the retired legacy planner-driven
incremental path). All three reuse the LIVE executor's certify_refresh / run_blocks, which
they import from execute.py.
"""
from __future__ import annotations

from typing import Callable

from graph.emit.emit import EMIT_ATTEMPT_TAG, build_recipe, partition, topo_order
from graph.model import Attempt
from graph.mutate.patch_gate import compose_script
from src.orchestrate.loop.execute import certify_refresh, run_blocks
from src.orchestrate.loop.ledger import ActionEvent, ActionLedger
from src.orchestrate.loop.world_model import RecipePatch, RecipeStep



# === block_emit.py: deterministic graph-compiled block-emit + ledger dual-write (design §5.1) ===
def block_emit(
    graph,
    sandbox_execute: Callable[[str], tuple[bool, str]],
    exec_readonly: Callable[[str], tuple[int, str]],
    ledger: ActionLedger,
    cycle: int,
    *,
    manual_blocks: tuple = (),
):
    """Run the graph-compiled blocks; mirror each command into ``ledger``; certify via
    run_blocks' host checks. Returns (certified_graph, EvidenceBundle, failed_block_id).

    The dual-write records ACTIONS only — node state is written exclusively by
    certify_refresh inside run_blocks (invariants #3/#4). Both successful and failed
    commands are mirrored; failures (rc != 0) feed _runtime_ingest_phase."""
    blocks = compose_script(graph, manual_blocks)

    def _mirroring_sandbox(cmd: str) -> tuple[bool, str]:
        ok, out = sandbox_execute(cmd)
        ledger.append(ActionEvent(
            step=len(ledger.events()),          # monotonic step (ActionEvent.step is required)
            cmd=cmd,
            rc=0 if ok else 1,
            stdout=out or "",
            mutation_class="file_or_env_change",
        ))
        return ok, out

    return run_blocks(blocks, _mirroring_sandbox, exec_readonly, graph, cycle)



# === depgraph_live.py (PARKED half): emit_drain + repair_failed_nodes (legacy incremental) ===
def emit_drain(
    graph,
    build_agent,
    sandbox_execute,
    ledger,
    exec_readonly,
    *,
    step_offset: int,
    cycle: int,
    max_drain: int = 4,
):
    """Drain the certifiable closure: emit -> run -> re-certify, repeat.

    Each pass emits the current emittable set (apt then pip), runs it through the
    real ``build_agent.run_recipe`` (D4: repair is a free safety layer), records
    an emit Attempt per target node, then re-certifies against the live container.
    Certifying a toolchain unlocks the build-from-source package that needs it, so
    the next pass picks it up (D5). Bounded by ``max_drain``.

    Returns ``(new_graph, reports, steps_consumed)``.
    """
    reports: list = []
    steps_consumed = 0
    new = graph
    if new is None or not new.nodes:
        return new, reports, steps_consumed

    for _ in range(max_drain):
        part = partition(new)
        if not part.emittable:
            break
        ordered = topo_order(new, part.emittable)
        emit_steps = build_recipe(new, ordered)
        if not emit_steps:
            break

        recipe = RecipePatch(steps=tuple(
            RecipeStep(
                id=f"emit-{cycle}-{i}",
                kind=s.kind,
                command=s.command,
                target_node_ids=s.target_node_ids,
            )
            for i, s in enumerate(emit_steps)
        ))
        report = build_agent.run_recipe(
            recipe, sandbox_execute, ledger, step_offset=step_offset + steps_consumed
        )
        reports.append(report)
        steps_consumed += len(report.commands)

        outcome = "succeeded" if report.status == "done" else "failed"
        for s in emit_steps:
            for nid in s.target_node_ids:
                node = new.get(nid)
                if node is not None:
                    # check=EMIT_ATTEMPT_TAG lets partition's backoff count failed
                    # emits and demote a doomed node to the frontier (no re-emit loop).
                    new = new.with_node(
                        node.with_attempt(Attempt(
                            command=s.command, outcome=outcome, cycle=cycle,
                            check=EMIT_ATTEMPT_TAG,
                        ))
                    )

        new = certify_refresh(new, exec_readonly, cycle)

    return new, reports, steps_consumed


def repair_failed_nodes(
    graph,
    build_agent,
    sandbox_execute,
    ledger,
    exec_readonly,
    *,
    step_offset: int,
    cycle: int,
    repaired_ids: set,
    max_repair: int = 3,
    budget: int = 5,
):
    """Host-first repair of reciped nodes the batch wave could not certify.

    For each failed reciped node not already in ``repaired_ids`` (capped at
    ``max_repair`` per call), frame a one-node ``Task`` and run a bounded
    host-first repair: ``BuildAgent.run`` with ``check=node.check_command`` and
    ``budget=budget`` — the LLM only proposes commands, the HOST check is the
    stop. Re-certify after each. One repair per node per run (cross-cycle memory
    lives in the caller's ``repaired_ids`` set).

    Returns ``(new_graph, steps_consumed, repaired_count)``.
    """
    from graph.emit.emit import failed_reciped_nodes
    from src.orchestrate.loop.world_model import Task

    steps = 0
    repaired = 0
    new = graph
    for node in failed_reciped_nodes(new):
        if repaired >= max_repair:
            break
        if node.id in repaired_ids:
            continue
        repaired_ids.add(node.id)
        task = Task(
            goal=f"Make the host check `{node.check_command}` succeed for "
                 f"{node.type.name} '{node.name}'. A batched install left it unsatisfied; "
                 f"read the error and provide whatever it needs (e.g. a system library).",
            done_when=node.check_command,
            layer=getattr(node.layer, "name", "pip").lower(),
            facts=(f"node: {node.id}",),
            target_node_ids=(node.id,),
        )
        report = build_agent.run(
            task, sandbox_execute, ledger, step_offset=step_offset + steps,
            check=node.check_command, budget=budget,
        )
        steps += len(report.commands)
        repaired += 1
        new = certify_refresh(new, exec_readonly, cycle)   # HOST flips state, not the LLM
    return new, steps, repaired
