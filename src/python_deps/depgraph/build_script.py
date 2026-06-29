"""Project a certified DepGraph into one whole, install-only setup.sh artifact
(design 2026-06-29). Pure: no Docker, no network, no LLM, no src.envstate.

Distinct from script.render_setup_sh (the live block-stepped, round-trippable
format): this renderer hoists shared setup and adds tier section headers, so it
is intentionally NOT parseable back to one-block-per-node.
"""
from __future__ import annotations

from python_deps.depgraph.emit import _is_reciped, _apt_name, _pip_spec, topo_order
from python_deps.depgraph.schema import DepGraph, Layer, Node, NodeType

_BANNER = (
    "#!/usr/bin/env bash",
    "#",
    "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.",
    "# Edit the graph and re-render; this file is an artifact, not a source.",
    "#",
)
_LAYER_ORDER: tuple[Layer, ...] = tuple(Layer)  # enum order == rank order


def _section_header(layer: Layer) -> str:
    label = layer.value.upper()
    return f"# ==================== {label} ===================="


def _install_command(node: Node) -> str:
    apt = _apt_name(node)
    if apt is not None:
        return f"apt-get install -y --no-install-recommends {apt}"
    if node.type is NodeType.PACKAGE:
        return f"python3 -m pip install --break-system-packages --no-deps {_pip_spec(node)}"
    return node.chosen_fix or ""  # defensive; reciped syslib/tool are always apt


def _annotation(graph: DepGraph, node: Node) -> list[str]:
    from python_deps.depgraph.advise import _best_evidence_line  # lazy: avoid load-order coupling
    toks = [f"#@node {node.id}"]
    if node.version:
        toks.append(f"version={node.version}")
    if _apt_name(node) is not None:
        toks.append(f"provider={node.chosen_fix}")
    reqs = [d.id for d in graph.requires_of(node.id) if _is_reciped(d)]
    toks.append("requires=" + (",".join(sorted(reqs)) if reqs else "-"))
    unblocks = sorted(n.id for n in graph.required_by(node.id) if _is_reciped(n))
    if unblocks:
        toks.append("unblocks=" + ",".join(unblocks))
    if node.build_from_source:
        toks.append("build-from-source")
    if node.layer is Layer.TOOLCHAIN:
        toks.append("toolchain")
    ev = _best_evidence_line(node.evidence)
    if ev:
        toks.append(f"evidence={ev}")
    out = ["  ".join(toks)]
    if node.check_command:
        out.append(f"#@check {node.check_command}")
    return out


def _node_block(graph: DepGraph, node: Node, apt_done: list[bool]) -> list[str]:
    out: list[str] = []
    if _apt_name(node) is not None and not apt_done[0]:
        out += ["export DEBIAN_FRONTEND=noninteractive", "apt-get update"]
        apt_done[0] = True
    out += _annotation(graph, node)
    out.append(_install_command(node))
    return out


def _reciped_in_layer(graph: DepGraph, layer: Layer) -> tuple[Node, ...]:
    nodes = tuple(n for n in graph.nodes if n.layer is layer and _is_reciped(n))
    return topo_order(graph, nodes)


_NEED_TYPES: tuple[NodeType, ...] = (NodeType.CONFIG, NodeType.SERVICE, NodeType.DATA_ASSET)


def _need_block(graph: DepGraph, node: Node) -> list[str]:
    from python_deps.depgraph.advise import _best_evidence_line  # lazy: avoid load-order coupling
    reqs = [d.id for d in graph.requires_of(node.id) if _is_reciped(d)]
    head = f"#@need {node.id}  state={node.state.value}"
    if reqs:
        head += "  requires=" + ",".join(sorted(reqs))
    out = ["#", head]
    if node.check_command:
        out.append(f"#@check {node.check_command}")
    ev = _best_evidence_line(node.evidence)
    if ev:
        out.append(f"#@evidence {ev}")
    out.append("#     (no command — propose a governed block to satisfy this)")
    return out


def _need_in_layer(graph: DepGraph, layer: Layer, covered: set[str]) -> list[Node]:
    nodes = [n for n in graph.nodes
             if n.layer is layer and n.type in _NEED_TYPES
             and not _is_reciped(n) and n.id not in covered]
    return sorted(nodes, key=lambda n: n.id)


def render_build_script(graph, manual_blocks=()) -> str:
    if graph is None:
        graph = DepGraph()
    parts: list[str] = list(_BANNER) + ["set -Eeuo pipefail"]
    apt_done = [False]
    for layer in _LAYER_ORDER:
        section: list[str] = []
        for node in _reciped_in_layer(graph, layer):
            section += _node_block(graph, node, apt_done)
        for node in _need_in_layer(graph, layer, covered=set()):
            section += _need_block(graph, node)
        if section:
            parts.append("")
            parts.append(_section_header(layer))
            parts.extend(section)
    return "\n".join(parts) + "\n"
