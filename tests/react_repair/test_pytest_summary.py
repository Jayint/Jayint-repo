import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.pytest_summary import summarize, format_breakdown, Cause


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
    assert "2 × ModuleNotFoundError" in out and "totally_missing_pkg" in out

def test_format_breakdown_caps_top_n_and_notes_remainder():
    causes = [Cause(exc=f"E{i}", detail="d", count=10 - i, outcome="FAILED", module="m.py")
              for i in range(8)]
    out = format_breakdown(causes, top=3)
    assert out.count("×") == 3
    assert "and 5 more" in out
