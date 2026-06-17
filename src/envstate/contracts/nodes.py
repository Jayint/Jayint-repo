# src/envstate/contracts/nodes.py
"""Generic frozen graph elements + JSON (de)serialization (spec §8)."""
from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class Node:
    id: str
    type: str  # NodeType value
    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    invalidated: bool = False


@dataclasses.dataclass(frozen=True)
class Edge:
    source: str
    type: str  # EdgeType value
    target: str
    invalidated: bool = False


@dataclasses.dataclass(frozen=True)
class ContractStatusEvent:
    contract_id: str
    status: str  # ContractStatus value
    revision_id: str
    evidence_ids: tuple[str, ...] = ()
    summary: str = ""


def node_to_dict(n: Node) -> dict[str, Any]:
    out: dict[str, Any] = {"id": n.id, "type": n.type}
    out.update(dict(n.data))  # flatten data fields to top level (spec §5 shape)
    if n.invalidated:
        out["invalidated"] = True
    return out


def node_from_dict(d: dict[str, Any]) -> Node:
    data = {k: v for k, v in d.items() if k not in ("id", "type", "invalidated")}
    return Node(
        id=str(d["id"]),
        type=str(d["type"]),
        data=data,
        invalidated=bool(d.get("invalidated", False)),
    )


def edge_to_dict(e: Edge) -> dict[str, Any]:
    out: dict[str, Any] = {"source": e.source, "type": e.type, "target": e.target}
    if e.invalidated:
        out["invalidated"] = True
    return out


def edge_from_dict(d: dict[str, Any]) -> Edge:
    return Edge(
        source=str(d["source"]),
        type=str(d["type"]),
        target=str(d["target"]),
        invalidated=bool(d.get("invalidated", False)),
    )


def event_to_dict(ev: ContractStatusEvent) -> dict[str, Any]:
    return {
        "contract_id": ev.contract_id,
        "status": ev.status,
        "revision_id": ev.revision_id,
        "evidence_ids": list(ev.evidence_ids),
        "summary": ev.summary,
    }


def event_from_dict(d: dict[str, Any]) -> ContractStatusEvent:
    return ContractStatusEvent(
        contract_id=str(d["contract_id"]),
        status=str(d["status"]),
        revision_id=str(d.get("revision_id", "")),
        evidence_ids=tuple(str(x) for x in d.get("evidence_ids", [])),
        summary=str(d.get("summary", "")),
    )
