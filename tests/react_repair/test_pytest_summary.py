import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.pytest_summary import summarize, format_breakdown, Cause


# A REAL pytest run (python -m pytest -q --continue-on-collection-errors): two modules fail to
# import the same missing package (collection ERRORS — the cause is in the traceback, NOT the
# summary line), one test fails an assertion. Captured verbatim so the parser is tested against the
# format pytest actually emits, not a hand-written approximation.
_REAL = """\
F..                                                                      [100%]
==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_missing_a.py ___________________
ImportError while importing test module '/app/tests/test_missing_a.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests/test_missing_a.py:1: in <module>
    import totally_missing_pkg
E   ModuleNotFoundError: No module named 'totally_missing_pkg'
___________________ ERROR collecting tests/test_missing_b.py ___________________
tests/test_missing_b.py:1: in <module>
    import totally_missing_pkg
E   ModuleNotFoundError: No module named 'totally_missing_pkg'
=================================== FAILURES ===================================
__________________________________ test_math ___________________________________

    def test_math():
>       assert 1 == 2
E       assert 1 == 2

tests/test_fail.py:2: AssertionError
=========================== short test summary info ============================
ERROR tests/test_missing_a.py
ERROR tests/test_missing_b.py
FAILED tests/test_fail.py::test_math - assert 1 == 2
1 failed, 2 passed, 2 errors in 0.03s
"""


def test_collection_error_cause_read_from_traceback_not_summary():
    # The dominant real-world case: the ModuleNotFoundError lives in the ERROR traceback block, and
    # the summary line "ERROR tests/test_missing_a.py" has NO reason. The parser must still find it.
    causes = summarize(_REAL)
    top = causes[0]
    assert top.exc == "ModuleNotFoundError"
    assert "totally_missing_pkg" in top.detail
    assert top.count == 2                        # both erroring modules, one root cause
    assert top.outcome == "ERROR"

def test_assertion_failure_type_from_loc_line():
    # test_math's E-line is "E   assert 1 == 2" (no type); the type is on "tests/...:2: AssertionError".
    causes = summarize(_REAL)
    asserts = [c for c in causes if c.exc == "AssertionError"]
    assert len(asserts) == 1 and asserts[0].count == 1 and asserts[0].outcome == "FAILED"

def test_sorted_by_count_descending():
    causes = summarize(_REAL)
    assert [c.count for c in causes] == sorted((c.count for c in causes), reverse=True)
    assert causes[0].exc == "ModuleNotFoundError"          # 2 > 1

def test_module_extracted_for_display():
    causes = summarize(_REAL)
    assert causes[0].module.endswith(".py") and "test_missing_a" in causes[0].module
    assert [c for c in causes if c.exc == "AssertionError"][0].module.endswith("test_fail.py")

def test_normalizes_assertion_values_so_they_group():
    out = ("=== FAILURES ===\n"
           "___ test_a ___\ntests/t.py:1: AssertionError\n"
           "___ test_b ___\ntests/t.py:2: AssertionError\n")
    causes = summarize(out)
    assert len(causes) == 1 and causes[0].exc == "AssertionError" and causes[0].count == 2

def test_no_double_count_when_e_line_and_loc_line_both_present():
    # a runtime ModuleNotFoundError has BOTH "E   ModuleNotFoundError: ..." and a
    # "path:line: ModuleNotFoundError" line — the block must count as ONE, not two.
    out = ("=== FAILURES ===\n"
           "___ test_x ___\n"
           "        import missing\n"
           "E       ModuleNotFoundError: No module named 'missing'\n"
           "tests/test_x.py:5: ModuleNotFoundError\n")
    causes = summarize(out)
    assert len(causes) == 1 and causes[0].count == 1
    assert causes[0].exc == "ModuleNotFoundError" and "missing" in causes[0].detail

def test_pytest_timeout_failed_is_captured():
    # pytest-timeout reports "E   Failed: Timeout (>Ns)..." — the exception name IS the suffix
    # ("Failed") with no prefix. Regression from the timeout e2e: the parser must still capture it.
    out = ("=================================== FAILURES ===================================\n"
           "___ test_hangs ___\n"
           "    def test_hangs():\n"
           ">       time.sleep(60)\n"
           "E       Failed: Timeout (>3.0s) from pytest-timeout.\n"
           "tests/test_hang.py:4: Failed\n")
    causes = summarize(out)
    assert len(causes) == 1
    assert causes[0].exc == "Failed" and "Timeout" in causes[0].detail

def test_empty_or_all_passed_returns_no_causes():
    assert summarize("") == []
    assert summarize("....                        [100%]\n5 passed in 0.1s\n") == []

def test_ignores_traceback_frame_lines_that_are_not_exceptions():
    # "tests/x.py:1: in <module>" and "/usr/lib/.../__init__.py:88: in import_module" must NOT be
    # read as causes (they end in "in <frame>", not an exception type).
    causes = summarize(_REAL)
    assert all("in " not in c.exc for c in causes)
    assert all(c.exc in ("ModuleNotFoundError", "AssertionError") for c in causes)


def test_format_breakdown_top_line_shape():
    out = format_breakdown(summarize(_REAL))
    assert "2 × [collect] ModuleNotFoundError" in out and "totally_missing_pkg" in out


def test_format_breakdown_tags_collect_and_run():
    # Each row is prefixed with the real pytest PHASE (Cause.phase), not a guess derived from
    # `outcome`: a collection error and a fixture-setup error BOTH have outcome="ERROR" but are
    # different problems (per-file vs per-test), so the tag now names the phase directly.
    causes = [
        Cause(exc="ModuleNotFoundError", detail="No module named 'psycopg2'",
              count=3, outcome="ERROR", module="tests/test_db.py", phase="collect"),
        Cause(exc="AssertionError", detail="", count=5, outcome="FAILED",
              module="tests/test_logic.py", phase="call"),
        Cause(exc="RuntimeError", detail="db down", count=2, outcome="ERROR",
              module="tests/test_db.py", phase="setup"),
    ]
    out = format_breakdown(causes)
    assert "3 × [collect] ModuleNotFoundError: No module named 'psycopg2'" in out
    assert "5 × [call] AssertionError" in out
    # A SETUP error is an ERROR outcome but a RUN-phase failure — the old `outcome`-derived tag
    # called this [collect], which was wrong.
    assert "2 × [setup] RuntimeError" in out

def test_format_breakdown_omits_file_path_to_avoid_navigation():
    # The rendered row is pure triage (count + type + message) — no representative file. Naming a
    # test file lures the agent into `cat`-ing it (wasted navigation), and the actionable identifier
    # is already in the message (the missing module name). The file stays on the Cause as metadata.
    causes = summarize(_REAL)
    out = format_breakdown(causes)
    assert "(e.g." not in out and ".py" not in out
    assert causes[0].module.endswith(".py")            # still available as metadata on the Cause

def test_format_breakdown_caps_top_n_and_notes_remainder():
    causes = [Cause(exc=f"E{i}", detail="d", count=10 - i, outcome="FAILED", module="m.py")
              for i in range(8)]
    out = format_breakdown(causes, top=3)
    assert out.count("×") == 3
    assert "and 5 more" in out


# A REAL pytest run (python3 -m pytest -q --continue-on-collection-errors, captured verbatim from a
# throwaway repo with an un-importable module, a fixture that raises in setup, one that raises in
# teardown, and a failing assertion) with BOTH kinds of "ERROR": a collection error (per-FILE, the
# tests inside never became items) and a setup error (per-TEST, the test WAS collected but its
# fixture blew up). Both banners start with "ERROR", which is why the old
# `title.startswith("ERROR")` bucketing conflated them. (The ImportError detail's absolute path is
# genericized to `/app/...` per this file's convention for `_REAL` above — every banner, `E` line,
# and traceback terminator is untouched.)
_PHASES = """\
E.EF                                                                     [100%]
==================================== ERRORS ====================================
____________________ ERROR collecting tests/test_missing.py ____________________
ImportError while importing test module '/app/tests/test_missing.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.11/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests/test_missing.py:1: in <module>
    import totally_missing_pkg
E   ModuleNotFoundError: No module named 'totally_missing_pkg'
_________________________ ERROR at setup of test_query _________________________

    @pytest.fixture
    def conn():
>       raise RuntimeError("db down")
E       RuntimeError: db down

tests/test_db.py:6: RuntimeError
______________________ ERROR at teardown of test_cleanup _______________________

    @pytest.fixture
    def cleanup():
        yield
>       raise OSError("could not remove tmpdir")
E       OSError: could not remove tmpdir

tests/test_db.py:16: OSError
=================================== FAILURES ===================================
__________________________________ test_math ___________________________________

    def test_math():
>       assert 1 == 2
E       assert 1 == 2

tests/test_fail.py:2: AssertionError
=========================== short test summary info ============================
FAILED tests/test_fail.py::test_math - assert 1 == 2
ERROR tests/test_missing.py
ERROR tests/test_db.py::test_query - RuntimeError: db down
ERROR tests/test_db.py::test_cleanup - OSError: could not remove tmpdir
1 failed, 1 passed, 3 errors in 0.04s
"""


def _by_exc(causes, exc):
    return next(c for c in causes if c.exc == exc)


def test_phase_collect_vs_setup_vs_teardown_vs_call():
    causes = summarize(_PHASES)
    assert _by_exc(causes, "ModuleNotFoundError").phase == "collect"
    assert _by_exc(causes, "RuntimeError").phase == "setup"
    assert _by_exc(causes, "OSError").phase == "teardown"
    assert _by_exc(causes, "AssertionError").phase == "call"


def test_outcome_is_unchanged_by_the_phase_split():
    # Backwards compatibility: `outcome` keeps its old values. Only `phase` is new.
    causes = summarize(_PHASES)
    assert _by_exc(causes, "ModuleNotFoundError").outcome == "ERROR"
    assert _by_exc(causes, "RuntimeError").outcome == "ERROR"
    assert _by_exc(causes, "AssertionError").outcome == "FAILED"


def test_error_at_call_of_keeps_outcome_ERROR_with_phase_call():
    # pytest builds the banner as f"ERROR at {rep.when} of ..." for ANY report its `error`
    # category owns, and a plugin can put a CALL report in that category via the
    # pytest_report_teststatus hook. So `ERROR at call of ...` is a real, valid banner:
    # phase is "call" (it IS the call phase) but the section is ERRORS, so outcome is "ERROR".
    # Deriving outcome from phase — "FAILED" iff phase == "call" — silently flips it to FAILED.
    out = """\
==================================== ERRORS ====================================
_____________________ ERROR at call of test_plugin_case _______________________
E       RuntimeError: boom

tests/test_x.py:7: RuntimeError
"""
    (cause,) = summarize(out)
    assert cause.phase == "call"
    assert cause.outcome == "ERROR"


def test_format_breakdown_tags_setup_as_setup_not_collect():
    # The old code tagged a setup error `[collect]` because its banner starts with "ERROR".
    out = format_breakdown(summarize(_PHASES))
    assert "[setup] RuntimeError" in out
    assert "[collect] ModuleNotFoundError" in out
    assert "[collect] RuntimeError" not in out


def test_same_exception_in_different_phases_does_not_group():
    # A ModuleNotFoundError at collection and one raised inside a test body are different
    # problems (one has an env fix, one does not), so they must not share a Cause.
    out = """\
==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_a.py ____________________
E   ModuleNotFoundError: No module named 'zzz'
=================================== FAILURES ===================================
__________________________________ test_b ___________________________________
E       ModuleNotFoundError: No module named 'zzz'

tests/test_b.py:9: ModuleNotFoundError
"""
    causes = summarize(out)
    phases = sorted(c.phase for c in causes if c.exc == "ModuleNotFoundError")
    assert phases == ["call", "collect"]
