"""run_repair_arm — the arm-C error loop (spec 2026-07-08 §4-5.1).

Render → clean replay → host certify → localize → route → fix_one_error → repeat. The build
script's execution order IS the schedule (no separate scheduler). Global termination:
DONE (clean replay green) or GIVEUP (same error unrepaired). Injected replay/certify/agent
keep it Docker-free; production injects Docker adapters, the eval injects FakeWorld."""
from __future__ import annotations

from src.envstate.repair_fix import fix_one_error


def _default_localize(result):
    return result                                   # the failing ReplayResult IS the error


def _default_diagnose(error):
    return "ENVIRONMENT"                            # production injects the real DiagnosisRouter


def run_repair_arm(graph, *, replay, certify, agent, log, readonly=None,
                   localize=None, diagnose=None, max_errors: int = 20):
    localize = localize or _default_localize
    diagnose = diagnose or _default_diagnose
    stuck: dict[str, int] = {}
    for _ in range(max_errors):
        log.d("RENDER", f"rendered script from {len(graph.nodes)} graph nodes")
        result = replay(graph, ())
        graph = certify(graph)
        if result.ok:
            log.d("DONE", "clean replay green — build works")
            return "DONE", graph
        error = localize(result)
        log.d("LOCALIZE", f"first failure at {error.failing_node} (missing {error.failing_cap})")
        route = diagnose(error)
        log.d("DIAGNOSE", f"route={route}")
        if route != "ENVIRONMENT":
            stuck[error.failing_node] = stuck.get(error.failing_node, 0) + 1
            if stuck[error.failing_node] >= 2:
                log.d("GIVEUP", f"non-env error at {error.failing_node} — give up")
                return "GIVEUP", graph
            continue
        graph, outcome = fix_one_error(graph, error, agent=agent, replay=replay,
                                       certify=certify, log=log, readonly=readonly)
        if outcome == "stalled":
            k = error.failing_node
            stuck[k] = stuck.get(k, 0) + 1
            if stuck[k] >= 2:
                log.d("GIVEUP", f"same error at {k} unrepaired — honest give-up")
                return "GIVEUP", graph
    return "GIVEUP", graph
