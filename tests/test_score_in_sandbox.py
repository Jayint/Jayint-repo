"""Tests for scripts/score_in_sandbox.py — the in-build (live-container) scorer.

Verifies the two artifact-source paths (<instance>.json and agent_run_summary.json),
the >=0.8 in-build-success bar, the executed/partial/not-executed buckets, and that
pass_rate is recomputed from raw (not trusted from the stored value).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import score_in_sandbox as sis  # noqa: E402


def _attempt(passed, failed=0, errors=0, skipped=0, command="pytest"):
    total = passed + failed + errors + skipped
    return {
        "step_index": 1,
        "command": command,
        "pass_rate": 999.0,  # deliberately wrong: scorer must recompute, not trust this
        "result": {
            "summary": {"total_tests": total, "passed": passed, "failed": failed,
                        "errors": errors, "skipped": skipped},
            "error_breakdown": {},
            "parse_method": "in_sandbox_pytest_text",
        },
    }


def _write_repo(root, owner, name, *, instance_best=None, summary_best="UNSET"):
    d = os.path.join(root, "output", owner, name)
    os.makedirs(d, exist_ok=True)
    json.dump({"status": "ok"}, open(os.path.join(d, "_result_row.json"), "w"))
    if instance_best is not None:
        inst = {"instance_id": f"{owner}__{name}", "logs": {"best_in_sandbox_test_result": instance_best}}
        json.dump(inst, open(os.path.join(d, f"{owner}__{name}.json"), "w"))
    if summary_best != "UNSET":
        json.dump({"best_in_sandbox_test_result": summary_best},
                  open(os.path.join(d, "agent_run_summary.json"), "w"))
    return d


def test_instance_json_path_and_080_bar(tmp_path):
    root = str(tmp_path)
    _write_repo(root, "o", "fullpass", instance_best=_attempt(10))          # 1.0 -> success
    _write_repo(root, "o", "exactly80", instance_best=_attempt(8, failed=2))  # 0.8 -> success
    _write_repo(root, "o", "partial", instance_best=_attempt(6, failed=4))   # 0.6 -> partial
    _write_repo(root, "o", "nocapture", instance_best=None)                  # not executed

    agg = sis.score_in_sandbox(root)
    assert agg["n"] == 4
    assert agg["executed"] == 3
    assert agg["in_build_success"] == 2  # 1.0 and exactly-0.8
    assert agg["full_pass"] == 1
    assert "o/partial" in [name for name, _ in agg["partial_repos"]]
    assert agg["not_executed_repos"] == ["o/nocapture"]


def test_recomputes_pass_rate_not_trusting_stored(tmp_path):
    root = str(tmp_path)
    # stored attempt pass_rate is 999.0 but real result is 6/10 = 0.6 -> partial, not success.
    _write_repo(root, "o", "r", instance_best=_attempt(6, failed=4))
    agg = sis.score_in_sandbox(root)
    assert agg["rows"][0]["pass_rate"] == 0.6
    assert agg["in_build_success"] == 0


def test_run_summary_fallback_when_no_instance_json(tmp_path):
    root = str(tmp_path)
    _write_repo(root, "o", "r", instance_best=None, summary_best=_attempt(9, failed=1))
    agg = sis.score_in_sandbox(root)
    assert agg["executed"] == 1
    assert agg["in_build_success"] == 1  # 0.9


def test_skipped_excluded_from_denominator(tmp_path):
    root = str(tmp_path)
    # 8 passed, 0 failed, 92 skipped -> effective 8 -> pass_rate 1.0 -> success
    _write_repo(root, "o", "r", instance_best=_attempt(8, skipped=92))
    agg = sis.score_in_sandbox(root)
    assert agg["rows"][0]["pass_rate"] == 1.0
    assert agg["in_build_success"] == 1


def test_glob_only_layout_without_result_rows(tmp_path):
    # No output/**/_result_row.json — only agent_run_summary.json files scattered.
    root = str(tmp_path)
    d = os.path.join(root, "workplace", "multi_docker_eval_o__r")
    os.makedirs(d)
    json.dump({"best_in_sandbox_test_result": _attempt(10)},
              open(os.path.join(d, "agent_run_summary.json"), "w"))
    agg = sis.score_in_sandbox(root)
    assert agg["n"] == 1 and agg["executed"] == 1 and agg["in_build_success"] == 1
