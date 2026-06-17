"""Validate a GraphPatch against a graph + ownership scope (spec §10)."""
from __future__ import annotations

from .graph import ContractGraph
from .patch import GraphPatch
from .schema import (
    EDGE_RULES,
    HOST_OWNED_NODE_TYPES,
    MAINTAINER_NODE_TYPES,
    VALID_NODE_TYPES,
    VALID_STATUSES,
)


def _node_type_index(graph: ContractGraph, patch: GraphPatch) -> dict[str, str]:
    """Type of every node visible after the patch (existing + added)."""
    index = {n.id: n.type for n in graph.nodes}
    for n in list(patch.add_nodes) + list(patch.update_nodes):
        index[n.id] = n.type
    return index


def _command_passed(graph: ContractGraph, patch: GraphPatch, node_id: str) -> bool:
    for n in list(graph.nodes) + list(patch.add_nodes):
        if n.id == node_id and n.type == "CommandExecution":
            return int(n.data.get("exit_code", 1)) == 0
    return False


def validate_patch(graph: ContractGraph, patch: GraphPatch, *, scope: str) -> list[str]:
    """Return a list of human-readable errors; empty list == valid."""
    errors: list[str] = []
    existing_ids = {n.id for n in graph.nodes}
    new_ids: set[str] = set()

    # --- node-level checks ---
    for n in patch.add_nodes:
        if n.type not in VALID_NODE_TYPES:
            errors.append(f"unknown node type {n.type!r} for {n.id}")
        if n.id in existing_ids or n.id in new_ids:
            errors.append(f"duplicate node id {n.id!r}")
        new_ids.add(n.id)
        if scope == "maintainer" and n.type in HOST_OWNED_NODE_TYPES:
            errors.append(f"maintainer may not create host-owned node {n.type!r} ({n.id})")
        if scope == "maintainer" and n.type not in MAINTAINER_NODE_TYPES:
            errors.append(f"maintainer node type {n.type!r} not allowed ({n.id})")

    type_index = _node_type_index(graph, patch)

    # --- edge-level checks ---
    for e in patch.add_edges:
        if e.type not in EDGE_RULES:
            errors.append(f"unknown edge type {e.type!r}")
            continue
        if e.source not in type_index or e.target not in type_index:
            errors.append(f"edge endpoint missing: {e.source} -{e.type}-> {e.target}")
            continue
        allowed_src, allowed_tgt = EDGE_RULES[e.type]
        if type_index[e.source] not in allowed_src or type_index[e.target] not in allowed_tgt:
            errors.append(
                f"edge type {e.type!r} not allowed between "
                f"{type_index[e.source]} and {type_index[e.target]}"
            )

    # --- status-event checks ---
    for ev in patch.add_status_events:
        if ev.status not in VALID_STATUSES:
            errors.append(f"invalid status {ev.status!r} for {ev.contract_id}")
        if ev.contract_id not in type_index:
            errors.append(f"status event for unknown contract {ev.contract_id!r}")
        for eid in ev.evidence_ids:
            if eid not in type_index:
                errors.append(f"status evidence id {eid!r} points to no node")
        # spec §7 rule 4: satisfied requires passing command / confirmed validator evidence
        if ev.status == "satisfied":
            ok = any(_command_passed(graph, patch, eid) for eid in ev.evidence_ids)
            if not ok:
                errors.append(
                    f"contract {ev.contract_id!r} marked satisfied without passing command evidence"
                )

    # --- structural grounding (spec §10) ---
    declared_reqs = {
        e.target for e in (list(graph.edges) + list(patch.add_edges)) if e.type == "declares" and not e.invalidated
    }
    for n in patch.add_nodes:
        if n.type == "Requirement" and n.id not in declared_reqs:
            errors.append(f"requirement {n.id!r} has no RepoArtifact declares edge")
    all_edges = list(graph.edges) + list(patch.add_edges)
    transition_targets = {
        e.source for e in all_edges if e.type == "targets" and not e.invalidated
    }
    # A transition that is the repair target of a contract (repaired_by edge) is also
    # considered connected even without an explicit targets out-edge.
    repaired_transitions = {
        e.target for e in all_edges if e.type == "repaired_by" and not e.invalidated
    }
    for n in patch.add_nodes:
        if n.type == "Transition" and n.id not in transition_targets and n.id not in repaired_transitions:
            errors.append(f"transition {n.id!r} targets no Contract/Failure/OpenProblem")

    return errors
