"""Domain-specific graph patch (spec §10) + tolerant parser."""
from __future__ import annotations

import dataclasses
from typing import Any

from .nodes import (
    ContractStatusEvent,
    Edge,
    Node,
    edge_from_dict,
    event_from_dict,
    node_from_dict,
)


@dataclasses.dataclass(frozen=True)
class GraphPatch:
    add_nodes: tuple[Node, ...] = ()
    update_nodes: tuple[Node, ...] = ()
    add_edges: tuple[Edge, ...] = ()
    add_status_events: tuple[ContractStatusEvent, ...] = ()
    invalidate_nodes: tuple[str, ...] = ()
    invalidate_edges: tuple[Edge, ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.add_nodes
            or self.update_nodes
            or self.add_edges
            or self.add_status_events
            or self.invalidate_nodes
            or self.invalidate_edges
        )


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def parse_graph_patch(d: Any) -> GraphPatch:
    """Parse a patch dict; tolerant of missing keys / wrong types (validate later)."""
    if not isinstance(d, dict):
        return GraphPatch()
    return GraphPatch(
        add_nodes=tuple(node_from_dict(x) for x in _as_list(d.get("add_nodes")) if isinstance(x, dict)),
        update_nodes=tuple(node_from_dict(x) for x in _as_list(d.get("update_nodes")) if isinstance(x, dict)),
        add_edges=tuple(edge_from_dict(x) for x in _as_list(d.get("add_edges")) if isinstance(x, dict)),
        add_status_events=tuple(
            event_from_dict(x) for x in _as_list(d.get("add_status_events")) if isinstance(x, dict)
        ),
        invalidate_nodes=tuple(str(x) for x in _as_list(d.get("invalidate_nodes"))),
        invalidate_edges=tuple(
            edge_from_dict(x) for x in _as_list(d.get("invalidate_edges")) if isinstance(x, dict)
        ),
    )
