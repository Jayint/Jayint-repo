"""Parity tests: done_gate._verified_test_run_passed vs maintainer._verified_test_run_passed."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest
from src.envstate.world_model import CommandRecord, TaskReport
from src.envstate.maintainer import _verified_test_run_passed as _maint_gate
from src.envstate.done_gate import _verified_test_run_passed as _dg_gate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(cmd, rc, output=""):
    return CommandRecord(cmd=cmd, rc=rc, output=output)


def _report(*records):
    return TaskReport(
        task_goal="run tests",
        status="done",
        commands=tuple(records),
        learning="",
    )


def _cmp(report, label=""):
    mv = _maint_gate(report)
    dv = _dg_gate(report)
    assert mv == dv, f"parity mismatch [{label}]: maintainer={mv}, done_gate={dv}"
    return dv


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_clean_pass():
    """A clean pytest run with N passed output returns True."""
    r = _report(_rec("pytest -q", 0, "5 passed in 0.12s"))
    assert _cmp(r, "clean_pass") is True


def test_collect_only():
    """pytest --collect-only with rc=0 but only 'collected N items' output returns False."""
    r = _report(_rec("pytest --collect-only", 0, "collected 10 items"))
    assert _cmp(r, "collect_only") is False


def test_all_skipped():
    """All-skipped run (3 skipped, 0 passed, rc=0) returns False."""
    r = _report(_rec("pytest -q", 0, "3 skipped in 0.05s"))
    assert _cmp(r, "all_skipped") is False


def test_venv_wrapped():
    """Venv-wrapped test run (poetry run pytest) is excluded from the gate."""
    r = _report(_rec("poetry run pytest", 0, "5 passed in 0.12s"))
    assert _cmp(r, "venv_wrapped") is False


def test_real_failure():
    """A test run with rc!=0 never satisfies the gate."""
    r = _report(_rec("pytest -q", 1, "2 failed, 3 passed in 0.20s"))
    assert _cmp(r, "real_failure") is False


def test_no_commands():
    """Empty report returns False."""
    r = _report()
    assert _cmp(r, "empty") is False


def test_non_test_command_rc0():
    """A non-test command (ls) even with rc=0 does not pass the gate."""
    r = _report(_rec("ls -la", 0, "total 8\ndrwxr-xr-x ..."))
    assert _cmp(r, "ls") is False


def test_exclusion_flag_deselect():
    """pytest with --deselect is rejected by the anti-gaming guard."""
    r = _report(_rec("pytest --deselect=tests/bad_test.py::test_bad", 0, "1 passed in 0.01s"))
    assert _cmp(r, "deselect") is False


def test_exclusion_flag_ignore():
    """pytest with --ignore= is rejected by the anti-gaming guard."""
    r = _report(_rec("pytest --ignore=examples", 0, "5 passed in 0.12s"))
    assert _cmp(r, "ignore") is False


def test_pytest_100pct_completion():
    """[100%] marker alone (without 'N passed' line) is accepted."""
    r = _report(_rec("pytest -q", 0, "tests/test_foo.py [100%]"))
    assert _cmp(r, "100pct") is True
