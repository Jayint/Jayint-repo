"""run_repair_arm — the arm-C error loop (spec 2026-07-08 §4-5.1).

Render → clean replay → host certify → localize → route → fix_one_error → repeat. The build
script's execution order IS the schedule (no separate scheduler). Global termination:
DONE (clean replay green AND every required, check-bearing node is host-certified —
not just script exit code 0) or GIVEUP (same error unrepaired). Injected replay/certify/
agent keep it Docker-free; production injects Docker adapters, the eval injects FakeWorld."""
from __future__ import annotations

from python_deps.depgraph.emit import partition
from src.envstate.repair_fix import fix_one_error, EVIDENCE
from src.envstate.repair_types import ReplayResult


def _default_localize(result):
    return result                                   # the failing ReplayResult IS the error


def _default_diagnose(error):
    return "ENVIRONMENT"                            # production injects the real DiagnosisRouter


def _first_unmet_required_node(graph):
    """A required, host-checkable node still MISSING after a green replay + host
    certify (the known-bug gap): a node with no chosen_fix/version yet renders NOTHING
    in the build script (see emit._is_reciped / render_build_script), so the script can
    exit 0 while that obligation was never installed. Reuses ``partition``'s frontier
    (MISSING and not emittable) rather than inventing a new classifier — this ALSO
    covers a reciped node whose check still fails for some other reason (including a
    certification revocation, spec §9: a node that WAS SATISFIED and got demoted).
    Filtered to check-bearing nodes only: no check_command means no host stop
    condition (same exclusion ``emit.failed_reciped_nodes`` uses). Deterministic
    (tier, id) order so localization is stable."""
    frontier = [n for n in partition(graph).frontier if n.check_command]
    if not frontier:
        return None
    return min(frontier, key=lambda n: (n.tier, n.id))


def _unmet_node_error(node) -> ReplayResult:
    """Synthesize the localized 'error' for a required node the replay never touched
    (or silently left/put back in MISSING) — fed through the SAME localize/diagnose/
    fix_one_error pipeline as a real replay failure."""
    return ReplayResult(
        ok=False, failing_node=node.id, failing_cap=node.id,
        failing_command=node.check_command,
        output=(f"{node.id} is still MISSING after a green install — either no recipe "
                f"was ever rendered for it (no chosen_fix/version yet), or its check "
                f"still fails."),
    )


def _mark_stuck(stuck: dict[str, int], node_id: str) -> int:
    """Bump and return the repeat-count of an unrepaired error at ``node_id`` — two
    repeats at the same node (non-env route, or a stalled session) means GIVEUP."""
    stuck[node_id] = stuck.get(node_id, 0) + 1
    return stuck[node_id]


def run_repair_arm(graph, *, replay, certify, agent, log, readonly=None,
                   localize=None, diagnose=None, known_evidence_ids=EVIDENCE,
                   max_errors: int = 20):
    localize = localize or _default_localize
    diagnose = diagnose or _default_diagnose
    stuck: dict[str, int] = {}
    for _ in range(max_errors):
        log.d("RENDER", f"rendered script from {len(graph.nodes)} graph nodes")
        result = replay(graph, ())
        graph = certify(graph)
        if result.ok:
            unmet = _first_unmet_required_node(graph)
            if unmet is None:
                log.d("DONE", "clean replay green — build works")
                return "DONE", graph
            result = _unmet_node_error(unmet)
        error = localize(result)
        log.d("LOCALIZE", f"first failure at {error.failing_node} (missing {error.failing_cap})")
        route = diagnose(error)
        log.d("DIAGNOSE", f"route={route}")
        if route != "ENVIRONMENT":
            if _mark_stuck(stuck, error.failing_node) >= 2:
                log.d("GIVEUP", f"non-env error at {error.failing_node} — give up")
                return "GIVEUP", graph
            continue
        graph, outcome = fix_one_error(graph, error, agent=agent, replay=replay,
                                       certify=certify, log=log, readonly=readonly,
                                       known_evidence_ids=known_evidence_ids)
        if outcome == "stalled" and _mark_stuck(stuck, error.failing_node) >= 2:
            log.d("GIVEUP", f"same error at {error.failing_node} unrepaired — honest give-up")
            return "GIVEUP", graph
    return "GIVEUP", graph
