"""Apply a (pre-validated) GraphPatch, returning a new immutable ContractGraph."""
from __future__ import annotations

import dataclasses

from .graph import ContractGraph
from .nodes import Edge, Node
from .patch import GraphPatch


def _edge_key(e: Edge) -> tuple[str, str, str]:
    return (e.source, e.type, e.target)


def apply_patch(graph: ContractGraph, patch: GraphPatch) -> ContractGraph:
    nodes_by_id = {n.id: n for n in graph.nodes}

    for n in patch.add_nodes:
        nodes_by_id.setdefault(n.id, n)  # dup ids rejected in validation; setdefault is belt-and-braces
    for n in patch.update_nodes:
        nodes_by_id[n.id] = n
    for nid in patch.invalidate_nodes:
        if nid in nodes_by_id:
            nodes_by_id[nid] = dataclasses.replace(nodes_by_id[nid], invalidated=True)

    invalidated_edges = {_edge_key(e) for e in patch.invalidate_edges}
    edges = tuple(
        dataclasses.replace(e, invalidated=True) if _edge_key(e) in invalidated_edges else e
        for e in graph.edges
    ) + tuple(patch.add_edges)

    return ContractGraph(
        nodes=tuple(nodes_by_id.values()),
        edges=edges,
        status_events=graph.status_events + tuple(patch.add_status_events),
    )
