"""Graph context for the react arm (spec Rev 3.3 §6). Pure — no Docker, no network.

This module owns EVERY rule about what an edge means. Nothing downstream of
``verdict()`` touches an edge attribute. Today those rules are smeared across
``emit._toolchain_ready`` (soft), ``emit._conflicted_ids``/``_is_emittable``
(conflicts), and ``resolve_lock``'s marker pruning (markers); the arm gets ONE
copy, unit-tested against hand-built graphs.
"""
from __future__ import annotations

import logging

from python_deps.depgraph.schema import DepGraph, Edge, EdgeType, Node, State

logger = logging.getLogger(__name__)

ACTIONABLE = "ACTIONABLE"      # MISSING, nothing missing beneath it -> the agent acts HERE
WAITING = "WAITING"            # a hard prerequisite is missing -> fix that first
BLOCKED = "BLOCKED"            # in a version conflict -> NO install will ever work
SATISFIED_OK = "SATISFIED_OK"  # already fine -> nothing to do, and NO record


def _marker_holds(edge: Edge, target_env: dict | None) -> bool:
    """True when the edge's PEP 508 marker holds for the target (or is unevaluable).

    A universal lock lists dependencies for the WHOLE requires-python range, so an edge
    carrying `python_version < "3.9"` is not causal on a 3.12 target. When we cannot
    evaluate — no target env, or an unparseable marker — we traverse CONSERVATIVELY:
    dropping a real prerequisite is far worse than keeping a spurious one, because the
    spurious one will simply certify SATISFIED and land in the rule-out ring.
    """
    marker = getattr(edge, "marker", None)
    if not marker or target_env is None:
        return True
    try:
        from packaging.markers import Marker
        return bool(Marker(marker).evaluate(target_env))
    except Exception:                                  # noqa: BLE001 — never break the render
        logger.debug("graph_context: unevaluable marker %r; traversing", marker)
        return True


def blocks(edge: Edge, target_env: dict | None = None) -> bool:
    """Is this edge a HARD, CAUSAL prerequisite on THIS target?

    The three ways a graph edge is NOT a prerequisite:
      * it is not a REQUIRES edge at all (CONFLICTS_WITH is a constraint, not a need);
      * it is SOFT -- emit.py:69-70, "soft requires edges never block (invariant #10)";
      * its environment marker does not hold for the target (resolve_lock.py:442-451).
    """
    if edge.relation is not EdgeType.REQUIRES:
        return False
    if not (edge.data or {}).get("hard", True):
        return False
    return _marker_holds(edge, target_env)


def in_conflict(graph: DepGraph, node: Node) -> bool:
    """True when the node sits on a CONFLICTS_WITH edge (uv unsat core).

    `emit._is_emittable` (emit.py:84-100) already refuses to emit such a node: it cannot be
    installed at ANY version. Without this check the node looks like a perfectly good root
    -- MISSING, with no missing prerequisite -- and we would tell the agent to `pip install`
    it, forever.
    """
    return any(
        e.relation is EdgeType.CONFLICTS_WITH and node.id in (e.src, e.dst)
        for e in graph.edges
    )


def verdict(graph: DepGraph, node: Node, target_env: dict | None = None) -> str:
    """SATISFIED_OK | BLOCKED | WAITING | ACTIONABLE — the node's decision state.

    Order matters, and both early returns are load-bearing:

      1. A SATISFIED node is DONE. Checking prerequisites first would call a satisfied leaf
         ACTIONABLE (it has no MISSING prerequisites, after all) and hand it a "fix this"
         record — telling the agent to install something that is already installed.
      2. A CONFLICTED node cannot be installed at ANY version (emit._is_emittable already
         refuses to emit it), yet it too has no missing prerequisites and would otherwise
         look like a perfectly good root. The agent would `pip install` it forever.

    Only after both of those do prerequisites decide WAITING vs ACTIONABLE.
    """
    if node.state is State.SATISFIED:
        return SATISFIED_OK
    if in_conflict(graph, node):
        return BLOCKED
    for edge in graph.edges:
        if edge.src != node.id or not blocks(edge, target_env):
            continue
        dst = graph.get(edge.dst)
        if dst is not None and dst.state is not State.SATISFIED:
            return WAITING
    return ACTIONABLE
