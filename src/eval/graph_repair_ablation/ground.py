# src/eval/graph_repair_ablation/ground.py
"""Deterministic grounding arm: run a captured failure through parse()->integrate()
(arm G) and through the PACKAGE-only baseline _anchor_for_cause (arm B), and grade each
anchor against the injection's correct_anchor. Pure; no LLM, no Docker."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GroundingScore:
    grounded: bool          # anchor matched correct_anchor (or correctly refused)
    mislocalized: bool      # produced an anchor, but the wrong one
    is_null: bool           # produced no anchor at all
    anchor: str | None


def grade_grounding(anchor: str | None, added_node: bool, correct_anchor: str) -> GroundingScore:
    """correct_anchor="" == REFUSE: correct iff NO graph node was added.
    Otherwise correct iff `anchor` equals correct_anchor (exact id)."""
    if correct_anchor == "":
        ok = not added_node
        return GroundingScore(grounded=ok, mislocalized=added_node, is_null=(anchor is None), anchor=anchor)
    if anchor is None:
        return GroundingScore(grounded=False, mislocalized=False, is_null=True, anchor=None)
    hit = (anchor == correct_anchor)
    return GroundingScore(grounded=hit, mislocalized=not hit, is_null=False, anchor=anchor)


def run_grounding(graph, cause_text, command, failure_output, ctx, phase="collection") -> dict:
    raise NotImplementedError("implemented in Task 2")
