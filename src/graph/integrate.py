"""ANCHOR / Integrate: merge a ParsedFailure into the persistent DepGraph.

Structure (nodes + one requires edge) -> DepGraph; causality (chain + blast radius
+ raw span) -> ObservationOverlay. Never invents a provider for an unresolved root.
Idempotent by stable id. Pure module. Additive — reuses ids/import_mapping only.
"""
from __future__ import annotations

from dataclasses import replace

from graph.diagnose import RepoContext, is_local_import
from graph.exec_trace import ObservationOverlay, Observation, ParsedFailure
from graph.ids import (
    TEST_NODE_ID, capability_id, config_id, import_id, package_id, service_id, syslib_id,
)
from graph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State, Strength,
)
from graph.python.util.import_mapping import (
    is_unresolved, map_import_to_package, normalize_package_name,
)

_KIND_LAYER = {
    NodeType.PACKAGE: Layer.PIP, NodeType.IMPORT: Layer.PIP,
    NodeType.SYSTEM_LIB: Layer.SYSTEM, NodeType.TOOL: Layer.TOOLCHAIN,
    NodeType.CONFIG: Layer.CONFIG, NodeType.SERVICE: Layer.SERVICES,
}


def _split(descriptor: str) -> tuple[str, str]:
    kind, _, name = descriptor.partition(":")
    return kind, name


def _resolve_root(parsed: ParsedFailure, ctx: RepoContext):
    """Return (disposition, node_id, node_type). disposition in
    {'provider','demand','refuse'}. Anchors the CAUSAL descriptor."""
    kind, name = _split(parsed.causal)
    if kind == "import":
        if is_local_import(name, ctx.local_names):
            return ("refuse", None, None)                    # repo-local: add nothing
        result = map_import_to_package(name)
        if is_unresolved(result):
            return ("demand", import_id(name), NodeType.IMPORT)   # NO provider guess
        return ("provider", package_id(result.package_name, None), NodeType.PACKAGE)
    if kind == "syslib":
        return ("provider", syslib_id(name), NodeType.SYSTEM_LIB)
    if kind in ("binary", "tool"):
        # FRACTURE GUARD: a missing executable is a binary: capability, never tool:<name>.
        return ("provider", capability_id("binary", name), NodeType.TOOL)
    if kind == "config":
        return ("provider", config_id(name), NodeType.CONFIG)
    if kind == "service":
        return ("provider", service_id(name), NodeType.SERVICE)
    return ("demand", import_id(name or parsed.causal), NodeType.IMPORT)


def _find_existing(graph: DepGraph, node_id: str, node_type: NodeType, name: str) -> Node | None:
    """Idempotent match: exact id, else PACKAGE by normalized name (pkg:<name> vs
    pkg:<name>==<ver> from the static resolver)."""
    direct = graph.get(node_id)
    if direct is not None:
        return direct
    if node_type is NodeType.PACKAGE:
        want = normalize_package_name(name)
        for n in graph.nodes:
            if n.type is NodeType.PACKAGE and normalize_package_name(n.name) == want:
                return n
    return None


def _edge_data(parsed: ParsedFailure) -> dict:
    via = [step[2] for step in parsed.chain[:-1]]
    importer = parsed.chain[-1][0] if parsed.chain else ""
    return {"phase": parsed.phase, "via": via, "importer": importer}


def integrate(
    graph: DepGraph,
    overlay: ObservationOverlay,
    parsed: ParsedFailure,
    ctx: RepoContext,
) -> tuple[DepGraph, ObservationOverlay]:
    disposition, node_id, node_type = _resolve_root(parsed, ctx)

    if disposition == "refuse":
        # Record the observation (evidence never lost) but touch NO graph node/edge.
        obs = Observation(stable_id=parsed.stable_id, anchor=parsed.causal, chain=parsed.chain,
                          blast_radius=parsed.blast_radius, phase=parsed.phase,
                          raw_span=parsed.raw_span)
        return graph, overlay.with_observation(obs)

    name = _split(parsed.causal)[1]
    existing = _find_existing(graph, node_id, node_type, name)
    if existing is not None:
        anchor_id = existing.id
        node = replace(existing, discovered_by=DiscoveredBy.RUNTIME,
                       evidence=parsed.raw_span[:500])
    else:
        anchor_id = node_id
        node = Node(id=node_id, type=node_type,
                    name=name if node_type is not NodeType.IMPORT else parsed.causal,
                    layer=_KIND_LAYER[node_type], discovered_by=DiscoveredBy.RUNTIME,
                    state=State.MISSING if disposition == "demand" else State.UNKNOWN,
                    strength=Strength.SOFT, evidence=parsed.raw_span[:500])
    new_graph = graph.with_node(node)

    # STRUCTURE: one requires edge owner->provider, carrying the causal chain.
    # Demand nodes (unresolved imports) get NO outgoing provider edge, but the owner
    # still requires the (unsatisfied) capability, so hang Test->import: for demand too
    # ONLY when it is a real capability the target needs. For provider, Test->provider.
    if disposition == "provider":
        owner = TEST_NODE_ID if new_graph.get(TEST_NODE_ID) is not None else None
        if owner is not None:
            edge = Edge(src=owner, dst=anchor_id, relation=EdgeType.REQUIRES,
                        origin="runtime", data=_edge_data(parsed))
            new_graph = new_graph.with_edge(edge)

    # CAUSALITY: overlay carries chain + blast + raw span.
    obs = Observation(stable_id=parsed.stable_id, anchor=anchor_id, chain=parsed.chain,
                      blast_radius=parsed.blast_radius, phase=parsed.phase,
                      raw_span=parsed.raw_span)
    return new_graph, overlay.with_observation(obs)
