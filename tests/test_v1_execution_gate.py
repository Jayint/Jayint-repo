# tests/test_v1_execution_gate.py
"""RED tests for Phase 1: execution-aware done-gate.

These tests are written FIRST and must fail before the implementation.

Spec (Phase 1 brief):
  A report command is a verified passing test run iff ALL hold:
    1. rec.rc == 0
    2. _DETECTOR.is_test_command(rec.cmd) is True
    3. NOT venv-wrapped (no poetry run / pipenv run / hatch run / conda run / pdm run)
    4. _DETECTOR.analyze_test_run(rec.cmd, rec.output)["is_effective_test_run"] is True
    5. _shows_execution(output) is True — output shows "N passed" or "Ran N tests"
       (a pure collect-only "collected N items" MUST return False)

The crux (condition 5): a clean `pytest --collect-only` (output "collected 182 items",
no "passed") MUST NOT finalize the agent.
"""
from __future__ import annotations

import pytest

from src.envstate.world_model import (
    CommandRecord,
    TaskReport,
    initial_map,
    merge_map,
)
from src.envstate.maintainer import parse_v1_maintainer_reply


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_map():
    return initial_map(
        base_image="python:3.12-slim",
        workdir="/app",
        language="python",
        build_system="pip",
        repo_layout=(),
    )


def _report(*cmds: tuple[str, int, str]) -> TaskReport:
    """Build a minimal TaskReport from (cmd, rc, output) tuples."""
    return TaskReport(
        task_goal="test",
        status="done",
        commands=tuple(CommandRecord(c, r, o) for c, r, o in cmds),
        learning="",
    )


_VALID_REPLY = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'


def _done(cmd: str, rc: int = 0, output: str = "") -> bool:
    """Return done_flag after running parse_v1_maintainer_reply for a single cmd record."""
    return parse_v1_maintainer_reply(_VALID_REPLY, _base_map(), _report((cmd, rc, output))).done_flag


# ---------------------------------------------------------------------------
# The crux: collect-only must NOT finalize
# ---------------------------------------------------------------------------

def test_collect_only_does_NOT_set_done_flag():
    """Pure pytest --collect-only (no 'passed' in output) must NOT finalize."""
    assert _done(
        "pytest --collect-only -q --disable-warnings",
        rc=0,
        output="collected 182 items",
    ) is False


def test_collect_only_co_alias_does_NOT_set_done_flag():
    """--co alias for collect-only must also NOT finalize."""
    assert _done(
        "pytest --co -q",
        rc=0,
        output="collected 7 items / 7 selected",
    ) is False


def test_collect_only_with_python_m_does_NOT_set_done_flag():
    """python -m pytest --collect-only must NOT finalize."""
    assert _done(
        "python -m pytest --collect-only -q",
        rc=0,
        output="collected 55 items",
    ) is False


# ---------------------------------------------------------------------------
# Real execution pass -> finalizes
# ---------------------------------------------------------------------------

def test_real_execution_pass_sets_done_flag():
    """python -m pytest -q with 'N passed' output MUST finalize."""
    assert _done(
        "python -m pytest -q",
        rc=0,
        output="182 passed in 4.0s",
    ) is True


def test_pytest_passed_output_sets_done_flag():
    """Plain pytest with passed output MUST finalize."""
    assert _done(
        "pytest -q",
        rc=0,
        output="8 passed, 2 warnings in 1.2s",
    ) is True


def test_unittest_ran_output_sets_done_flag():
    """unittest-style 'Ran N tests' output MUST finalize."""
    assert _done(
        "python -m pytest -q",
        rc=0,
        output="Ran 12 tests in 0.345s\n\nOK",
    ) is True


# ---------------------------------------------------------------------------
# Failure signals -> no finalize
# ---------------------------------------------------------------------------

def test_failed_output_does_NOT_set_done_flag():
    """Output with 'N failed' must not finalize even if rc=0."""
    assert _done(
        "pytest -q",
        rc=0,
        output="182 failed in 4.0s",
    ) is False


def test_connection_error_output_does_NOT_set_done_flag():
    """Output with 'N errors' (e.g. rq/redis scenario) must not finalize."""
    assert _done(
        "pytest -q",
        rc=0,
        output="345 errors in 2.1s",
    ) is False


def test_nonzero_rc_does_NOT_set_done_flag():
    """Even with a 'passed' output, rc != 0 must not finalize."""
    assert _done(
        "python -m pytest -q",
        rc=1,
        output="3 passed, 1 failed in 0.5s",
    ) is False


# ---------------------------------------------------------------------------
# Venv-wrapped commands -> no finalize (Phase 1 includes wrapper rejection)
# ---------------------------------------------------------------------------

def test_poetry_run_pytest_does_NOT_set_done_flag():
    """poetry run pytest N passed must not finalize (venv-wrapped)."""
    assert _done(
        "poetry run pytest -q",
        rc=0,
        output="8 passed in 1.0s",
    ) is False


def test_pipenv_run_pytest_does_NOT_set_done_flag():
    """pipenv run pytest must not finalize."""
    assert _done(
        "pipenv run pytest -q",
        rc=0,
        output="3 passed in 0.5s",
    ) is False


def test_hatch_run_pytest_does_NOT_set_done_flag():
    """hatch run pytest must not finalize."""
    assert _done(
        "hatch run pytest -q",
        rc=0,
        output="5 passed in 0.8s",
    ) is False


def test_conda_run_pytest_does_NOT_set_done_flag():
    """conda run pytest must not finalize."""
    assert _done(
        "conda run pytest -q",
        rc=0,
        output="2 passed in 0.3s",
    ) is False


def test_pdm_run_pytest_does_NOT_set_done_flag():
    """pdm run pytest must not finalize."""
    assert _done(
        "pdm run pytest -q",
        rc=0,
        output="4 passed in 0.6s",
    ) is False


# ---------------------------------------------------------------------------
# Empty / no-test run -> no finalize
# ---------------------------------------------------------------------------

def test_no_tests_ran_does_NOT_set_done_flag():
    """'no tests ran' output must not finalize."""
    assert _done(
        "pytest -q",
        rc=0,
        output="no tests ran",
    ) is False


def test_zero_passed_does_NOT_set_done_flag():
    """0 passed should not satisfy the >=1 pass requirement."""
    assert _done(
        "pytest -q",
        rc=0,
        output="0 passed in 0.1s",
    ) is False


# ---------------------------------------------------------------------------
# done_flag is sticky (existing True must remain True)
# ---------------------------------------------------------------------------

def test_done_flag_stays_true_once_set():
    """If done_flag is already True, the gate must keep it True even on a
    collect-only (or any other) report."""
    already_done = merge_map(_base_map(), done_flag=True)
    report = _report(("pytest --collect-only -q", 0, "collected 3 items"))
    out = parse_v1_maintainer_reply(_VALID_REPLY, already_done, report)
    assert out.done_flag is True


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def test_is_venv_wrapped_importable():
    """_is_venv_wrapped must be importable from maintainer."""
    from src.envstate.maintainer import _is_venv_wrapped  # noqa
    assert callable(_is_venv_wrapped)


def test_shows_execution_importable():
    """_shows_execution must be importable from maintainer."""
    from src.envstate.maintainer import _shows_execution  # noqa
    assert callable(_shows_execution)


def test_verified_test_run_passed_importable():
    """_verified_test_run_passed must be importable from maintainer."""
    from src.envstate.maintainer import _verified_test_run_passed  # noqa
    assert callable(_verified_test_run_passed)


# ---------------------------------------------------------------------------
# _is_venv_wrapped unit tests
# ---------------------------------------------------------------------------

def test_is_venv_wrapped_detects_poetry():
    from src.envstate.maintainer import _is_venv_wrapped
    assert _is_venv_wrapped("poetry run pytest -q") is True


def test_is_venv_wrapped_detects_pipenv():
    from src.envstate.maintainer import _is_venv_wrapped
    assert _is_venv_wrapped("pipenv run pytest") is True


def test_is_venv_wrapped_detects_hatch():
    from src.envstate.maintainer import _is_venv_wrapped
    assert _is_venv_wrapped("hatch run pytest") is True


def test_is_venv_wrapped_detects_conda():
    from src.envstate.maintainer import _is_venv_wrapped
    assert _is_venv_wrapped("conda run pytest") is True


def test_is_venv_wrapped_detects_pdm():
    from src.envstate.maintainer import _is_venv_wrapped
    assert _is_venv_wrapped("pdm run pytest") is True


def test_is_venv_wrapped_false_for_bare_pytest():
    from src.envstate.maintainer import _is_venv_wrapped
    assert _is_venv_wrapped("pytest -q") is False


def test_is_venv_wrapped_false_for_python_m_pytest():
    from src.envstate.maintainer import _is_venv_wrapped
    assert _is_venv_wrapped("python -m pytest -q") is False


# ---------------------------------------------------------------------------
# _shows_execution unit tests
# ---------------------------------------------------------------------------

def test_shows_execution_true_for_n_passed():
    from src.envstate.maintainer import _shows_execution
    assert _shows_execution("182 passed in 4.0s") is True


def test_shows_execution_true_for_one_passed():
    from src.envstate.maintainer import _shows_execution
    assert _shows_execution("1 passed in 0.1s") is True


def test_shows_execution_true_for_ran_n_tests():
    from src.envstate.maintainer import _shows_execution
    assert _shows_execution("Ran 12 tests in 0.345s\nOK") is True


def test_shows_execution_false_for_collected_only():
    from src.envstate.maintainer import _shows_execution
    assert _shows_execution("collected 182 items") is False


def test_shows_execution_false_for_empty():
    from src.envstate.maintainer import _shows_execution
    assert _shows_execution("") is False


def test_shows_execution_false_for_zero_passed():
    from src.envstate.maintainer import _shows_execution
    # Zero passed does not satisfy >=1 pass requirement
    assert _shows_execution("0 passed in 0.1s") is False
