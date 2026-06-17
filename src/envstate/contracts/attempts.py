"""Host-created Attempt nodes + addresses-edge wiring (replaces transitions.py)."""
from __future__ import annotations

from .graph import ContractGraph
from .nodes import Edge, Node
from .patch import GraphPatch
from . import ids


def attempt_node(step: object, proposed_by: str) -> Node:
    """Build an Attempt Node from a RecipeStep (or duck-typed equivalent)."""
    key = str(getattr(step, "id", "")) + ":" + str(getattr(step, "command", ""))[:20]
    node_id = ids.attempt_id(key)
    data = {
        "intent": str(getattr(step, "id", "")),
        "kind": str(getattr(step, "kind", "")),
        "proposed_by": proposed_by,
        "commands": [str(getattr(step, "command", ""))],
        "outcome": "pending",
        "outcome_reason": "",
        "evidence_refs": [],
        "created_from_target_node_ids": list(getattr(step, "target_node_ids", ())),
        "metadata": {},
    }
    return Node(node_id, "Attempt", data)


def commit_attempt(graph: ContractGraph, step: object, proposed_by: str) -> GraphPatch:
    """Return a GraphPatch that adds an Attempt node and one addresses-edge per existing target."""
    node = attempt_node(step, proposed_by)
    edges: list[Edge] = []
    for target_id in getattr(step, "target_node_ids", ()):
        if graph.has_node(target_id):
            edges.append(Edge(node.id, "addresses", target_id))
    return GraphPatch(
        add_attempts=(node,),
        add_edges=tuple(edges),
    )
