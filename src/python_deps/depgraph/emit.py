"""Pure emit core: classify the graph and turn the certified closure into a recipe.

This module is the deterministic counterpart to the LLM recipe loop: it decides
which MISSING nodes the host can install without judgement (EMITTABLE), which
require the LLM (FRONTIER), and emits an ordered, layer-correct recipe for the
emittable set. Pure with respect to its inputs — no Docker, no network, no
subprocess (mirrors probe.py / advise.py). Returns neutral EmitStep objects so
this package keeps its zero dependency on src.envstate (world_model.py:22).
"""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.schema import (
    DepGraph,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)

# Node types the host can directly install. Import/Test/Project/Runtime are
# structural — satisfied via their Package (naming relink) or out of scope here.
_INSTALLABLE: tuple[NodeType, ...] = (
    NodeType.PACKAGE,
    NodeType.SYSTEM_LIB,
    NodeType.TOOL,
)


@dataclass(frozen=True)
class Partition:
    certified: tuple[Node, ...]
    emittable: tuple[Node, ...]
    frontier: tuple[Node, ...]


def _conflicted_ids(graph: DepGraph) -> set[str]:
    """Node ids touched by a conflicts_with edge (uv unsat core) — never emit."""
    ids: set[str] = set()
    for e in graph.edges:
        if e.relation is EdgeType.CONFLICTS_WITH:
            ids.add(e.src)
            ids.add(e.dst)
    return ids


def _toolchain_ready(graph: DepGraph, pkg: Node) -> bool:
    """True when every SystemLib/Tool this package requires is already SATISFIED."""
    for dep in graph.requires_of(pkg.id):
        if dep.type in (NodeType.SYSTEM_LIB, NodeType.TOOL) and dep.state is not State.SATISFIED:
            return False
    return True


def _is_emittable(graph: DepGraph, node: Node, conflicted: set[str]) -> bool:
    if node.state is not State.MISSING:
        return False
    if node.id in conflicted:
        return False
    if node.type is NodeType.PACKAGE:
        if not node.version:           # unresolved -> the LLM's call
            return False
        if node.build_from_source and not _toolchain_ready(graph, node):
            return False               # wait for its toolchain to certify
        return True
    if node.type in (NodeType.SYSTEM_LIB, NodeType.TOOL):
        return bool(node.chosen_fix and node.chosen_fix.startswith("apt:"))
    return False


def partition(graph: DepGraph) -> Partition:
    """Classify installable nodes into certified / emittable / frontier."""
    conflicted = _conflicted_ids(graph)
    certified: list[Node] = []
    emittable: list[Node] = []
    frontier: list[Node] = []
    for n in graph.nodes:
        if n.type not in _INSTALLABLE:
            continue
        if n.state is State.SATISFIED:
            certified.append(n)
        elif _is_emittable(graph, n, conflicted):
            emittable.append(n)
        elif n.state is State.MISSING:
            frontier.append(n)
        # UNKNOWN with no decision: neither emitted nor escalated.
    return Partition(tuple(certified), tuple(emittable), tuple(frontier))


# Bottom-up execution rank (matches certify._LAYER_ORDER / advise._LAYER_RANK).
_LAYER_RANK: dict[Layer, int] = {
    Layer.INTERPRETER: 0,
    Layer.SYSTEM: 1,
    Layer.TOOLCHAIN: 2,
    Layer.PIP: 3,
    Layer.NAMING: 4,
    Layer.RUNTIME: 5,
    Layer.TESTS: 6,
}


def topo_order(graph: DepGraph, nodes: tuple[Node, ...]) -> tuple[Node, ...]:
    """Order ``nodes`` dependency-first (a required node before its dependent).

    Kahn's algorithm over ``requires`` edges restricted to the node set; ties
    broken by (layer rank, name) for reproducibility. On a cycle (should not
    happen for a resolved closure) the remaining nodes are emitted in
    layer-rank+name order rather than raising — emit must never crash a run.
    """
    ids = {n.id for n in nodes}
    by_id = {n.id: n for n in nodes}
    deps: dict[str, set[str]] = {nid: set() for nid in ids}
    for e in graph.edges:
        if e.relation is EdgeType.REQUIRES and e.src in ids and e.dst in ids:
            deps[e.src].add(e.dst)

    ordered: list[Node] = []
    placed: set[str] = set()
    remaining = set(ids)
    while remaining:
        ready = [nid for nid in remaining if deps[nid] <= placed]
        if not ready:                      # cycle — emit the rest deterministically
            ready = list(remaining)
        ready.sort(key=lambda nid: (_LAYER_RANK.get(by_id[nid].layer, 9), by_id[nid].name))
        nxt = ready[0]
        ordered.append(by_id[nxt])
        placed.add(nxt)
        remaining.discard(nxt)
    return tuple(ordered)


_APT_PREFIX = "apt:"


@dataclass(frozen=True)
class EmitStep:
    kind: str                      # AttemptKind value: system_install | python_install
    command: str
    target_node_ids: tuple[str, ...]


def _apt_name(node: Node) -> str | None:
    if node.chosen_fix and node.chosen_fix.startswith(_APT_PREFIX):
        return node.chosen_fix[len(_APT_PREFIX):]
    return None


def _pip_spec(node: Node) -> str:
    return f"{node.name}=={node.version}" if node.version else node.name


def build_recipe(graph: DepGraph, ordered: tuple[Node, ...]) -> tuple[EmitStep, ...]:
    """Turn the topo-ordered emittable set into at most two steps (D2):

    1. one apt step for all SystemLib/Tool nodes (dedup apt names), and
    2. one pinned-closure pip step for all Package nodes (resolver-consistent).

    Cross-layer / build-from-source ordering is handled by the drain loop across
    iterations, so a single pass needs only apt-before-pip.
    """
    syslibs = [n for n in ordered if n.type in (NodeType.SYSTEM_LIB, NodeType.TOOL)]
    packages = [n for n in ordered if n.type is NodeType.PACKAGE]
    steps: list[EmitStep] = []

    if syslibs:
        names: list[str] = []
        for n in syslibs:
            apt = _apt_name(n)
            if apt and apt not in names:
                names.append(apt)
        if names:
            steps.append(EmitStep(
                kind="system_install",
                command="apt-get update && apt-get install -y " + " ".join(names),
                target_node_ids=tuple(n.id for n in syslibs),
            ))

    if packages:
        specs = " ".join(_pip_spec(n) for n in packages)
        steps.append(EmitStep(
            kind="python_install",
            command="python -m pip install " + specs,
            target_node_ids=tuple(n.id for n in packages),
        ))
    return tuple(steps)
