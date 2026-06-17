"""Graph → planner markdown and graph → Maintainer dict (active objects only)."""
from __future__ import annotations

from typing import Any

from .goals import evaluate_goal_readiness
from .graph import ContractGraph
from .nodes import node_to_dict, edge_to_dict


def render_graph_for_planner(graph: ContractGraph) -> str:
    active = graph.active_nodes()
    if not active:
        return "## Contract Graph\n  (empty — no contracts derived yet)"

    lines: list[str] = ["## Contract Graph"]
    lines.append(f"goal_ready: {evaluate_goal_readiness(graph)}")

    contracts = [n for n in active if n.type == "Contract"]
    if contracts:
        lines.append("### contracts (id — status — description)")
        for c in contracts:
            ev = graph.latest_status(c.id)
            status = ev.status if ev else "unknown"
            level = c.data.get("level", "atomic")
            desc = c.data.get("description", "")
            lines.append(f"  - [{level}] {c.id} — {status} — {desc}")

    failures = [n for n in active if n.type == "Failure"]
    if failures:
        lines.append("### failures (id — summary)")
        for f in failures:
            lines.append(f"  - {f.id} — {f.data.get('summary', '')}")

    ops = [n for n in active if n.type == "OpenProblem"]
    if ops:
        lines.append("### open_problems (id — summary)")
        for op in ops:
            oos = " [out_of_scope]" if op.data.get("out_of_scope") else ""
            lines.append(f"  - {op.id}{oos} — {op.data.get('summary', '')}")

    transitions = [n for n in active if n.type == "Transition"]
    if transitions:
        lines.append("### transitions already proposed (id — intent)")
        for t in transitions:
            lines.append(f"  - {t.id} — {t.data.get('intent', '')}")

    return "\n".join(lines)


def serialize_graph_for_maintainer(graph: ContractGraph) -> dict[str, Any]:
    active_nodes = graph.active_nodes()
    active_ids = {n.id for n in active_nodes}
    latest: dict[str, str] = {}
    for n in active_nodes:
        if n.type == "Contract":
            ev = graph.latest_status(n.id)
            latest[n.id] = ev.status if ev else "unknown"
    return {
        "nodes": [node_to_dict(n) for n in active_nodes],
        "edges": [edge_to_dict(e) for e in graph.edges if not e.invalidated and e.source in active_ids and e.target in active_ids],
        "latest_status": latest,
    }
