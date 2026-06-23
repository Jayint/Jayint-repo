"""Deterministic Maintainer (Change B): blocker extraction + done-gate, no LLM.

Replaces the LLM Maintainer's graph-patch step with verbatim-signature blockers
carrying the correct layer, so the existing auto-resolve machinery
(_auto_resolve_blockers / _auto_resolve_system_problems) fires after a fix.
Attempts/outcomes are NOT handled here — the orchestrator already does that.
"""
from __future__ import annotations

from .contracts import ids
from .contracts.extract import _RULES
from .contracts.graph import ContractGraph
from .contracts.nodes import Edge, Node
from .contracts.patch import GraphPatch
from .world_model import TaskReport

# blocker kind (from extract._RULES) -> contract kind
_CONTRACT_KIND = {
    "module_not_found": "python_import",
    "missing_binary": "binary",
    "missing_system_library": "system_library",
}
# contract kind -> obligation layer (mirrors extract.py:48)
_LAYER = {"python_import": "deps", "binary": "system", "system_library": "system"}


def _extract(line: str) -> tuple[str | None, str, str]:
    """Return (subject, blocker_kind, matched_text) for the first rule that fires.

    ``matched_text`` is the regex group(0) — the verbatim portion of the line
    that triggered the rule.  Used as the canonical blocker signature / id so
    the id is stable regardless of surrounding context (e.g. "Error: pg_config:
    command not found" → matched_text = "pg_config: command not found").
    """
    for pat, kind, _ in _RULES:
        m = pat.search(line)
        if m:
            return m.group(1), kind, m.group(0)
    return None, "unknown", ""


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
            subject, bkind, sig = _extract(line)
            ckind = _CONTRACT_KIND.get(bkind)
            if subject is None or ckind is None:
                continue
            layer = _LAYER[ckind]
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
