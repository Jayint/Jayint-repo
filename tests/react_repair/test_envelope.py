import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.observe import edit_result, run_envelope, strip_legacy_header


# --- the command envelope: `$ cmd → result`, the atom of an agent transcript ---------------
def test_build_failure_shows_the_command_and_the_halting_line():
    e = run_envelope({"build_ok": False, "failing_command": "pip install psycopg2", "lineno": 12})
    assert "$ bash setup.sh" in e
    assert "exit 1 — halted at line 12: `pip install psycopg2`" in e

def test_every_run_states_the_reset():
    # The arm's strangest property (WHOLE script re-runs from a clean container each turn) was only
    # ever explained in system prose. Showing it on the command line makes it self-evident.
    assert "EVERY turn" in run_envelope({"build_ok": True, "ran_tests": False})

def test_green_build_without_tests_is_just_exit_0():
    e = run_envelope({"build_ok": True, "ran_tests": False})
    assert "exit 0" in e and "pytest" not in e

def test_pytest_counts_replace_the_fake_ratio():
    # THE bug this fixes: `BUILD OK. TESTS 0/5 passed.` on a repo with 297 tests, none of which ran.
    # The "5" was five unimportable MODULES. Report what pytest reported.
    e = run_envelope({"build_ok": True, "ran_tests": True, "passed": 0, "failed": 0,
                      "errors": 5, "skipped": 0, "collected": 0})
    assert "$ python -m pytest -q" in e
    assert "0 passed, 0 failed, 5 collection errors — no tests ran" in e
    assert "0/5" not in e                              # the ratio that wasn't true

def test_real_failures_report_passed_and_failed():
    e = run_envelope({"build_ok": True, "ran_tests": True, "passed": 41, "failed": 9,
                      "errors": 0, "skipped": 0, "collected": 50})
    assert "41 passed, 9 failed" in e and "no tests ran" not in e

def test_silent_skip_gap_is_surfaced():
    e = run_envelope({"build_ok": True, "ran_tests": True, "passed": 199, "failed": 1,
                      "errors": 0, "skipped": 50, "collected": 250})
    assert "50 skipped" in e and "250 tests collected" in e

def test_no_pytest_exit_code_is_invented():
    # We never captured pytest's rc (TestOutcome.ok is the HOST's 80% gate verdict, not pytest's
    # return value). Printing one would be the same sin as the fake ratio.
    e = run_envelope({"build_ok": True, "ran_tests": True, "passed": 0, "failed": 3, "errors": 0})
    assert e.count("exit") == 1                        # exactly one: the BUILD's, which we do know

def test_singular_collection_error():
    e = run_envelope({"build_ok": True, "ran_tests": True, "passed": 0, "failed": 0, "errors": 1})
    assert "1 collection error —" in e and "errors" not in e.split("pytest -q")[1]

def test_missing_outcome_degrades_to_a_build_failure_not_a_crash():
    assert "$ bash setup.sh" in run_envelope(None)


# --- the legacy header is stripped (the envelope states it in the tool's voice) -------------
def test_strips_build_ok_header():
    assert strip_legacy_header("BUILD OK. TESTS 0/5 passed.\nreal output\n") == "real output\n"

def test_strips_build_ok_header_with_collected_suffix():
    assert strip_legacy_header("BUILD OK. TESTS 40/200 passed (250 collected).\nx") == "x"

def test_strips_build_failed_header_with_and_without_lineno():
    assert strip_legacy_header("BUILD FAILED at `pip install x` (line 7):\nboom") == "boom"
    assert strip_legacy_header("BUILD FAILED at `pip install x`:\nboom") == "boom"

def test_leaves_a_body_with_no_header_alone():
    assert strip_legacy_header("just output\n") == "just output\n"
    assert strip_legacy_header(None) == ""


# --- the edit tool result: where line numbers are still TRUE --------------------------------
def test_edit_result_echoes_the_spliced_lines_at_their_numbers():
    r = edit_result({"kind": "edit", "verb": "replace", "start": 7, "end": 8,
                     "content": "python -m pip install -e .[tests]"})
    assert r == "setup.sh updated:\n  7| python -m pip install -e .[tests]"

def test_edit_result_numbers_a_multiline_insert_consecutively():
    r = edit_result({"kind": "edit", "verb": "insert", "start": 5, "end": 5,
                     "content": "apt-get update\napt-get install -y libpq-dev"})
    assert "  5| apt-get update" in r and "  6| apt-get install -y libpq-dev" in r

def test_edit_result_caps_a_huge_insert():
    r = edit_result({"kind": "edit", "verb": "insert", "start": 1, "end": 1,
                     "content": "\n".join(f"line{i}" for i in range(20))})
    assert "(+14 more lines)" in r

def test_edit_result_for_delete_names_the_span():
    assert edit_result({"kind": "edit", "verb": "delete", "start": 7, "end": 9}) \
        == "setup.sh updated — deleted lines 7-9."
    assert edit_result({"kind": "edit", "verb": "delete", "start": 7, "end": 7}) \
        == "setup.sh updated — deleted line 7."

def test_edit_result_is_none_for_non_edits():
    assert edit_result({"kind": "explore", "command": "ls"}) is None
    assert edit_result(None) is None
