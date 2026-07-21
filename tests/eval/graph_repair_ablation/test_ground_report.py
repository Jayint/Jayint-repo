# tests/eval/graph_repair_ablation/test_ground_report.py
from src.eval.graph_repair_ablation.ground import aggregate_grounding, render_grounding_report_md


def _row(cls, arm, grounded, mis=False, null=False):
    return {"failure_class": cls, "arm": arm,
            "score": {"grounded": grounded, "mislocalized": mis, "is_null": null, "anchor": None}}


def test_aggregate_and_delta():
    rows = [
        _row("SYSLIB_MISSING", "G", True), _row("SYSLIB_MISSING", "B", False, null=True),
        _row("MODULE_NOT_FOUND", "G", True), _row("MODULE_NOT_FOUND", "B", True),
    ]
    agg = aggregate_grounding(rows)
    assert agg[("SYSLIB_MISSING", "G")]["grounded_at_1"] == 1.0
    assert agg[("SYSLIB_MISSING", "B")]["grounded_at_1"] == 0.0
    md = render_grounding_report_md(agg)
    assert "SYSLIB_MISSING" in md and "| G |" in md and "| B |" in md
