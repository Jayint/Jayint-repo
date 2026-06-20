"""Tests for src.pytest_summary — the in-sandbox pytest/unittest summary parser.

The pass_rate formula must match compute_essr.official_pass_rate exactly
(passed / (total_tests - skipped)) so the in-build number is comparable to cleanroom.
"""
from src.pytest_summary import parse_pytest_summary, pass_rate_of, select_best_attempt


def test_pytest_all_passed():
    r = parse_pytest_summary("==== 5 passed in 0.12s ====")
    assert r["summary"]["passed"] == 5
    assert r["summary"]["total_tests"] == 5
    assert r["summary"]["skipped"] == 0
    assert pass_rate_of(r) == 1.0
    assert r["parse_method"] == "in_sandbox_pytest_text"
    assert "5 passed" in r["raw_summary_line"]


def test_pytest_mixed_pass_fail():
    r = parse_pytest_summary("= 1 failed, 4 passed in 0.50s =")
    assert r["summary"]["passed"] == 4
    assert r["summary"]["failed"] == 1
    assert r["summary"]["total_tests"] == 5
    assert abs(pass_rate_of(r) - 0.8) < 1e-9


def test_pytest_skipped_excluded_from_denominator():
    r = parse_pytest_summary("===== 73 passed, 1 failed, 2 skipped in 4.48s =====")
    s = r["summary"]
    assert (s["passed"], s["failed"], s["skipped"]) == (73, 1, 2)
    assert s["total_tests"] == 76
    # effective = total - skipped = 74 ; pass_rate = 73/74
    assert abs(pass_rate_of(r) - (73 / 74)) < 1e-9


def test_pytest_errors_count_in_denominator():
    r = parse_pytest_summary("==== 3 errors, 2 passed in 1.00s ====")
    s = r["summary"]
    assert s["errors"] == 3 and s["passed"] == 2
    assert s["total_tests"] == 5
    assert abs(pass_rate_of(r) - 0.4) < 1e-9


def test_xfailed_and_deselected_handling():
    # xfailed counts toward total; deselected is excluded (not executed).
    r = parse_pytest_summary("==== 8 passed, 2 xfailed, 5 deselected in 1.00s ====")
    s = r["summary"]
    assert s["passed"] == 8 and s["xfailed"] == 2
    assert s["total_tests"] == 10  # deselected NOT counted
    assert abs(pass_rate_of(r) - 0.8) < 1e-9


def test_takes_last_summary_line():
    text = "\n".join([
        "===== 1 passed in 0.01s =====",            # earlier partial run
        "some other logs",
        "===== 9 passed, 1 failed in 2.00s =====",  # final/full run wins
    ])
    r = parse_pytest_summary(text)
    assert r["summary"]["passed"] == 9 and r["summary"]["failed"] == 1


def test_long_duration_summary_with_clock():
    r = parse_pytest_summary("===== 100 passed in 65.43s (0:01:05) =====")
    assert r["summary"]["passed"] == 100
    assert pass_rate_of(r) == 1.0


def test_no_summary_returns_none():
    assert parse_pytest_summary("just some build logs, nothing here") is None
    assert parse_pytest_summary("") is None
    assert parse_pytest_summary(None) is None


def test_pytest_no_tests_ran():
    r = parse_pytest_summary("===== no tests ran in 0.01s =====")
    assert r is not None
    assert r["summary"]["total_tests"] == 0
    assert pass_rate_of(r) == 0.0  # matches the honest scorer (0 effective -> 0.0)


def test_unittest_ok():
    r = parse_pytest_summary("test_foo (t.T) ... ok\nRan 5 tests in 0.10s\n\nOK")
    assert r["summary"]["passed"] == 5
    assert r["summary"]["total_tests"] == 5
    assert pass_rate_of(r) == 1.0
    assert r["parse_method"] == "in_sandbox_unittest_text"


def test_unittest_failed_with_failures_and_errors():
    r = parse_pytest_summary("Ran 10 tests in 0.20s\n\nFAILED (failures=1, errors=2)")
    s = r["summary"]
    assert (s["passed"], s["failed"], s["errors"]) == (7, 1, 2)
    assert s["total_tests"] == 10
    assert abs(pass_rate_of(r) - 0.7) < 1e-9


def test_unittest_ok_with_skips():
    r = parse_pytest_summary("Ran 4 tests in 0.10s\n\nOK (skipped=2)")
    s = r["summary"]
    assert s["skipped"] == 2 and s["passed"] == 2
    assert s["total_tests"] == 4
    assert pass_rate_of(r) == 1.0  # effective = 4 - 2 = 2 ; 2/2


def test_pytest_summary_takes_priority_over_unittest_line():
    r = parse_pytest_summary("Ran 3 tests in 0.1s\n==== 10 passed in 0.5s ====")
    assert r["summary"]["passed"] == 10
    assert r["parse_method"] == "in_sandbox_pytest_text"


def test_error_breakdown_captures_import_errors():
    text = "E   ModuleNotFoundError: No module named 'foo'\n==== 2 errors, 3 passed in 1.0s ===="
    r = parse_pytest_summary(text)
    assert r["error_breakdown"].get("ModuleNotFoundError", 0) >= 1


def test_pass_rate_of_handles_none_and_empty():
    assert pass_rate_of(None) == 0.0
    assert pass_rate_of({}) == 0.0
    assert pass_rate_of({"summary": {}}) == 0.0


def test_select_best_prefers_fullest_suite_over_high_subset():
    # A narrow 1.0 subset must NOT win over the full-suite 0.6 run (RAT-comparable).
    full = {"step_index": 2, "pass_rate": 0.6, "effective_total": 200}
    subset = {"step_index": 5, "pass_rate": 1.0, "effective_total": 3}
    assert select_best_attempt([subset, full]) is full


def test_select_best_tiebreaks_by_pass_rate_then_step():
    early = {"step_index": 1, "pass_rate": 0.3, "effective_total": 50}
    fixed = {"step_index": 7, "pass_rate": 0.95, "effective_total": 50}
    assert select_best_attempt([early, fixed]) is fixed


def test_select_best_empty_is_none():
    assert select_best_attempt([]) is None
    assert select_best_attempt(None) is None
