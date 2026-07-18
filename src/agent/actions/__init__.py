"""The agent's MOVE — how it acts on the build (spec §4A).

Split (3a-6) from the former single actions.py + script_prep.py + v3_build_agent.py into one package,
one concept per module:
  * base   — the react arm's move parse+apply: Action / EditOp, the native tool-call path
    (action_from_tool_call), the text fallback (parse_action), the line-splice (apply_edit), and the
    thought/reasoning extractors.
  * script — de-graphs a rendered build script for the react (script-primary) arm (strip_graph_framing).
  * graph  — the v3 graph-primary proposal-plane agent (V3BuildAgent.propose -> one typed PatchProposal).

`base` IS the package's public face: its move-parse API is re-exported here so `from src.agent.actions
import Action` (the former actions.py surface) keeps working unchanged. `script` and `graph` are reached
by their own paths (src.agent.actions.script / src.agent.actions.graph), not re-exported here.
"""
from __future__ import annotations

from src.agent.actions.base import (
    Action,
    EditOp,
    TOOLS_SCHEMA,
    action_from_tool_call,
    apply_edit,
    extract_reasoning,
    extract_thought,
    parse_action,
)

__all__ = [
    "Action",
    "EditOp",
    "TOOLS_SCHEMA",
    "action_from_tool_call",
    "apply_edit",
    "extract_reasoning",
    "extract_thought",
    "parse_action",
]
