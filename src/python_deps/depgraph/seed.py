"""Predicted native-build nodes — proactive ``Tool`` / ``SystemLib`` prediction.

Realizes the spec's "Predicted native nodes" pass
(``docs/superpowers/specs/2026-06-23-uv-enriched-depgraph.md``): after the
resolver produces the ``Package`` layer, packages with a known native footprint
get *predicted* provider nodes BEFORE the build runs, so an agent can see the
likely native gaps without first failing a build.

A package triggers a prediction when either:

* it has a ``tables.PACKAGE_TO_SYSTEM_DEPS`` hit (curated apt dev/runtime deps), or
* it must be built from source (``build_from_source=True``) — a source build needs
  a compiler toolchain (generic ``build-essential`` prediction) even when no
  curated table row exists.

Predicted nodes are ``discovered_by=RESOLVER`` (a *prediction*, not an
observation) and ``state=UNKNOWN``; the probe stage later RECONCILES them with the
real observed gap (same id) and the host certifier flips ``state``.  Apt packages
ending in ``-dev`` (and the generic ``build-essential``) are build-time toolchain
needs (``Tool``/``TOOLCHAIN``); the rest are runtime shared-library needs
(``SystemLib``/``SYSTEM``).

Pure: every "mutation" returns a NEW ``DepGraph`` (repo immutability rule).
"""

from __future__ import annotations

from python_deps.depgraph.ids import syslib_id, tool_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from python_deps.depgraph.tables import system_deps_for_package

# Generic toolchain predicted for a from-source build with no curated table row.
_GENERIC_TOOLCHAIN_APT = "build-essential"

# Apt packages that are build-time toolchain needs rather than runtime libs.
_TOOLCHAIN_APT = frozenset({_GENERIC_TOOLCHAIN_APT})


def _is_toolchain_apt(apt: str) -> bool:
    """True when ``apt`` is a build-time (toolchain) need, not a runtime lib."""
    return apt.endswith("-dev") or apt in _TOOLCHAIN_APT


def _predicted_node(apt: str) -> Node:
    """A resolver-predicted provider node for apt package ``apt`` (UNKNOWN)."""
    fix = f"apt:{apt}"
    if _is_toolchain_apt(apt):
        node_type, layer, node_id = NodeType.TOOL, Layer.TOOLCHAIN, tool_id(apt)
        # presence check for a -dev/toolchain apt package (host-certified later).
        check = f"dpkg -s {apt}"
    else:
        node_type, layer, node_id = NodeType.SYSTEM_LIB, Layer.SYSTEM, syslib_id(apt)
        check = f"dpkg -s {apt}"
    return Node(
        id=node_id,
        type=node_type,
        name=apt,
        layer=layer,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.UNKNOWN,
        check_command=check,
        fix_candidates=(fix,),
        chosen_fix=fix,
        provenance="predicted (native-risk)",
    )


def _predicted_apts(pkg: Node) -> list[str]:
    """Apt provider packages to predict for ``pkg`` (table first, then generic)."""
    table = system_deps_for_package(pkg.name)
    if table:
        return table
    if pkg.build_from_source:
        return [_GENERIC_TOOLCHAIN_APT]
    return []


def seed_predicted_native(graph: DepGraph) -> DepGraph:
    """Pre-emit predicted ``Tool`` / ``SystemLib`` nodes for native-risk packages.

    For every ``Package`` with a curated native footprint or a from-source build
    risk, add the predicted provider node(s) (deduped by id across packages) and a
    ``requires`` edge from the owning package.  Returns a NEW graph.
    """
    new = graph
    for pkg in [n for n in graph.nodes if n.type is NodeType.PACKAGE]:
        for apt in _predicted_apts(pkg):
            node = _predicted_node(apt)
            if new.get(node.id) is None:
                new = new.with_node(node)
            new = new.with_edge(
                Edge(
                    src=pkg.id,
                    dst=node.id,
                    relation=EdgeType.REQUIRES,
                    origin="resolver",
                )
            )
    return new
