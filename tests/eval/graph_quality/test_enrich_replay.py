"""Unit tests for the enrich-replay grader (plan Task 3, Step 1 + honesty extras).

The five tests through `test_aggregate_reports_PER_SLICE_and_refuses_a_bare_aggregate`
are the ones the plan prescribes verbatim (docs/superpowers/plans/2026-07-13-graph-
quality-eval.md, Task 3 Step 1). The rest guard the HARD CONSTRAINTS the task adds on
top: the offline resolver must fail HONESTLY (never silently pass or silently count as
"not discovered"), and hallucination/owner-anchoring must be independently falsifiable.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.eval.graph_quality.corpus import Label, Pair, load_pairs
from src.eval.graph_quality.enrich_replay import (
    PairScore, _OfflineExecutor, aggregate, score_pair,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESULTS = _REPO_ROOT / "outputs" / "repo2run_benchmark" / "results"
_ARTIFACTS = _REPO_ROOT / "outputs" / "repo2run_benchmark" / "eval_artifacts"


def _pg_config_pair() -> Pair:
    return Pair(
        repo="x", round_index=0,
        stderr="#10 12.3 Error: pg_config executable not found.\n",
        before="FROM python:3.10\nRUN pip install psycopg2==2.9.12\n",
        after="FROM python:3.10\nRUN apt-get install -y libpq-dev\n"
              "RUN pip install psycopg2==2.9.12\n",
        label=Label(apt=frozenset({"libpq-dev"}), pip=frozenset(), kind="os-package"),
    )


def test_a_pg_config_failure_PREEMPTS_the_libpq_dev_repair():
    """THE case. The error names pg_config; the repair the agent actually wrote added
    `apt-get install -y libpq-dev`. enrich must connect the two WITHOUT being told the
    answer."""
    s = score_pair(_pg_config_pair())
    assert s.preempted is True
    assert "binary:pg_config" in s.discovered


def test_a_missing_python_module_PREEMPTS_the_pip_repair():
    pair = Pair(
        repo="x", round_index=0,
        stderr="ModuleNotFoundError: No module named 'yaml'\n",
        before="FROM python:3.10\n",
        after="FROM python:3.10\nRUN pip install PyYAML\n",
        label=Label(apt=frozenset(), pip=frozenset({"PyYAML"}), kind="python-package"),
    )
    assert score_pair(pair).preempted is True


def test_the_NEGATIVE_CONTROL_produces_no_nodes_at_all():
    """A test-body assertion failure has NO environment fix. Any node here is a false
    positive, and the phase gate is supposed to make one structurally impossible."""
    pair = Pair(
        repo="x", round_index=0,
        stderr="E   AssertionError: assert 3 == 4\n",
        before="FROM python:3.10\n", after="FROM python:3.10\n",
        label=Label(frozenset(), frozenset(), "not-an-env-fix"),
    )
    s = score_pair(pair)
    assert s.discovered == frozenset()


def test_replaying_the_same_error_twice_is_idempotent():
    pair = _pg_config_pair()
    once = score_pair(pair)
    twice = score_pair(pair)
    assert once.discovered == twice.discovered
    assert once.preempted == twice.preempted


def test_aggregate_reports_PER_SLICE_and_refuses_a_bare_aggregate():
    # A 90% average would have concealed pg_config at 0% -- the case the arm exists for.
    scores = [
        PairScore(repo="a", kind="os-package", discovered=frozenset({"binary:pg_config"}),
                  preempted=True, hallucinated=frozenset(), owner_anchored=True),
        PairScore(repo="b", kind="python-package", discovered=frozenset(),
                  preempted=False, hallucinated=frozenset(), owner_anchored=False),
        PairScore(repo="c", kind="not-an-env-fix", discovered=frozenset(),
                  preempted=False, hallucinated=frozenset(), owner_anchored=False),
    ]
    out = aggregate(scores)
    assert set(out["by_kind"]) >= {"os-package", "python-package"}
    assert "preemption_rate" in out["by_kind"]["os-package"]
    assert out["by_kind"]["os-package"]["preemption_rate"] == "1/1"
    assert out["by_kind"]["python-package"]["preemption_rate"] == "0/1"
    # not-an-env-fix must NEVER get a preemption_rate -- it is not in that denominator.
    assert out["by_kind"]["not-an-env-fix"]["preemption_rate"] is None
    assert out["negative_control"]["n"] == 1
    assert out["negative_control"]["produced_a_node"] == 0
    assert "denominator_warning" in out  # never a bare rate with no size caveat


# --------------------------------------------------------------------------- #
# HARD CONSTRAINT extras: offline resolver honesty, hallucination, owner-anchoring.
# --------------------------------------------------------------------------- #

def test_offline_executor_never_touches_network_and_fails_HONESTLY():
    """The resolver's apt-file fallback needs a live container; `_OfflineExecutor`
    must fail that cleanly (never crash, never silently succeed) so a table-miss is
    visibly attributable to "the resolver could not be reached"."""
    ex = _OfflineExecutor()
    result = ex.run("apt-file search some/path")
    assert result.returncode != 0
    assert ex.calls == ["apt-file search some/path"]


def test_a_capability_fix_OUTSIDE_the_offline_table_is_UNRESOLVED_not_a_silent_miss():
    """`tesseract-ocr` is not in `os_resolver.PROVIDER_TABLE`. Discovering the TOOL is
    still real graph behaviour; failing to invent an apt name for it OFFLINE must show
    up as `capability_unresolved`, not get silently folded into "preempted=False" where
    it would be indistinguishable from "the graph never even discovered it"."""
    pair = Pair(
        repo="x", round_index=0,
        stderr="tesseract: command not found\n",
        before="FROM python:3.10\n", after="FROM python:3.10\nRUN apt-get install -y tesseract-ocr\n",
        label=Label(apt=frozenset({"tesseract-ocr"}), pip=frozenset(), kind="os-package"),
    )
    s = score_pair(pair)
    assert "binary:tesseract" in s.discovered
    assert s.preempted is False, "must not be silently credited for a fix the offline resolver never produced"
    assert "binary:tesseract" in s.capability_unresolved, (
        "the miss must be attributable to the resolver being unreachable, not to a "
        "classifier failure"
    )


def test_hallucinated_flags_a_node_whose_fix_never_appears_in_a_working_dockerfile():
    """A discovered node with a resolved fix that this repo's Dockerfile never actually
    installs is a genuine false positive -- the counterweight to attribution coverage."""
    pair = Pair(
        repo="x", round_index=0,
        stderr="#1 Error: pg_config executable not found.\n",
        before="FROM python:3.10\nRUN pip install psycopg2==2.9.12\n",
        # The repair that ACTUALLY worked never touched libpq-dev -- e.g. the repo
        # vendored a prebuilt wheel and the real fix was unrelated. The classifier
        # still (wrongly, for this synthetic case) proposes libpq-dev.
        after="FROM python:3.10\nRUN pip install psycopg2==2.9.12\nRUN pip install some-other-fix\n",
        label=Label(apt=frozenset(), pip=frozenset({"some-other-fix"}), kind="python-package"),
    )
    s = score_pair(pair)
    assert "binary:pg_config" in s.discovered
    assert "binary:pg_config" in s.hallucinated
    assert s.preempted is False


def test_owner_anchored_true_when_the_seeded_package_is_the_sole_pip_install():
    s = score_pair(_pg_config_pair())
    assert s.owner_anchored is True


def test_owner_anchored_false_when_before_declares_MULTIPLE_packages():
    """With more than one declared package, `owner_node_for_command` cannot
    unambiguously pick a culprit from the label alone -- the discovery legitimately
    falls back to TEST_NODE_ID rather than guessing. This mirrors the majority shape
    of the real corpus (repo2run's own `poetry`/`pytest`/`pytest-xdist` boilerplate is
    ALWAYS declared alongside the real dependencies)."""
    pair = Pair(
        repo="x", round_index=0,
        stderr="ModuleNotFoundError: No module named 'yaml'\n",
        before="FROM python:3.10\nRUN pip install poetry pytest pytest-xdist\n",
        after="FROM python:3.10\nRUN pip install poetry pytest pytest-xdist\nRUN pip install PyYAML\n",
        label=Label(apt=frozenset(), pip=frozenset({"PyYAML"}), kind="python-package"),
    )
    s = score_pair(pair)
    assert s.preempted is True          # coverage is unaffected by owner ambiguity
    assert s.owner_anchored is False    # but depth/attribution honestly is


# --------------------------------------------------------------------------- #
# The real corpus. Skipped (not failed) when the 587MB corpus isn't on disk.
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not _RESULTS.is_dir(), reason="repo2run_benchmark corpus not on disk")
def test_replay_over_the_REAL_corpus_reports_a_tiny_honest_denominator():
    """Guards the instrument: if a refactor silently breaks scoring against the real
    corpus, this would raise or aggregate would collapse to n=0 without anyone noticing
    the enrich CLI's own printed report."""
    pairs = load_pairs(str(_RESULTS), str(_ARTIFACTS))
    scores = [score_pair(p, artifacts_dir=str(_ARTIFACTS)) for p in pairs]
    agg = aggregate(scores)
    assert agg["n_total"] == len(pairs)
    # The spec's own verified finding: 111 pairs, 14 in scope (3 os-package + 11
    # python-package). Assert the SHAPE, not a moving exact count, so an unrelated
    # corpus change doesn't spuriously fail this test.
    assert agg["n_in_scope"] < 30, "the in-scope denominator is supposed to be tiny"
    assert "not-an-env-fix" in agg["by_kind"]
    assert agg["negative_control"]["n"] > agg["n_in_scope"], (
        "not-an-env-fix should dwarf the in-scope population -- that IS §2.2's finding"
    )


# --------------------------------------------------------------------------- #
# Regression: the STREAM must match production (gpt-5.6-terra review of e71cb21)
#
# The grader originally sent EVERY pair down enrich's build-stream --
# RunResult(ok=False, causes=[]) -- including the ~half of this corpus that failed at TEST
# time. enrich's pytest-stream is phase-gated on `causes`, so with causes=[] it never fired:
# the graph was handed nothing and scored a false miss. The headline read 1/14 when the truth
# was 2/14. A grader that slanders the thing it measures is worse than no grader.
# --------------------------------------------------------------------------- #

def _pair(stderr, *, failed_at, before="FROM python:3.10\n", after="", label=None):
    from src.eval.graph_quality.corpus import Label
    return Pair(repo="x", round_index=1, stderr=stderr, before=before, after=after or before,
                label=label or Label(frozenset(), frozenset({"Pillow"}), "python-package"),
                failed_at=failed_at)


def test_a_TEST_stream_failure_reaches_the_graph_through_the_pytest_causes_path():
    """THE regression. A pytest collection error must produce a node.

    Real shape, from arvindrajan92__DTrOCR: a collection blob with TWO ModuleNotFoundErrors.
    Down the build-stream path this discovers nothing at all.
    """
    stderr = (
        "==================================== ERRORS ====================================\n"
        "_____________ ERROR collecting tests/test_processor.py _____________\n"
        "tests/test_processor.py:3: in <module>\n"
        "    import torch\n"
        "E   ModuleNotFoundError: No module named 'torch'\n"
        "_____________ ERROR collecting tests/test_model.py _____________\n"
        "tests/test_model.py:1: in <module>\n"
        "    from PIL import Image\n"
        "E   ModuleNotFoundError: No module named 'PIL'\n"
    )
    score = score_pair(_pair(stderr, failed_at="test"))
    assert score.discovered, "the pytest-causes stream never fired -- the graph got nothing"
    assert score.preempted is True, "PIL -> Pillow is in the curated import table; this is a real hit"


def test_the_SAME_text_down_the_BUILD_stream_LOSES_the_second_of_two_collection_errors():
    """A demonstration of the bug's exact MECHANISM, so nobody 'simplifies' the routing away.

    It is narrower than it first looks. The build stream is NOT blind to a lone
    ModuleNotFoundError -- its classifier reads raw output too. It is blind to the SECOND one:
    `failure_classifier.MODULE_NOT_FOUND_RE` uses `.search()`, not `.finditer()`, so exactly one
    match per blob is ever seen. In DTrOCR's real collection output that first match is `torch`
    (deliberately unmapped -> no node), and `PIL` -- which IS in the curated import table and
    would have pre-empted the label cleanly -- never gets looked at.

    The pytest stream escapes this because `summarize()` splits the blob into ONE CAUSE PER
    COLLECTION ERROR, so PIL arrives as its own observation.

    (The `.search()` narrowing is a real latent bug in `src/python_deps/failure_classifier.py`,
    reported, deliberately NOT fixed here: this eval must not edit the code it measures.)
    """
    two_errors = (
        "==================================== ERRORS ====================================\n"
        "_____________ ERROR collecting tests/test_processor.py _____________\n"
        "tests/test_processor.py:3: in <module>\n"
        "    import torch\n"
        "E   ModuleNotFoundError: No module named 'torch'\n"
        "_____________ ERROR collecting tests/test_model.py _____________\n"
        "tests/test_model.py:1: in <module>\n"
        "    from PIL import Image\n"
        "E   ModuleNotFoundError: No module named 'PIL'\n"
    )
    routed_as_test = score_pair(_pair(two_errors, failed_at="test"))
    routed_as_build = score_pair(_pair(two_errors, failed_at="build"))

    assert routed_as_test.preempted is True, "the production path finds Pillow behind torch"
    assert routed_as_build.preempted is False, (
        "the build stream sees only the FIRST ModuleNotFoundError (torch, unmapped). If this "
        "starts passing, .search() became .finditer() in failure_classifier -- which is a FIX, "
        "and this eval's python-package number should be re-measured."
    )
