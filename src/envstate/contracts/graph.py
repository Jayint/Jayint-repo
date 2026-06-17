"""Immutable ContractGraph container + query helpers + JSON serialization."""
from __future__ import annotations

import dataclasses
from typing import Any, Optional

from .nodes import (
    ContractStatusEvent,
    Edge,
    Node,
    edge_from_dict,
    edge_to_dict,
    event_from_dict,
    event_to_dict,
    node_from_dict,
    node_to_dict,
)


@dataclasses.dataclass(frozen=True)
class ContractGraph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    status_events: tuple[ContractStatusEvent, ...] = ()

    @staticmethod
    def empty() -> "ContractGraph":
        return ContractGraph()

    # ---- queries (all ignore invalidated unless noted) -------------------
    def node(self, node_id: str) -> Optional[Node]:
        for n in self.nodes:  # includes invalidated; callers filter if needed
            if n.id == node_id:
                return n
        return None

    def has_node(self, node_id: str) -> bool:
        return self.node(node_id) is not None

    def active_nodes(self) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes if not n.invalidated)

    def nodes_by_type(self, node_type: str) -> tuple[Node, ...]:
        return tuple(n for n in self.active_nodes() if n.type == node_type)

    def out_edges(self, source: str, edge_type: Optional[str] = None) -> tuple[Edge, ...]:
        return tuple(
            e
            for e in self.edges
            if not e.invalidated and e.source == source and (edge_type is None or e.type == edge_type)
        )

    def in_edges(self, target: str, edge_type: Optional[str] = None) -> tuple[Edge, ...]:
        return tuple(
            e
            for e in self.edges
            if not e.invalidated and e.target == target and (edge_type is None or e.type == edge_type)
        )

    def latest_status(self, contract_id: str) -> Optional[ContractStatusEvent]:
        last: Optional[ContractStatusEvent] = None
        for ev in self.status_events:  # append-only; last wins
            if ev.contract_id == contract_id:
                last = ev
        return last

    def goal_contracts(self) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes_by_type("Contract") if n.data.get("level") == "goal")

    def required_goal_contracts(self) -> tuple[Node, ...]:
        return tuple(n for n in self.goal_contracts() if bool(n.data.get("required", False)))

    # ---- serialization ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node_to_dict(n) for n in self.nodes],
            "edges": [edge_to_dict(e) for e in self.edges],
            "contract_status_events": [event_to_dict(ev) for ev in self.status_events],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ContractGraph":
        d = d or {}
        return ContractGraph(
            nodes=tuple(node_from_dict(x) for x in d.get("nodes", [])),
            edges=tuple(edge_from_dict(x) for x in d.get("edges", [])),
            status_events=tuple(event_from_dict(x) for x in d.get("contract_status_events", [])),
        )
