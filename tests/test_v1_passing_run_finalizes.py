"""Regression tests for the v1 success-capture gap (2026-06-12).

A genuine in-sandbox pytest pass must register as a verified success even when
the trailing 'N passed' summary line is lost in output capture, leaving only the
pytest '[100%]' progress-completion marker. Observed on StacklokLabs/promptwright
(45/45 tests passed in-loop, yet configuration_success=False, no Dockerfile).
Root cause + evidence: outputs/failed53_v1_rootcause.md.

pytest prints '[NNN%]' ONLY when it actually executes tests to completion; a
pure --collect-only run prints 'collected N items' with no '[NNN%]'. Combined
with the rc==0 + no-failure guards already enforced by _verified_test_run_passed,
a surviving '[100%]' is a reliable pass signal.
"""

from src.envstate.maintainer import _verified_test_run_passed, _shows_pytest_completion
from src.envstate.world_model import TaskReport, CommandRecord


def _report(out, cmd="python -m pytest -q", rc=0):
    return TaskReport(
        task_goal="",
        status="done",
        commands=(CommandRecord(cmd=cmd, rc=rc, output=out),),
        learning="",
    )


# pytest -q where the trailing 'N passed' summary was lost in capture.
PYTEST_COMPLETION_NO_SUMMARY = "." * 45 + " " * 20 + "[100%]\n"
PYTEST_FULL = "." * 45 + " [100%]\n\n45 passed in 0.21s\n"
PYTEST_ANSI_COMPLETION = "\x1b[32m" + "." * 45 + "\x1b[0m" + " " * 20 + "[100%]\n"
COLLECT_ONLY = "collected 45 items\n"


def test_completion_marker_counts_as_pass_when_rc0():
    # The fix: rc==0 + reached [100%] is a real pass even without the summary line.
    assert _verified_test_run_passed(_report(PYTEST_COMPLETION_NO_SUMMARY, rc=0)) is True


def test_ansi_wrapped_completion_marker_counts_as_pass():
    assert _verified_test_run_passed(_report(PYTEST_ANSI_COMPLETION, rc=0)) is True


def test_explicit_summary_still_passes():
    assert _verified_test_run_passed(_report(PYTEST_FULL, rc=0)) is True


def test_completion_marker_rejected_when_rc_nonzero():
    # rc!=0 means failures/interruption — never a pass regardless of [100%].
    assert _verified_test_run_passed(_report(PYTEST_COMPLETION_NO_SUMMARY, rc=1)) is False


def test_collect_only_still_rejected():
    # collect-only prints 'collected N items' but never '[NNN%]' -> not an execution.
    assert _verified_test_run_passed(_report(COLLECT_ONLY, rc=0)) is False


def test_shows_pytest_completion_helper():
    assert _shows_pytest_completion("foo [100%]\n") is True
    assert _shows_pytest_completion("\x1b[32m. \x1b[0m[100%]") is True
    assert _shows_pytest_completion("collected 5 items") is False
    assert _shows_pytest_completion("") is False
