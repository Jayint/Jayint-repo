"""Project a certified DepGraph into one whole, install-only setup.sh artifact
(design 2026-06-29). Pure: no Docker, no network, no LLM, no src.envstate.

Distinct from script.render_setup_sh (the live block-stepped, round-trippable
format): this renderer hoists shared setup and adds tier section headers, so it
is intentionally NOT parseable back to one-block-per-node.
"""
from __future__ import annotations

from python_deps.depgraph.schema import DepGraph

_BANNER = (
    "#!/usr/bin/env bash",
    "#",
    "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.",
    "# Edit the graph and re-render; this file is an artifact, not a source.",
    "#",
)


def render_build_script(graph, manual_blocks=()) -> str:
    if graph is None:
        graph = DepGraph()
    parts: list[str] = list(_BANNER) + ["set -Eeuo pipefail"]
    return "\n".join(parts) + "\n"
