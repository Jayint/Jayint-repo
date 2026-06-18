"""Graph → planner markdown and graph → Maintainer dict (active objects only)."""
from __future__ import annotations

from typing import Any

from .graph import ContractGraph, frontier_by_layer, project_status, root_blockers
from .nodes import edge_to_dict, node_to_dict


def render_graph_for_planner(graph: ContractGraph, host_satisfied: frozenset[str]) -> str:
    """Render three-section markdown for the planner: Repair Map + Repair Frontier."""
    lines: list[str] = []

    # --- Repair Map ---
    lines.append("## Repair Map")
    required_goals = graph.required_goal_contracts()
    if required_goals:
        lines.append("### Required Goals")
        for goal in required_goals:
            status = project_status(graph, goal.id, host_satisfied)
            lines.append(f"  - {goal.id} — {status}")
            # list active blockers violating this goal's deps
            from .graph import depends_on_closure
            deps = depends_on_closure(graph, goal.id)
            for dep_id in deps:
                dep_status = project_status(graph, dep_id, host_satisfied)
                if dep_status == "violated":
                    for e in graph.in_edges(dep_id, "violates"):
                        b = graph.node(e.source)
                        if b is not None and not b.invalidated and bool(b.data.get("active", True)):
                            lines.append(
                                f"    - blocker: {b.id} — {b.data.get('summary', '')} "
                                f"[{b.data.get('root_or_downstream', 'unknown')}]"
                            )

    active_blockers = root_blockers(graph)
    if active_blockers:
        lines.append("### Active Blockers (root-first)")
        for b in active_blockers:
            lines.append(f"  - {b.id} — {b.data.get('summary', '')} [{b.data.get('root_or_downstream', 'unknown')}]")

    attempts = graph.attempts()
    if attempts:
        lines.append("### Recent Attempts (id — outcome — intent)")
        for a in attempts:
            outcome = a.data.get("outcome", "pending")
            intent = a.data.get("intent", "")
            lines.append(f"  - {a.id} — {outcome} — {intent}")

    # --- Repair Frontier ---
    lines.append("## Repair Frontier")
    frontier = frontier_by_layer(graph, host_satisfied)
    if frontier:
        lines.append("### Unsatisfied Contracts by Layer")
        for layer, contract_ids in sorted(frontier.items()):
            lines.append(f"  [{layer}]")
            for cid in contract_ids:
                status = project_status(graph, cid, host_satisfied)
                lines.append(f"    - {cid} — {status}")

    rb = root_blockers(graph)
    if rb:
        lines.append("### Root Blockers")
        for b in rb:
            if b.data.get("root_or_downstream") == "root":
                lines.append(f"  - {b.id} — {b.data.get('summary', '')}")

    # --- Recent Diagnoses ---
    # The Maintainer's rolling NL advisories (root-cause hypotheses, what a repair
    # changed, what to try next).  Stored capped on the graph; surfaced here so the
    # planner actually sees them instead of them dead-ending in graph state.
    notes = graph.diagnostic_notes
    if notes:
        lines.append("## Recent Diagnoses (Maintainer advisories — most recent last)")
        for note in notes:
            lines.append(f"  - {note}")

    return "\n".join(lines)


def serialize_graph_for_maintainer(graph: ContractGraph) -> dict[str, Any]:
    """Return active Contract/Blocker/Attempt dicts + active edges, no status_events."""
    active_ids = {n.id for n in graph.active_nodes()}
    contracts = [node_to_dict(n) for n in graph.contracts()]
    blockers = [node_to_dict(n) for n in graph.blockers()]
    attempts = [node_to_dict(n) for n in graph.attempts()]
    edges = [
        edge_to_dict(e)
        for e in graph.edges
        if not e.invalidated
        and e.source in active_ids
        and e.target in active_ids
    ]
    return {
        "contracts": contracts,
        "blockers": blockers,
        "attempts": attempts,
        "edges": edges,
    }
