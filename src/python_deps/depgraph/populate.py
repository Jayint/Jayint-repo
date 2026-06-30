"""The single producer of node install commands for the static path.

Pure: no Docker, no network, no LLM, no src.envstate. populate_setup_commands
fills node.setup_commands for the reciped tiers (Package/SystemLib/Tool) so the
renderer can be a dumb emitter. _command_for here is the ONLY copy of the
per-node install-command logic in the static path — build_script._install_command
is deleted in favour of it.
"""
from __future__ import annotations

from dataclasses import replace

from python_deps.depgraph.emit import _apt_name, _is_reciped, _pip_spec
from python_deps.depgraph.schema import DepGraph, NodeType, Strength


def _command_for(node) -> str:
    """The install command for a reciped node (apt for SystemLib/Tool, pinned
    --no-deps pip for Package). The single source of this derivation."""
    apt = _apt_name(node)
    if apt is not None:
        return f"apt-get install -y --no-install-recommends {apt}"
    if node.type is NodeType.PACKAGE:
        return f"python3 -m pip install --break-system-packages --no-deps {_pip_spec(node)}"
    return node.chosen_fix or ""  # defensive; reciped syslib/tool are always apt


def populate_setup_commands(graph: DepGraph) -> DepGraph:
    """Return a NEW graph in which every reciped node lacking setup_commands gets
    its install command + strength=HARD. Idempotent; leaves Service/Config/
    DataAsset and already-populated nodes untouched."""
    new = graph
    for node in graph.nodes:
        if node.setup_commands:
            continue
        if not _is_reciped(node):
            continue
        cmd = _command_for(node)
        if not cmd:
            continue
        new = new.with_node(replace(node, setup_commands=(cmd,), strength=Strength.HARD))
    return new
