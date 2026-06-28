"""Compile a certified DepGraph's emittable wave into annotated, one-action-per-block
script blocks (design §6). Pure: no Docker, no network, no LLM."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.emit import partition, topo_order, _apt_name, _pip_spec
from python_deps.depgraph.schema import DepGraph, Node, NodeType


@dataclass(frozen=True)
class Block:
    block_id: str
    wave: str                              # node.layer.value
    commands: tuple[str, ...]
    target_node_ids: tuple[str, ...]
    provider_ids: tuple[str, ...] = ()
    check_commands: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    mutates_env: bool = True
    can_batch: bool = False                # v1: one action per block (defer batching to v2)


def _command_for(node: Node) -> str:
    apt = _apt_name(node)
    if apt is not None:
        return f"apt-get install -y --no-install-recommends {apt}"
    if node.type is NodeType.PACKAGE:
        return f"python3 -m pip install --break-system-packages {_pip_spec(node)}"
    # Fallback: a node with an explicit chosen_fix that is not apt: (e.g. a shell recipe).
    return node.chosen_fix or ""


def _block_id_for(node: Node) -> str:
    short = node.id.split(":", 1)[-1]
    return f"{node.layer.value}.{short}"


def compile_blocks(graph: DepGraph) -> tuple[Block, ...]:
    if graph is None:
        return ()
    ready = topo_order(graph, partition(graph).emittable)
    blocks: list[Block] = []
    for n in ready:
        cmd = _command_for(n)
        if not cmd:
            continue
        apt = _apt_name(n)
        blocks.append(Block(
            block_id=_block_id_for(n),
            wave=n.layer.value,
            commands=(cmd,),
            target_node_ids=(n.id,),
            provider_ids=(n.chosen_fix,) if apt is not None else (),
            check_commands=(n.check_command,) if n.check_command else (),
        ))
    return tuple(blocks)
