"""Unit tests for the pure A/B root-selection scorer.

Track A of docs/superpowers/plans/2026-07-03-root-selection-ab-eval.md: adjudicate the
divergence set (packages imports-as-GENERATOR adds and imports-as-VERIFIER omits) against
gold labels. All pure — no repo checkout, no container, no python_deps import.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.eval.graph_fidelity.root_selection_ab import (  # noqa: E402
    DivergentAdd,
    adjudicate_divergence,
    aggregate,
    partition_roots,
)


# --- partition_roots ------------------------------------------------------

def test_partition_separates_declared_from_gapfill():
    roots = [(None, "Flask"), (None, "requests"), ("import:bs4", "beautifulsoup4")]
    p = partition_roots(roots, {"import:bs4": "bs4"})
    # verifier = declared only (import_id is None), canon + sorted
    assert p.verifier == ("flask", "requests")
    assert p.generator == ("beautifulsoup4", "flask", "requests")
    assert p.divergence == (DivergentAdd("import:bs4", "bs4", "beautifulsoup4"),)


def test_partition_no_divergence_when_all_declared():
    roots = [(None, "numpy"), (None, "pandas")]
    p = partition_roots(roots, {})
    assert p.divergence == ()
    assert p.generator == p.verifier == ("numpy", "pandas")


def test_partition_falls_back_to_id_when_name_unknown():
    p = partition_roots([("import:cv2", "opencv-python")], {})
    assert p.divergence[0].import_name == "import:cv2"


# --- adjudicate_divergence ------------------------------------------------

def test_adjudicate_splits_good_bad_unlabeled():
    div = (
        DivergentAdd("i1", "cv2", "opencv-python"),
        DivergentAdd("i2", "ipdb", "ipdb"),
        DivergentAdd("i3", "mystery", "mystery"),
    )
    gold = {"cv2": "needed", "ipdb": "optional"}
    res = adjudicate_divergence(div, gold)
    assert [a.import_name for a in res["good"]] == ["cv2"]
    assert [a.import_name for a in res["bad"]] == ["ipdb"]
    assert [a.import_name for a in res["unlabeled"]] == ["mystery"]


def test_adjudicate_all_benign_labels_are_bad_adds():
    div = tuple(
        DivergentAdd(f"i{i}", name, name)
        for i, name in enumerate(["a", "b", "c", "d", "e", "f", "g"])
    )
    gold = {
        "a": "optional", "b": "typing", "c": "local", "d": "tooling",
        "e": "transitive", "f": "alias_of_declared", "g": "stdlib",
    }
    res = adjudicate_divergence(div, gold)
    assert len(res["bad"]) == 7 and not res["good"] and not res["unlabeled"]


# --- aggregate ------------------------------------------------------------

def _card(good=0, bad=0, unlabeled=0, div=None):
    div = div if div is not None else [{}] * (good + bad + unlabeled)
    return {
        "repo": "x", "divergence": div,
        "good_adds": [{}] * good, "bad_adds": [{}] * bad, "unlabeled_adds": [{}] * unlabeled,
    }


def test_aggregate_identical_when_zero_divergence():
    agg = aggregate([_card(div=[]), _card(div=[])])
    assert agg["total_divergence"] == 0
    assert agg["verdict"] == "identical"


def test_aggregate_verifier_wins_when_bad_ge_good():
    agg = aggregate([_card(good=1, bad=3)])
    assert agg["verdict"] == "verifier"
    assert agg["good_adds"] == 1 and agg["bad_adds"] == 3


def test_aggregate_generator_wins_when_good_gt_bad():
    agg = aggregate([_card(good=4, bad=1)])
    assert agg["verdict"] == "generator"


def test_aggregate_needs_labels_when_unlabeled_present():
    agg = aggregate([_card(good=2, bad=1, unlabeled=1)])
    assert agg["verdict"] == "needs-labels"
