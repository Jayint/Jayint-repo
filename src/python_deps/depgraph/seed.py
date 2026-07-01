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
needs (``Tool``/``TOOLCHAIN``, apt-keyed — a header is never ``ldd``-observable);
the rest are runtime shared-library needs (``SystemLib``/``SYSTEM``).

Canonical rule (native-identity): for a runtime ``SystemLib`` the SONAME is the
node's identity, not the apt package name.  The soname is what ``ldd_probe`` /
``import_probe`` actually OBSERVE on the installed binary, so keying the
*prediction* by soname too makes the seed prediction and the real observation
land on the SAME node — no split, regardless of whether soname->apt resolution
succeeds.  The apt package (from ``tables.NATIVE_LIB_TO_APT``) is carried as a
``chosen_fix``/``fix_candidates`` attribute, never as the id.

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
from python_deps.depgraph.tables import apt_for_soname, system_deps_for_package

# Generic toolchain predicted for a from-source build with no curated table row.
_GENERIC_TOOLCHAIN_APT = "build-essential"

# Apt packages that are build-time toolchain needs rather than runtime libs.
_TOOLCHAIN_APT = frozenset({_GENERIC_TOOLCHAIN_APT})


def _is_toolchain_apt(entry: str) -> bool:
    """True when a curated table ``entry`` names a build-time (toolchain) need.

    Apt tool/header names end in ``-dev`` (or are the generic
    ``build-essential``); a runtime soname never does, so this also correctly
    routes soname entries to the SystemLib branch below.
    """
    return entry.endswith("-dev") or entry in _TOOLCHAIN_APT


def _predicted_tool_node(apt: str) -> Node:
    """A resolver-predicted ``Tool`` node for build-time apt package ``apt``."""
    fix = f"apt:{apt}"
    return Node(
        id=tool_id(apt),
        type=NodeType.TOOL,
        name=apt,
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.UNKNOWN,
        # presence check for a -dev/toolchain apt package (host-certified later).
        check_command=f"dpkg -s {apt}",
        fix_candidates=(fix,),
        chosen_fix=fix,
        provenance="predicted (native-risk)",
    )


def _predicted_syslib_node(soname: str) -> Node:
    """A resolver-predicted ``SystemLib`` node, keyed by the SONAME (canonical
    identity — see module docstring).  The apt package (if the offline table
    knows it) is carried in ``chosen_fix``/``fix_candidates``, never the id.
    """
    apt = apt_for_soname(soname)
    fix = f"apt:{apt}" if apt else None
    return Node(
        id=syslib_id(soname),
        type=NodeType.SYSTEM_LIB,
        name=soname,
        layer=Layer.SYSTEM,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.UNKNOWN,
        check_command=f"ldconfig -p | grep {soname}",
        fix_candidates=(fix,) if fix else (),
        chosen_fix=fix,
        provenance="predicted (native-risk)",
    )


def _predicted_node(entry: str) -> Node:
    """A resolver-predicted provider node for a curated table ``entry``.

    ``entry`` is either a build-time apt tool/header name (-> ``Tool``,
    apt-keyed) or a run-time soname (-> ``SystemLib``, soname-keyed).
    """
    if _is_toolchain_apt(entry):
        return _predicted_tool_node(entry)
    return _predicted_syslib_node(entry)


def _predicted_entries(pkg: Node) -> list[str]:
    """Curated table entries to predict for ``pkg`` (table first, then generic).

    Each entry is either an apt tool/header name or a runtime soname (see
    ``tables.PACKAGE_TO_SYSTEM_DEPS`` docstring) — ``_predicted_node`` dispatches
    on its shape.
    """
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
        for entry in _predicted_entries(pkg):
            node = _predicted_node(entry)
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
