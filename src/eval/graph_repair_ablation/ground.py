# src/eval/graph_repair_ablation/ground.py
"""Deterministic grounding arm: run a captured failure through parse()->integrate()
(arm G) and through the PACKAGE-only baseline _anchor_for_cause (arm B), and grade each
anchor against the injection's correct_anchor. Pure; no LLM, no Docker."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from graph.model import DepGraph
from graph.python.enrich.exec_trace import parse, ObservationOverlay
from graph.python.enrich.integrate import integrate
from graph.view.graph_context import _anchor_for_cause


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
    # arm G: parse -> integrate
    pf = parse(command, failure_output, phase, ctx)
    g2, overlay = integrate(graph, ObservationOverlay(), pf, ctx)
    obs = overlay.get(pf.stable_id)
    grounded_anchor = obs.anchor if obs is not None else None
    grounded_added_node = len(g2.nodes) > len(graph.nodes)
    via = tuple(obs.chain) if obs is not None else ()

    # arm B: PACKAGE-only baseline (reads cause.detail only)
    node = _anchor_for_cause(graph, SimpleNamespace(detail=cause_text))
    baseline_anchor = node.id if node is not None else None

    return {
        "grounded_anchor": grounded_anchor,
        "grounded_added_node": grounded_added_node,
        "baseline_anchor": baseline_anchor,
        "via": via,
    }


_GROUND_ARMS = ("G", "B")


def aggregate_grounding(results: list[dict]) -> dict:
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in results:
        s = r.get("score")
        if not s:
            continue
        groups.setdefault((r["failure_class"], r["arm"]), []).append(s)
    agg: dict[tuple[str, str], dict] = {}
    for key, scores in groups.items():
        n = len(scores)
        agg[key] = {
            "n": n,
            "grounded_at_1": sum(1 for s in scores if s["grounded"]) / n,
            "mislocalized": sum(1 for s in scores if s["mislocalized"]),
            "null_rate": sum(1 for s in scores if s["is_null"]) / n,
        }
    return agg


def render_grounding_report_md(agg: dict) -> str:
    lines = ["# Grounding-Arm Report (G = parse->integrate, B = PACKAGE-only baseline)", ""]
    if not agg:
        return "\n".join(lines + ["(no data)"]) + "\n"
    lines += [
        "| Failure Class | Arm | N | grounded@1 | mislocalized | null_rate |",
        "|---|---|---|---|---|---|",
    ]
    for cls in sorted({c for c, _ in agg}):
        for arm in [a for a in _GROUND_ARMS if (cls, a) in agg]:
            cell = agg[(cls, arm)]
            lines.append(
                f"| {cls} | {arm} | {cell['n']} | {cell['grounded_at_1']:.0%} | "
                f"{cell['mislocalized']} | {cell['null_rate']:.0%} |"
            )
    return "\n".join(lines) + "\n"


def grounding_scorecard(gcases) -> tuple[dict, str]:
    rows: list[dict] = []
    for c in gcases:
        g = DepGraph(nodes=c.starting_nodes)
        res = run_grounding(g, c.cause_text, c.command, c.failure_output, c.ctx)
        gs = grade_grounding(res["grounded_anchor"], res["grounded_added_node"], c.correct_anchor)
        bs = grade_grounding(res["baseline_anchor"], res["baseline_anchor"] is not None, c.correct_anchor)
        rows.append({"failure_class": c.failure_class, "arm": "G", "score": gs.__dict__})
        rows.append({"failure_class": c.failure_class, "arm": "B", "score": bs.__dict__})
    agg = aggregate_grounding(rows)
    return agg, render_grounding_report_md(agg)
