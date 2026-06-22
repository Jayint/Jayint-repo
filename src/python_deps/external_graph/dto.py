"""ExternalDependencyGraphSlice DTO: sorting helpers + is_empty + to_dict.

Methods to_flat_hint and trim_to_budget delegate to rendering.py and budget.py
respectively (lazy import inside method body to avoid circular imports).

See docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md for the full design.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Node cap and edge cap — identical for both flat and graph rendering.
_MAX_NODES = 40
_MAX_EDGES = 60
_BUDGET_BYTES = 16384  # safety net ABOVE the max-capped size (~12 KB for 40 nodes/60 edges).
# Node/edge caps (above) are the primary size control; this byte budget is a backstop so a
# fully-capped graph fits WITHOUT trimming away its structural edges. A 4 KB budget could not
# hold 40 nodes, so trim dropped all edges first and destroyed the graph's structure.


def _sorted_nodes(nodes: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """Sort nodes deterministically by id."""
    return tuple(sorted(nodes, key=lambda n: str(n.get("id", ""))))


def _public_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return nodes with all '_'-prefixed internal keys stripped.

    This produces the same representation as ``to_dict()`` for nodes:
    only public keys (id, kind, state, specifier, …) are kept.  Internal
    keys such as ``_priority`` are used only inside ``trim_to_budget`` for
    drop-order decisions and must NOT contribute to the budget measurement
    because they are stripped before the payload is sent to the LLM.

    Using this helper in all drop-loop serializations ensures the budget is
    measured against exactly the bytes ``to_dict()`` emits — no over-trimming.
    """
    return [{k: v for k, v in n.items() if not k.startswith("_")} for n in nodes]


def _sorted_edges(edges: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """Sort edges deterministically by (src, kind, dst)."""
    return tuple(sorted(
        edges,
        key=lambda e: (str(e.get("src", "")), str(e.get("kind", "")), str(e.get("dst", ""))),
    ))


@dataclass(frozen=True)
class ExternalDependencyGraphSlice:
    """Prompt-facing external dependency graph slice (pure DTO, not a graph engine).

    Invariants:
    - nodes: tuple of node dicts, sorted by id, ≤ 40
    - edges: tuple of edge dicts, sorted by (src, kind, dst), ≤ 60
    - frontier: read-only mapping (conflict frontier, missing imports, transactions, etc.)
    - provenance: tuple of provenance/drop-note dicts

    Construct with raw (unsorted) tuples; sorting and validation happen in __post_init__.
    """

    nodes: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    edges: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    frontier: Mapping[str, Any] = field(default_factory=dict)
    provenance: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Sort for determinism
        object.__setattr__(self, "nodes", _sorted_nodes(self.nodes))
        object.__setattr__(self, "edges", _sorted_edges(self.edges))
        # Validate caps
        if len(self.nodes) > _MAX_NODES:
            raise ValueError(
                f"node cap exceeded: {len(self.nodes)} > {_MAX_NODES}. "
                "Trim before constructing ExternalDependencyGraphSlice."
            )
        if len(self.edges) > _MAX_EDGES:
            raise ValueError(
                f"edge cap exceeded: {len(self.edges)} > {_MAX_EDGES}. "
                "Trim before constructing ExternalDependencyGraphSlice."
            )

    # ------------------------------------------------------------------
    # Core predicates
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """True when the slice carries no nodes, no edges, and no renderable frontier data.

        Consistent with to_flat_hint(): a frontier dict whose every value is an empty
        list (or empty string) produces no bullets in the flat hint, so is_empty()
        returns True in that case too.  Callers should not assume 'not is_empty()' implies
        that 'to_flat_hint() != ""' — they are identical when this invariant holds.
        """
        if self.nodes or self.edges:
            return False
        # No nodes, no edges — check whether any frontier value would actually render.
        if not self.frontier:
            return True
        return not any(
            (isinstance(v, (list, tuple)) and len(v) > 0)
            or (not isinstance(v, (list, tuple)) and v is not None and v != "")
            for v in self.frontier.values()
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain JSON-serializable dict (lists, not tuples).

        Internal/private keys (starting with '_') are stripped from node dicts
        so they do not appear in the LLM-facing prompt payload.  Internal keys
        such as ``_priority`` are used only inside trim_to_budget and must not
        pollute the structured graph JSON the LLM sees.
        """
        return {
            "nodes": [
                {k: v for k, v in n.items() if not k.startswith("_")}
                for n in self.nodes
            ],
            "edges": list(self.edges),
            "frontier": dict(self.frontier),
            "provenance": list(self.provenance),
        }

    def to_flat_hint(self) -> str:
        """Lossless linearization: one bullet per node, per edge, plus frontier bullets.

        The flat rendering intentionally carries the same information as to_dict():
        every node id and every edge (src, kind, dst) must appear. No JSON braces or
        nested structure; every non-empty line starts with '- '.

        Delegates to rendering.to_flat_hint_impl().
        """
        from .rendering import to_flat_hint_impl
        return to_flat_hint_impl(self)

    # ------------------------------------------------------------------
    # Budget trimming
    # ------------------------------------------------------------------

    def trim_to_budget(
        self, *, budget_bytes: int = _BUDGET_BYTES
    ) -> "ExternalDependencyGraphSlice":
        """Return a slice trimmed to fit within budget_bytes when serialized.

        Drop order (frontier-distant first):
        1. Edges are dropped from the end first (cheapest to lose).
        2. Nodes are then dropped from the end while still over budget.

        Nodes are re-sorted before trimming so that frontier-closest nodes survive.
        A drop note is always logged in provenance; there is no silent truncation.

        Delegates to budget.trim_to_budget_impl().
        """
        from .budget import trim_to_budget_impl
        return trim_to_budget_impl(self, budget_bytes=budget_bytes)
