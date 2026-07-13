"""Graph context for the react arm (spec Rev 3.3 §6). Pure — no Docker, no network.

This module owns EVERY rule about what an edge means. Nothing downstream of
``verdict()`` touches an edge attribute. Today those rules are smeared across
``emit._toolchain_ready`` (soft), ``emit._conflicted_ids``/``_is_emittable``
(conflicts), and ``resolve_lock``'s marker pruning (markers); the arm gets ONE
copy, unit-tested against hand-built graphs.
"""
from __future__ import annotations

import logging

from python_deps.depgraph.schema import DepGraph, Edge, EdgeType, Node, NodeType, State

logger = logging.getLogger(__name__)

ACTIONABLE = "ACTIONABLE"      # MISSING, nothing missing beneath it -> the agent acts HERE
WAITING = "WAITING"            # a hard prerequisite is missing -> fix that first
BLOCKED = "BLOCKED"            # in a version conflict -> NO install will ever work
SATISFIED_OK = "SATISFIED_OK"  # already fine -> nothing to do, and NO record
UNCERTIFIED = "UNCERTIFIED"    # UNKNOWN state -> a root CANDIDATE, never a confirmed one


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


def blocks(graph: DepGraph, edge: Edge, target_env: dict | None = None) -> bool:
    """Does this edge currently BLOCK its source node from being installed?

    This mirrors `emit._toolchain_ready` (emit.py:63-81), which is the incumbent authority:
    the build-script renderer already decides what may be emitted, and if the arm reached a
    different conclusion we would tell the agent to fix something the renderer would happily
    have installed — a wasted turn, and every turn is a full container rebuild.

    An edge is not a blocker when:
      * it is not a REQUIRES edge (CONFLICTS_WITH is a constraint, not a need);
      * it is SOFT -- emit.py:69-70, "soft requires edges never block (invariant #10)";
      * its environment marker does not hold for the target (resolve_lock.py:442-451);
      * its target is already SATISFIED.

    And then DEPENDENCY TYPE decides, exactly as `_toolchain_ready` decides it:

      SystemLib  blocks ALWAYS. A wheel dlopens a runtime .so just as a source build links
                 against it, so a missing SystemLib defeats both.
      Tool       blocks a SOURCE build and an UNKNOWN build mode -- but NOT a known wheel
                 (`build_from_source is False`). A wheel needs no compiler, so telling the
                 agent to apt-get a build tool it will never invoke is a wasted rebuild.
      Package    never blocks: `pip install X` resolves and installs X's own dependencies.
                 (emit does not gate on these either -- it topologically orders them instead.)
      Config /   never blocks: those edges are SOFT by construction (the LLM's Config/Service
      Service    edges), so they are already excluded above.

    Taking the source node's build mode into account is why this needs the GRAPH and not just
    the edge: an edge alone cannot answer "is my owner a wheel?".
    """
    if edge.relation is not EdgeType.REQUIRES:
        return False
    if not (edge.data or {}).get("hard", True):
        return False
    if not _marker_holds(edge, target_env):
        return False
    dep = graph.get(edge.dst)
    if dep is None or dep.state is State.SATISFIED:
        return False
    if dep.type is NodeType.SYSTEM_LIB:
        return True
    if dep.type is NodeType.TOOL:
        src = graph.get(edge.src)
        return src is None or src.build_from_source is not False
    return False


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
    """SATISFIED_OK | BLOCKED | UNCERTIFIED | WAITING | ACTIONABLE — the node's decision state.

    Order matters, and every early return is load-bearing:

      1. A SATISFIED node is DONE. Checking prerequisites first would call a satisfied leaf
         ACTIONABLE (it has no MISSING prerequisites, after all) and hand it a "fix this"
         record — telling the agent to install something that is already installed.
      2. A CONFLICTED node cannot be installed at ANY version (emit._is_emittable already
         refuses to emit it), yet it too has no missing prerequisites and would otherwise
         look like a perfectly good root. The agent would `pip install` it forever.
      3. An UNKNOWN node was never certified against the container — typically because it has
         no `check_command`. `emit._is_emittable` refuses every non-MISSING node for exactly
         this reason. It may be a root CANDIDATE; presenting it as a confirmed one would be
         passing off a guess as a measurement (spec §6.4: "UNKNOWN never masquerades as
         MISSING").

    Only after all three do prerequisites decide WAITING vs ACTIONABLE.
    """
    if node.state is State.SATISFIED:
        return SATISFIED_OK
    if in_conflict(graph, node):
        return BLOCKED
    if node.state is not State.MISSING:
        return UNCERTIFIED
    for edge in graph.edges:
        if edge.src == node.id and blocks(graph, edge, target_env):
            return WAITING
    return ACTIONABLE
