"""Phase-1 curated runtime-executable prior.

Mints a ``binary:<tool>`` Tool node for every closure Package in
``PACKAGE_TO_RUNTIME_TOOLS`` — the run-time analogue of ``seed_build_deps``, for
external CLI programs a package shells out to that no static sensor can observe.
Minted ``discovered_by=RESOLVER`` (so the probe path's ``reconcile_predicted``
annotates rather than overwrites) via ``apt_for_cli_tool`` (the resolve path
lacks these binaries in PROVIDER_TABLE). Pure; idempotent (reconciles with a
repo-source subprocess-scan node of the same id).
"""

from __future__ import annotations

from graph.model import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
    capability_id,
)
from graph.python.native.tables import apt_for_cli_tool, runtime_tools_for


def _tool_node(tool: str, apt: str) -> Node:
    fix = f"apt:{apt}"
    return Node(
        id=capability_id("binary", tool),
        type=NodeType.TOOL,
        name=tool,
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.UNKNOWN,
        check_command=f"command -v {tool}",
        fix_candidates=(fix,),
        chosen_fix=fix,
        provenance="runtime-tool prior",
    )


def seed_runtime_tools(graph: DepGraph) -> DepGraph:
    """For each closure Package in PACKAGE_TO_RUNTIME_TOOLS, add its
    ``binary:<tool>`` Tool node(s) (deduped by id) + a ``requires`` edge from the
    Package. Returns a NEW graph; a no-op when no package maps to a tool."""
    new = graph
    for pkg in [n for n in graph.nodes if n.type is NodeType.PACKAGE]:
        for tool in runtime_tools_for(pkg.name):
            apt = apt_for_cli_tool(tool)
            if apt is None:      # table invariant guards this; belt-and-suspenders
                continue
            tid = capability_id("binary", tool)
            if new.get(tid) is None:
                new = new.with_node(_tool_node(tool, apt))
            new = new.with_edge(Edge(
                src=pkg.id, dst=tid, relation=EdgeType.REQUIRES, origin="resolver"
            ))
    return new
