"""Contract Graph — a planner-facing reasoning layer inside WorldModelMap.

Eager package re-exports for the concise-contract-graph v2 rewrite.
"""
from __future__ import annotations

from .projection import refresh_host_graph
from .render import render_graph_for_planner, serialize_graph_for_maintainer

__all__ = [
    "refresh_host_graph",
    "render_graph_for_planner",
    "serialize_graph_for_maintainer",
]
