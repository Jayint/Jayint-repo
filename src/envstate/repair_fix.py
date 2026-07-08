"""fix_one_error — the sustained per-error session loop (spec 2026-07-08 §5).

One conversation per error; the agent SEES its own patch history (compounding memory);
only a host check flips state (via the injected ``certify``); the agent's only mutation is
a typed ``PatchProposal`` through the REAL ``admit_proposal`` gate. Injected
``agent``/``replay``/``certify`` keep it Docker-free and unit-testable — the SAME function
runs in production (Docker adapters) and in the offline eval (FakeWorld adapters)."""
from __future__ import annotations

from python_deps.depgraph.patch_gate import admit_proposal
from python_deps.depgraph.schema import State
from src.envstate.repair_session import (
    RepairSession, Step, made_progress, persist_session_to_attempts,
)

# Eval evidence id; the production entry supplies the real known-evidence set.
EVIDENCE = frozenset({"ev.1"})


def fix_one_error(graph, error, *, agent, replay, certify, log, readonly=None,
                  stall_limit: int = 2, turn_cap: int = 15,
                  known_evidence_ids=EVIDENCE):
    session = RepairSession(error.failing_node, error.failing_cap)
    log.d("SESSION_START", f"error_key=({error.failing_node}, {error.failing_cap})")
    no_progress = 0
    current = error
    while len(session.steps) < turn_cap:
        act = agent.next_action(session, current, log)
        if act[0] == "probe":
            _, cmd, cap = act
            rc, out = readonly(cmd) if readonly is not None else (0, "")
            session.steps.append(Step("probe", f"probe:{cmd}", cap=cap,
                                      output=(out or "").strip()[:200]))
            log.d("SESSION_PROBE", f"{cmd} -> rc{rc} (read-only, no mutation)")
            continue
        _, patch, cap = act
        admit = admit_proposal(graph, patch, known_evidence_ids=known_evidence_ids)
        added = [r.id for r in patch.add_requirements]
        log.d("GATE", f"add={added} accepted={admit.accepted} errs={list(admit.errors)}")
        if not admit.accepted:
            session.steps.append(Step("patch", f"REJECTED:{added}", cap=cap, accepted=False))
            no_progress += 1
            if no_progress >= stall_limit:
                log.d("SESSION_STALL", f"{no_progress} rejects — give up {error.failing_node}")
                return persist_session_to_attempts(graph, session, error.failing_node), "stalled"
            continue
        graph = admit.graph
        before = {n.id for n in graph.nodes if n.state is State.SATISFIED}
        result = replay(graph, admit.manual_blocks)
        log.d("CLEAN_REPLAY",
              f"from base → {'OK' if result.ok else f'FAIL {result.failing_cap}'}")
        graph = certify(graph)
        newly = sorted({n.id for n in graph.nodes if n.state is State.SATISFIED} - before)
        log.d("HOST_CERTIFY", f"newly SATISFIED (host check): {newly or '∅'}")
        prog = made_progress(session, result)
        session.steps.append(Step("patch", f"add:{added}", cap=cap, accepted=True,
                                  replay=result, progress=prog))
        log.d("SESSION_PATCH",
              f"applied {added}; replay {'green' if result.ok else result.failing_cap}")
        log.d("PROGRESS", f"progress={prog}")
        if result.ok or result.failing_node != error.failing_node:
            log.d("SESSION_RESOLVED", f"{error.failing_node} past seed error")
            graph = persist_session_to_attempts(graph, session, error.failing_node)
            log.d("ATTEMPTS_PERSIST",
                  f"{sum(1 for s in session.steps if s.kind == 'patch')} "
                  f"patch-steps → {error.failing_node}.attempts")
            return graph, "resolved"
        current = result
        no_progress = 0 if prog else no_progress + 1
        if no_progress >= stall_limit:
            log.d("SESSION_STALL", f"{no_progress} no-progress — give up {error.failing_node}")
            return persist_session_to_attempts(graph, session, error.failing_node), "stalled"
    log.d("SESSION_STALL", f"turn cap {turn_cap} hit")
    return persist_session_to_attempts(graph, session, error.failing_node), "stalled"
