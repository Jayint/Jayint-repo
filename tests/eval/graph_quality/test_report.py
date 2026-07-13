"""The report must never print a number that hides a slice (plan Task 7, Global Constraints).

A 90% average would have concealed the pg_config case at 0% -- the case the whole arm exists
for. So this is asserted, not trusted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.eval.graph_quality.__main__ import main, render_markdown, run_block, run_patch

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESULTS = _REPO_ROOT / "outputs" / "repo2run_benchmark" / "results"
_ARTIFACTS = _REPO_ROOT / "outputs" / "repo2run_benchmark" / "eval_artifacts"


def test_the_enrich_table_never_shows_a_headline_without_its_slices():
    """The one property that matters: you cannot read the headline without also reading the
    per-slice rows that produced it."""
    fake = {
        "n_total": 111, "n_in_scope": 14,
        "preemption_rate_in_scope": "2/14",
        "denominator_warning": "too small to support a rate",
        "by_kind": {
            "os-package": {"n": 3, "attribution_coverage": "1/3", "preemption_rate": "1/3",
                           "hallucination_count": "0/3", "capability_unresolved_count": "0/3"},
            "python-package": {"n": 11, "attribution_coverage": "2/11", "preemption_rate": "1/11",
                               "hallucination_count": "0/11",
                               "capability_unresolved_count": "1/11"},
        },
        "negative_control": {"n": 72, "produced_a_node": 4},
    }
    md = render_markdown({"enrich": fake})

    assert "2/14" in md
    assert "too small to support a rate" in md
    # every slice is present with its OWN denominator -- not folded into the headline
    for kind in fake["by_kind"]:
        assert kind in md
    assert "1/3" in md and "1/11" in md
    # and the negative control is never silently dropped
    assert "72" in md and "false positive" in md.lower()


def test_every_reported_number_carries_a_denominator():
    """A bare '2' or a bare '14%' is not reportable. Counts appear as k/n."""
    md = render_markdown({"patch": run_patch()})
    assert "root-hit" in md
    assert "/" in md.split("root-hit")[1][:12], "root-hit must be reported as k/n"
    assert "SMOKE TEST" in md, "5 cells must never be presented as a rate"


def test_block_reports_SKIPPED_loudly_rather_than_a_perfect_score_over_nothing():
    """The dangerous failure: an un-minted graph cache produces an empty sweep, and an empty
    sweep has zero divergences -- a perfect score over zero nodes. It must say SKIPPED."""
    res = run_block()
    md = render_markdown({"block": res})
    if res.get("status") == "SKIPPED":
        assert "SKIPPED" in md
        assert "not a pass" in md.lower()
    else:
        assert "divergences" in md


@pytest.mark.skipif(not _RESULTS.is_dir(), reason="repo2run_benchmark corpus not on disk")
def test_the_cli_writes_both_artifacts(tmp_path):
    rc = main(["--enrich", "--patch", "--out-dir", str(tmp_path),
               "--results-dir", str(_RESULTS), "--artifacts-dir", str(_ARTIFACTS)])
    assert rc == 0
    results = json.loads((tmp_path / "results.json").read_text())
    assert results["enrich"]["by_kind"], "results.json must carry the per-slice breakdown"
    assert (tmp_path / "report.md").read_text().startswith("# Graph quality")
