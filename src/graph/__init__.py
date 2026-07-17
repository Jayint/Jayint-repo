"""Concrete, host-certified dependency graph package.

Realizes the model in ``docs/DESIGN-static-probe-certified-dependency-graph.md``
section 5.  See ``schema`` for the typed immutable graph, ``executor`` for the
command interface, ``ids`` for node-id constructors and ``tables`` for curated
Debian/Ubuntu provider mappings.
"""

from __future__ import annotations

from graph.build import build_dep_graph
from graph.certify import certify, certify_all
from graph.executor import (
    CommandResult,
    DockerExecutor,
    Executor,
    LocalSubprocessExecutor,
)
from graph.export import to_graphml
from graph.schema import (
    Attempt,
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)

__all__ = [
    "Node",
    "Edge",
    "DepGraph",
    "NodeType",
    "EdgeType",
    "State",
    "DiscoveredBy",
    "Layer",
    "Attempt",
    "CommandResult",
    "Executor",
    "LocalSubprocessExecutor",
    "DockerExecutor",
    "build_dep_graph",
    "certify",
    "certify_all",
    "to_graphml",
]
