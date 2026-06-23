"""Deterministic Maintainer (Change B): blocker extraction + done-gate, no LLM.

Replaces the LLM Maintainer's graph-patch step with verbatim-signature blockers
carrying the correct layer, so the existing auto-resolve machinery
(_auto_resolve_blockers / _auto_resolve_system_problems) fires after a fix.
Attempts/outcomes are NOT handled here — the orchestrator already does that.
"""
from __future__ import annotations

from .contracts import ids
from .contracts.extract import CONTRACT_LAYERS, extract_blocker_match
from .contracts.graph import ContractGraph
from .contracts.nodes import Edge, Node
from .contracts.patch import GraphPatch
from .world_model import TaskReport


def build_blocker_patch(graph: ContractGraph, report: TaskReport) -> GraphPatch:
    """A scope='host' patch of Contract + Blocker + violates for each failure
    signature in the report's command output. Idempotent vs the graph."""
    contracts: list[Node] = []
    blockers: list[Node] = []
    edges: list[Edge] = []
    seen: set[str] = set()

    for rec in report.commands:
        for raw in (rec.output or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            match = extract_blocker_match(line)
            if match is None:
                continue
            subject, bkind, ckind, sig = match
            layer = CONTRACT_LAYERS[ckind]
            cid = ids.contract_id(ckind, subject)
            bid = ids.blocker_id(sig)
            if cid not in seen and not graph.has_node(cid):
                contracts.append(Node(cid, "Contract", {
                    "level": "atomic", "kind": ckind, "subject": subject, "layer": layer,
                    "check": "", "source_refs": [f"signature:{sig[:60]}"],
                    "evidence_refs": [], "description": f"{ckind} obligation: {subject}.",
                    "metadata": {},
                }))
            if bid not in seen and not graph.has_node(bid):
                blockers.append(Node(bid, "Blocker", {
                    "signature": sig, "kind": bkind, "layer": layer, "subject": subject,
                    "summary": f"{bkind}: {subject}", "root_or_downstream": "root",
                    "active": True, "evidence_refs": [], "metadata": {},
                }))
                edges.append(Edge(bid, "violates", cid))
            seen.add(cid)
            seen.add(bid)

    notes = (report.learning,) if (report.learning or "").strip() else ()
    return GraphPatch(
        add_contracts=tuple(contracts),
        add_blockers=tuple(blockers),
        add_edges=tuple(edges),
        diagnostic_notes=notes,
    )
