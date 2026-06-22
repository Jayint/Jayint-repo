"""Budget-trimming for ExternalDependencyGraphSlice.

The main implementation is in trim_to_budget_impl(); the method
ExternalDependencyGraphSlice.trim_to_budget() delegates here.

See docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md for the full design.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .dto import _BUDGET_BYTES, _public_nodes

if TYPE_CHECKING:
    from .dto import ExternalDependencyGraphSlice


def trim_to_budget_impl(
    slice_: "ExternalDependencyGraphSlice",
    *,
    budget_bytes: int = _BUDGET_BYTES,
) -> "ExternalDependencyGraphSlice":
    """Return a slice trimmed to fit within budget_bytes when serialized.

    Drop order (frontier-distant first):
    1. Edges are dropped from the end first (cheapest to lose).
    2. Nodes are then dropped from the end while still over budget.

    Nodes are re-sorted before trimming so that frontier-closest nodes survive:
    - If any node carries a ``_priority`` key, nodes are sorted by
      (_priority, id) ascending so that the lowest _priority (= most important,
      frontier-closest) sorts first and is kept.
    - If no node has a ``_priority`` key, the existing alphabetical-by-id order
      is preserved. In this case the builder is responsible for assigning
      lexicographically-early ids to frontier-adjacent nodes so that
      alphabetical order equals frontier proximity.

    **Frontier-data budget limitation:** The frontier Mapping is frozen and is
    not trimmed.  When frontier data alone exceeds ``budget_bytes`` (after all
    nodes and edges have been dropped), the budget guarantee cannot be met.  In
    that case the method returns a best-effort trim (all nodes and edges dropped)
    and records a ``budget_exceeded`` provenance event noting the shortfall.
    Tasks 3-4 must keep frontier data compact; cap the number of items in each
    frontier list at construction time to prevent this path.

    A drop note is always logged in provenance; there is no silent truncation.
    """
    from .dto import ExternalDependencyGraphSlice

    current = slice_.to_dict()
    serialized = json.dumps(current).encode()
    if len(serialized) <= budget_bytes:
        return slice_  # already within budget

    # Rebuild mutable lists for trimming
    # Re-sort nodes by (_priority, id) so priority-aware drop order is correct.
    nodes: list[dict[str, Any]] = list(slice_.nodes)
    if any("_priority" in n for n in nodes):
        nodes.sort(key=lambda n: (n.get("_priority", 0), str(n.get("id", ""))))

    edges: list[dict[str, Any]] = list(slice_.edges)
    provenance: list[dict[str, Any]] = list(slice_.provenance)

    dropped_nodes = 0
    dropped_edges = 0

    # Drop edges first (cheaper to lose) from the end (frontier-distant).
    # IMPORTANT: measure the PUBLIC (stripped) form of nodes in ALL drop-loop
    # serializations so that internal keys like _priority do not inflate the
    # measurement and cause over-trimming.  _priority keys are stripped by
    # to_dict() before the payload reaches the LLM; the budget must be measured
    # against exactly those bytes.
    while edges:
        payload = json.dumps({
            "nodes": _public_nodes(nodes),
            "edges": edges,
            "frontier": dict(slice_.frontier),
            "provenance": provenance,
        }).encode()
        if len(payload) <= budget_bytes:
            break
        edges.pop()
        dropped_edges += 1

    # If still over budget, drop nodes from the end
    while nodes:
        payload = json.dumps({
            "nodes": _public_nodes(nodes),
            "edges": edges,
            "frontier": dict(slice_.frontier),
            "provenance": provenance,
        }).encode()
        if len(payload) <= budget_bytes:
            break
        nodes.pop()
        dropped_nodes += 1

    drop_note: dict[str, Any] = {
        "event": "budget_trim",
        "note": (
            f"dropped {dropped_nodes} node(s) and {dropped_edges} edge(s) "
            f"to fit within {budget_bytes}-byte payload budget"
        ),
        "dropped_nodes": dropped_nodes,
        "dropped_edges": dropped_edges,
    }

    # Account for the provenance note itself in the budget
    test_provenance = list(provenance) + [drop_note]
    while nodes:
        payload = json.dumps({
            "nodes": _public_nodes(nodes),
            "edges": edges,
            "frontier": dict(slice_.frontier),
            "provenance": test_provenance,
        }).encode()
        if len(payload) <= budget_bytes:
            break
        nodes.pop()
        dropped_nodes += 1
        drop_note = {
            "event": "budget_trim",
            "note": (
                f"dropped {dropped_nodes} node(s) and {dropped_edges} edge(s) "
                f"to fit within {budget_bytes}-byte payload budget"
            ),
            "dropped_nodes": dropped_nodes,
            "dropped_edges": dropped_edges,
        }
        test_provenance = list(provenance) + [drop_note]

    provenance = test_provenance

    # Check if the payload still exceeds budget after all nodes and edges have been
    # dropped (the frontier Mapping is frozen and cannot be trimmed further).
    # When this happens the budget guarantee cannot be met; record a budget_exceeded
    # event so callers can detect the shortfall.
    #
    # Distinguish two sub-cases for a correct diagnostic message:
    #  (a) Frontier data alone pushes the total over budget — frontier is the culprit.
    #  (b) The frontier is empty/tiny and provenance notes themselves are the culprit.
    final_payload = json.dumps({
        "nodes": _public_nodes(nodes),
        "edges": edges,
        "frontier": dict(slice_.frontier),
        "provenance": list(provenance),
    }).encode()
    if len(final_payload) > budget_bytes:
        actual_size = len(final_payload)
        # Determine which component is responsible: frontier or provenance notes.
        frontier_only_size = len(json.dumps({
            "nodes": [], "edges": [],
            "frontier": dict(slice_.frontier),
            "provenance": [],
        }).encode())
        if frontier_only_size > budget_bytes:
            culprit_desc = (
                f"frontier data alone ({frontier_only_size} bytes) exceeds budget "
                f"({budget_bytes} bytes)"
            )
        else:
            culprit_desc = (
                f"provenance notes alone ({actual_size} bytes total) exceed budget "
                f"({budget_bytes} bytes) after frontier ({frontier_only_size} bytes) "
                "was accounted for"
            )
        exceeded_note: dict[str, Any] = {
            "event": "budget_exceeded",
            "note": (
                f"{culprit_desc}; cannot trim further — all nodes and "
                "edges have been dropped. Cap frontier list sizes at construction time."
            ),
            "actual_bytes": actual_size,
            "budget_bytes": budget_bytes,
        }
        provenance = list(provenance) + [exceeded_note]

    # Reconstruct — sort and validate (trimmed counts are within caps)
    return ExternalDependencyGraphSlice(
        nodes=tuple(nodes),
        edges=tuple(edges),
        frontier=slice_.frontier,
        provenance=tuple(provenance),
    )
