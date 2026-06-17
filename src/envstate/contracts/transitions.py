# src/envstate/contracts/transitions.py
"""Host-side transition commit + executed_as linking (from planner proposals + ledger)."""
from __future__ import annotations

from . import ids
from .graph import ContractGraph
from .nodes import Edge, Node
from .patch import GraphPatch


def commit_transition_patch(graph: ContractGraph, proposal, target_node_ids) -> GraphPatch:
    tid = ids.transition_id(proposal.kind, ids.slug(proposal.target) or proposal.target)
    existing_edges = {(e.source, e.type, e.target) for e in graph.edges}
    nodes = []
    if not graph.has_node(tid):
        nodes.append(
            Node(tid, "Transition", {
                "kind": proposal.kind, "target": proposal.target, "intent": proposal.intent,
                "command_templates": list(proposal.command_templates),
            })
        )
    edges = []
    for tgt in target_node_ids:
        node = graph.node(tgt)
        if node is None or node.invalidated:
            continue  # only ground against real nodes
        if (tid, "targets", tgt) not in existing_edges:
            edges.append(Edge(tid, "targets", tgt))
        if node.type == "Contract" and (tgt, "repaired_by", tid) not in existing_edges:
            edges.append(Edge(tgt, "repaired_by", tid))
    return GraphPatch(add_nodes=tuple(nodes), add_edges=tuple(edges))


def executed_as_patch(graph: ContractGraph, transition_id: str, command_steps) -> GraphPatch:
    existing = {(e.source, e.type, e.target) for e in graph.edges}
    edges = []
    for step in command_steps:
        cmd_id = ids.command_id(step)
        if graph.has_node(cmd_id) and (transition_id, "executed_as", cmd_id) not in existing:
            edges.append(Edge(transition_id, "executed_as", cmd_id))
    return GraphPatch(add_edges=tuple(edges))
