"""Immutable ContractGraph container + status projection + traversal."""
from __future__ import annotations

import dataclasses
from typing import Optional

from .nodes import Edge, Node, edge_from_dict, edge_to_dict, node_from_dict, node_to_dict


@dataclasses.dataclass(frozen=True)
class ContractGraph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    diagnostic_notes: tuple[str, ...] = ()

    @staticmethod
    def empty() -> "ContractGraph":
        return ContractGraph()

    def node(self, node_id: str) -> Optional[Node]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def has_node(self, node_id: str) -> bool:
        return self.node(node_id) is not None

    def active_nodes(self) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes if not n.invalidated)

    def nodes_by_type(self, t: str) -> tuple[Node, ...]:
        return tuple(n for n in self.active_nodes() if n.type == t)

    def contracts(self) -> tuple[Node, ...]:
        return self.nodes_by_type("Contract")

    def blockers(self) -> tuple[Node, ...]:
        return self.nodes_by_type("Blocker")

    def attempts(self) -> tuple[Node, ...]:
        return self.nodes_by_type("Attempt")

    def out_edges(self, source: str, edge_type: Optional[str] = None) -> tuple[Edge, ...]:
        return tuple(
            e for e in self.edges
            if not e.invalidated and e.source == source
            and (edge_type is None or e.type == edge_type)
        )

    def in_edges(self, target: str, edge_type: Optional[str] = None) -> tuple[Edge, ...]:
        return tuple(
            e for e in self.edges
            if not e.invalidated and e.target == target
            and (edge_type is None or e.type == edge_type)
        )

    def goal_contracts(self) -> tuple[Node, ...]:
        return tuple(n for n in self.contracts() if n.data.get("level") == "goal")

    def required_goal_contracts(self) -> tuple[Node, ...]:
        return tuple(n for n in self.goal_contracts() if bool(n.data.get("required", False)))

    def to_dict(self) -> dict:
        return {
            "nodes": [node_to_dict(n) for n in self.nodes],
            "edges": [edge_to_dict(e) for e in self.edges],
            "diagnostic_notes": list(self.diagnostic_notes),
        }

    @staticmethod
    def from_dict(d: dict) -> "ContractGraph":
        d = d or {}
        return ContractGraph(
            nodes=tuple(node_from_dict(x) for x in d.get("nodes", [])),
            edges=tuple(edge_from_dict(x) for x in d.get("edges", [])),
            diagnostic_notes=tuple(d.get("diagnostic_notes", [])),
        )


def _active_blocker_violates(graph: ContractGraph, contract_id: str) -> bool:
    for e in graph.in_edges(contract_id, "violates"):
        b = graph.node(e.source)
        if b is not None and not b.invalidated and bool(b.data.get("active", True)):
            return True
    return False


def project_status(graph: ContractGraph, contract_id: str, host_satisfied: frozenset) -> str:
    if contract_id in host_satisfied:
        return "satisfied"
    if _active_blocker_violates(graph, contract_id):
        return "violated"
    return "unknown"


def depends_on_closure(graph: ContractGraph, goal_id: str) -> tuple[str, ...]:
    seen: set[str] = set()
    stack = [goal_id]
    out: list[str] = []
    while stack:
        cur = stack.pop()
        for e in graph.out_edges(cur, "depends_on"):
            if e.target not in seen:
                seen.add(e.target)
                out.append(e.target)
                stack.append(e.target)
    return tuple(out)


def root_blockers(graph: ContractGraph) -> tuple[Node, ...]:
    active = [b for b in graph.blockers() if bool(b.data.get("active", True))]
    return tuple(
        sorted(active, key=lambda b: 0 if b.data.get("root_or_downstream") == "root" else 1)
    )


def frontier_by_layer(graph: ContractGraph, host_satisfied: frozenset) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for c in graph.contracts():
        if project_status(graph, c.id, host_satisfied) != "satisfied":
            out.setdefault(c.data.get("layer", "deps"), []).append(c.id)
    return {k: tuple(v) for k, v in out.items()}


def goal_ready(graph: ContractGraph, host_satisfied: frozenset) -> bool:
    required = graph.required_goal_contracts()
    if not required:
        return False
    for goal in required:
        if project_status(graph, goal.id, host_satisfied) != "satisfied":
            return False
        for dep in depends_on_closure(graph, goal.id):
            if project_status(graph, dep, host_satisfied) != "satisfied":
                return False
    return True
