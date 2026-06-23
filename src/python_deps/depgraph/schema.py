"""Typed, immutable concrete dependency graph.

This realizes the model in ``docs/DESIGN-static-probe-certified-dependency-graph.md``
section 5: one concrete node type per layer joined by ``requires`` edges, where
each node carries a host-certified ``state`` axis (the certification invariant of
section 3.1) that is separate from the ``attempts`` action axis.

Everything here is a frozen dataclass.  Every "mutation" returns a NEW object;
the originals are never changed (repo immutability rule).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace


class NodeType(enum.Enum):
    TEST = "Test"
    IMPORT = "Import"
    PACKAGE = "Package"
    SYSTEM_LIB = "SystemLib"
    TOOL = "Tool"
    RUNTIME = "Runtime"


class EdgeType(enum.Enum):
    REQUIRES = "requires"
    ALTERNATIVE_TO = "alternative_to"  # reserved; not emitted in this plan
    CONFLICTS_WITH = "conflicts_with"  # reserved; not emitted in this plan


class State(enum.Enum):
    """Certification axis. Only a host-run check_command flips this (3.1)."""

    UNKNOWN = "unknown"
    MISSING = "missing"
    SATISFIED = "satisfied"


class DiscoveredBy(enum.Enum):
    GOAL = "goal"
    STATIC_SCAN = "static_scan"
    RESOLVER = "resolver"
    PROBE = "probe"
    RUNTIME = "runtime"


class Layer(enum.Enum):
    INTERPRETER = "interpreter"
    SYSTEM = "system"
    TOOLCHAIN = "toolchain"
    PIP = "pip"
    NAMING = "naming"
    RUNTIME = "runtime"
    TESTS = "tests"


# relation -> (allowed src node-type values, allowed dst node-type values)
EDGE_RULES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "requires": (
        frozenset({"Test", "Import", "Package"}),
        frozenset({"Import", "Package", "SystemLib", "Tool", "Runtime"}),
    ),
}


@dataclass(frozen=True)
class Attempt:
    command: str
    outcome: str  # "succeeded" | "failed" | "unknown"
    check: str = ""
    cycle: int = 0

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "outcome": self.outcome,
            "check": self.check,
            "cycle": self.cycle,
        }


@dataclass(frozen=True)
class Node:
    id: str
    type: NodeType
    name: str
    layer: Layer
    discovered_by: DiscoveredBy
    state: State = State.UNKNOWN
    version: str | None = None
    check_command: str | None = None
    evidence: str | None = None
    fix_candidates: tuple[str, ...] = ()
    chosen_fix: str | None = None
    attempts: tuple[Attempt, ...] = ()
    provenance: str | None = None
    discovered_cycle: int = 0
    certified_cycle: int | None = None

    def with_state(
        self,
        state: State,
        *,
        evidence: str | None = None,
        cycle: int | None = None,
    ) -> "Node":
        """Return a NEW node with an updated certification state.

        ``evidence`` overrides only when provided; ``cycle`` (when provided)
        records the certification cycle.
        """
        changes: dict = {"state": state}
        if evidence is not None:
            changes["evidence"] = evidence
        if cycle is not None:
            changes["certified_cycle"] = cycle
        return replace(self, **changes)

    def with_attempt(self, attempt: Attempt) -> "Node":
        """Return a NEW node with ``attempt`` appended to the history."""
        return replace(self, attempts=self.attempts + (attempt,))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "layer": self.layer.value,
            "discovered_by": self.discovered_by.value,
            "state": self.state.value,
            "version": self.version,
            "check_command": self.check_command,
            "evidence": self.evidence,
            "fix_candidates": list(self.fix_candidates),
            "chosen_fix": self.chosen_fix,
            "attempts": [a.to_dict() for a in self.attempts],
            "provenance": self.provenance,
            "discovered_cycle": self.discovered_cycle,
            "certified_cycle": self.certified_cycle,
        }


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    relation: EdgeType = EdgeType.REQUIRES
    origin: str | None = None  # "scan" | "resolver" | "probe" | "runtime"

    def key(self) -> tuple[str, str, str]:
        return (self.src, self.dst, self.relation.value)

    def to_dict(self) -> dict:
        return {
            "src": self.src,
            "dst": self.dst,
            "relation": self.relation.value,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class DepGraph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()

    def get(self, node_id: str) -> Node | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def with_node(self, node: Node) -> "DepGraph":
        """Add ``node``, or replace any existing node with the same id."""
        kept = tuple(n for n in self.nodes if n.id != node.id)
        return replace(self, nodes=kept + (node,))

    def with_edge(self, edge: Edge) -> "DepGraph":
        """Add ``edge`` deduped by (src, dst, relation), validating EDGE_RULES."""
        self._validate_edge(edge)
        if any(e.key() == edge.key() for e in self.edges):
            return self
        return replace(self, edges=self.edges + (edge,))

    def _validate_edge(self, edge: Edge) -> None:
        rule = EDGE_RULES.get(edge.relation.value)
        if rule is None:
            # Reserved relations (alternative_to / conflicts_with) carry no
            # type constraints in this plan.
            return
        allowed_src, allowed_dst = rule
        src_node = self.get(edge.src)
        dst_node = self.get(edge.dst)
        if src_node is None or dst_node is None:
            raise ValueError(
                f"edge {edge.relation.value} references unknown node(s): "
                f"{edge.src!r} -> {edge.dst!r}"
            )
        if src_node.type.value not in allowed_src:
            raise ValueError(
                f"illegal {edge.relation.value} source type "
                f"{src_node.type.value!r} ({edge.src!r})"
            )
        if dst_node.type.value not in allowed_dst:
            raise ValueError(
                f"illegal {edge.relation.value} destination type "
                f"{dst_node.type.value!r} ({edge.dst!r})"
            )

    def requires_of(self, node_id: str) -> tuple[Node, ...]:
        """Successor nodes reachable from ``node_id`` via a requires edge."""
        out = []
        for edge in self.edges:
            if edge.relation is EdgeType.REQUIRES and edge.src == node_id:
                dst = self.get(edge.dst)
                if dst is not None:
                    out.append(dst)
        return tuple(out)

    def required_by(self, node_id: str) -> tuple[Node, ...]:
        """Predecessor nodes that require ``node_id`` via a requires edge."""
        out = []
        for edge in self.edges:
            if edge.relation is EdgeType.REQUIRES and edge.dst == node_id:
                src = self.get(edge.src)
                if src is not None:
                    out.append(src)
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }
