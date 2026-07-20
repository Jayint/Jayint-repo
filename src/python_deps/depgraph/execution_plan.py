"""Graph-linked execution plan used by the incremental v3 executor.

The final ``setup.sh`` remains the reproducible artifact, but the live executor
must not rediscover block boundaries by parsing shell text.  This module
projects the same graph and governed manual blocks into structured ``Block``
objects whose commands, targets, checks, providers, and evidence stay linked.

Pure: no Docker, network, LLM, or ``src.envstate`` imports.
"""
from __future__ import annotations

import hashlib
import json

from python_deps.depgraph.block import Block
from python_deps.depgraph.certify import EXECUTION_LAYER_ORDER
from python_deps.depgraph.emit import _apt_name, _is_reciped, topo_order
from python_deps.depgraph.populate import populate_setup_commands
from python_deps.depgraph.schema import DepGraph, Layer, Node


_LAYER_ORDER: tuple[Layer, ...] = EXECUTION_LAYER_ORDER + tuple(
    layer for layer in Layer if layer not in EXECUTION_LAYER_ORDER
)


def _block_id_for(node: Node) -> str:
    return f"{node.layer.value}.{node.id.split(':', 1)[-1]}"


def _node_block(node: Node, *, apt_prelude: bool) -> Block:
    commands = list(node.setup_commands)
    if apt_prelude:
        commands[:0] = [
            "export DEBIAN_FRONTEND=noninteractive",
            "apt-get update",
        ]
    return Block(
        block_id=_block_id_for(node),
        wave=node.layer.value,
        commands=tuple(commands),
        target_node_ids=(node.id,),
        provider_ids=(node.chosen_fix,) if node.chosen_fix else (),
        check_commands=(node.check_command,) if node.check_command else (),
        evidence_refs=(node.evidence,) if node.evidence else (),
    )


def compile_execution_plan(
    graph: DepGraph | None,
    manual_blocks: tuple[Block, ...] = (),
) -> tuple[Block, ...]:
    """Compile the complete replay plan without flattening it to shell text.

    The ordering and apt prelude match ``render_build_script``: graph-derived
    blocks first within each wave, then governed manual blocks for that wave.
    Every reciped node is included regardless of its current certification
    state because checkpoints may need to reproduce any suffix from a clean
    prefix.
    """
    if graph is None:
        graph = DepGraph()
    graph = populate_setup_commands(graph)

    known_waves = {layer.value for layer in _LAYER_ORDER}
    illegal = [block.block_id for block in manual_blocks if block.wave not in known_waves]
    if illegal:
        raise ValueError(
            "compile_execution_plan: manual blocks have illegal waves "
            f"(not a Layer value): {illegal}"
        )

    manual_by_wave: dict[str, list[Block]] = {}
    for block in manual_blocks:
        manual_by_wave.setdefault(block.wave, []).append(block)

    blocks: list[Block] = []
    apt_done = False
    for layer in _LAYER_ORDER:
        nodes = tuple(
            node for node in graph.nodes if node.layer is layer and _is_reciped(node)
        )
        for node in topo_order(graph, nodes):
            if not node.setup_commands:
                continue
            is_apt = _apt_name(node) is not None
            blocks.append(_node_block(node, apt_prelude=is_apt and not apt_done))
            apt_done = apt_done or is_apt
        blocks.extend(manual_by_wave.get(layer.value, ()))
    return tuple(blocks)


def block_signature(block: Block) -> str:
    """Stable semantic key for checkpoint-prefix invalidation."""
    # Evidence is diagnostic metadata, not environment state. A failed host
    # check may update it without changing the plan and must not invalidate an
    # otherwise identical checkpoint prefix.
    payload = {
        "id": block.block_id,
        "wave": block.wave,
        "commands": list(block.commands),
        "targets": list(block.target_node_ids),
        "providers": list(block.provider_ids),
        "checks": list(block.check_commands),
        "mutates_env": block.mutates_env,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def execution_plan_hash(blocks: tuple[Block, ...]) -> str:
    blob = "\n".join(block_signature(block) for block in blocks)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:12]
