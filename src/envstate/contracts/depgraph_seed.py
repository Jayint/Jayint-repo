"""Proactive, depgraph-sourced sibling of extract.promote_atomic_contracts.

Translates the certified dependency graph's obligation-bearing nodes
(Import / SystemLib / Tool) into the SAME flat atomic Contract nodes that
``promote_atomic_contracts`` emits from stderr — but sourced from the depgraph
(all of them, not only the ones that already failed), tagged with depgraph
provenance. No Blockers, no edges, no state assertions: the host still
certifies. Idempotent (skips ids already in the graph). Pure: no Docker, no
network — the DepGraph is built elsewhere and passed in.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import ids
from .nodes import Node

if TYPE_CHECKING:
    from .graph import ContractGraph
    from python_deps.depgraph.schema import DepGraph

# depgraph NodeType.value -> (contract kind, contract layer).
# Mirrors extract.py:48 layer choices; other node types are not obligations.
_KIND_BY_TYPE: dict[str, tuple[str, str]] = {
    "Import": ("python_import", "deps"),
    "SystemLib": ("system_library", "system"),
    "Tool": ("binary", "system"),
}


def seed_contracts_from_depgraph(
    graph: "ContractGraph", dep_graph: "DepGraph"
) -> list[Node]:
    """Atomic Contract nodes seeded from the depgraph's obligations.

    Returns a ``list[Node]`` (Contracts only) for ``GraphPatch(add_contracts=...)``;
    skips any contract id already present and dedupes within the pass.
    """
    out: list[Node] = []
    seen: set[str] = set()
    for node in dep_graph.nodes:
        mapping = _KIND_BY_TYPE.get(node.type.value)
        if mapping is None:
            continue
        ckind, layer = mapping
        subject = node.name
        cid = ids.contract_id(ckind, subject)
        if cid in seen or graph.has_node(cid):
            continue
        seen.add(cid)
        out.append(
            Node(
                cid,
                "Contract",
                {
                    "level": "atomic",
                    "kind": ckind,
                    "subject": subject,
                    "layer": layer,
                    "check": node.check_command or "",
                    "source_refs": [f"depgraph:{node.id}"],
                    "evidence_refs": [],
                    "description": f"{ckind} obligation: {subject}.",
                    "metadata": {},
                },
            )
        )
    return out
