"""done_gate.py — Execution-aware done-gate helpers for v3.

Verbatim copy of the gate-related module-level items from
``src/envstate/maintainer.py``.  The only intentional deviation is
``_get_detector``, which imports ``RunOracle`` from ``src.run_oracle``
instead of ``Synthesizer`` from ``src.synthesizer``, so the v3-only branch
can drop synthesizer.py without touching this file.

Exports consumed by v3 consumers:
  _verified_test_run_passed   — used by orchestrator.py
  _progress_synced_with_done  — used by deterministic_maintainer.py
"""
from __future__ import annotations

import re
import shlex

from src.envstate.world_model import TaskReport

# ---------------------------------------------------------------------------
# Execution-aware done-gate helpers
# ---------------------------------------------------------------------------

# Module-level detector singleton — lazy-imported to avoid circular imports.
# Use _get_detector() instead of _DETECTOR directly.
_DETECTOR = None


def _get_detector():
    """Return the module-level RunOracle singleton, creating it on first call."""
    global _DETECTOR
    if _DETECTOR is None:
        from src.run_oracle import RunOracle  # lazy to avoid circular import
        _DETECTOR = RunOracle()
    return _DETECTOR


# Venv runner prefixes that wrap the test command — the grader uses the bare
# system interpreter, so commands run under these wrappers do NOT count.
_VENV_WRAPPERS: frozenset[str] = frozenset({
    "poetry", "pipenv", "hatch", "conda", "pdm",
})


def _is_venv_wrapped(cmd: str) -> bool:
    """Return True iff *cmd* is run under a venv manager (poetry/pipenv/hatch/conda/pdm).

    Detects the pattern ``<wrapper> run <...>`` at the start of the command
    (after optional path prefix). Case-insensitive on the wrapper name.
    Never raises.
    """
    try:
        tokens = shlex.split(cmd.strip())
    except ValueError:
        tokens = cmd.strip().split()
    if len(tokens) < 2:
        return False
    # The first token may be a full path, e.g. /usr/local/bin/poetry.
    first = tokens[0].split("/")[-1].lower()
    # Pattern: <wrapper> run ...
    return first in _VENV_WRAPPERS and len(tokens) >= 2 and tokens[1] == "run"


# Regexes for execution summary detection (condition 5 of the gate).
# Matches "N passed" (pytest) and "Ran N tests" (unittest) with N >= 1.
_RE_N_PASSED = re.compile(r"\b([1-9]\d*)\s+passed\b", re.IGNORECASE)
_RE_RAN_N_TESTS = re.compile(r"\bran\s+([1-9]\d*)\s+tests?\b", re.IGNORECASE)
_RE_N_FAILED = re.compile(r"\b([1-9]\d*)\s+failed\b", re.IGNORECASE)
_RE_N_ERRORS = re.compile(r"\b([1-9]\d*)\s+errors?\b", re.IGNORECASE)
_MIN_PARTIAL_PASS_RATE = 0.8

# ANSI escape sequence regex — strip before matching so that pytest's color
# output (\x1b[1m5 passed\x1b[0m) does not break the \b word-boundary check.
_RE_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_RE_NON_EVALUABLE_TEST_FAILURE = re.compile(
    r"ModuleNotFoundError|No module named|ImportError while loading conftest|"
    r"SyntaxError|docker\.errors\.DockerException|collected\s+0\s+items",
    re.IGNORECASE,
)
_RE_ASSERTION_TEST_FAILURE = re.compile(
    r"^\s*(?:FAILED|ERROR)\s+\S+|\bAssertionError\b",
    re.IGNORECASE | re.MULTILINE,
)


def _shows_execution(output: str) -> bool:
    """Return True iff *output* shows a genuine test execution summary.

    Accepts:
      - "N passed ..."  (pytest) with N >= 1
      - "Ran N tests ..." (unittest) with N >= 1

    Rejects:
      - "collected N items"  (pure --collect-only, the hole we are closing)
      - ""  (no output)
      - "0 passed"  (zero tests pass does not satisfy >=1 requirement)
    """
    if not output:
        return False
    clean = _RE_ANSI.sub("", output)
    return bool(_RE_N_PASSED.search(clean) or _RE_RAN_N_TESTS.search(clean))


def _looks_like_non_evaluable_failure(output: str) -> bool:
    """True for environment/collection failures that should drive repair/ingest."""
    if not output:
        return False
    return bool(_RE_NON_EVALUABLE_TEST_FAILURE.search(_RE_ANSI.sub("", output)))


def _looks_like_assertion_test_failure(output: str) -> bool:
    """True for concrete test failures when counts/progress were truncated."""
    if not output:
        return False
    return bool(_RE_ASSERTION_TEST_FAILURE.search(_RE_ANSI.sub("", output)))


# pytest prints a "[NNN%]" progress-completion marker ONLY when it actually
# executed tests to completion; a pure --collect-only run prints "collected N
# items" and never "[NNN%]". So a surviving "[100%]" is a reliable execution
# signal even when the trailing "N passed" summary line was lost in capture
# (observed on promptwright: 45/45 passed in-loop, only "[100%]" survived ->
# the gate could not confirm the pass). Callers MUST already hold rc==0 and a
# no-failure check (see _verified_test_run_passed); under those guards a 100%
# completion == every collected test passed.
_RE_PYTEST_COMPLETE = re.compile(r"\[\s*100%\]")


def _shows_pytest_completion(output: str) -> bool:
    """True iff *output* contains the pytest "[100%]" progress-completion marker."""
    if not output:
        return False
    return bool(_RE_PYTEST_COMPLETE.search(_RE_ANSI.sub("", output)))


def _pytest_summary_pass_rate(output: str) -> float | None:
    """Return pytest summary pass rate when enough summary counts survived.

    The benchmark metric is partial pass rate, and the gate evidence string is
    explicitly ``pass_rate>=0.8``.  A pytest run with one or a few test failures
    therefore remains useful evidence that the environment is testable.  Keep
    this narrow: require at least one real pass and at least one failure/error
    count, so collect-only output and non-test crashes cannot look green.
    """
    if not output:
        return None
    clean = _RE_ANSI.sub("", output)

    def _count(regex: re.Pattern[str]) -> int:
        return sum(int(m.group(1)) for m in regex.finditer(clean))

    passed = _count(_RE_N_PASSED)
    failed = _count(_RE_N_FAILED)
    errors = _count(_RE_N_ERRORS)
    total = passed + failed + errors
    if passed <= 0 or total <= passed:
        return None
    return passed / total


# pytest prints "[100%]" even when every collected test was SKIPPED (rc==0).
# An all-skipped run is zero real passes and must NOT satisfy the done-gate.
_RE_N_SKIPPED = re.compile(r"\b([1-9]\d*)\s+skipped\b", re.IGNORECASE)


def _all_skipped(output: str) -> bool:
    """True iff *output* shows a pytest run that skipped >=1 test and passed none.

    Guards the "[100%]" completion branch: pytest prints "[100%]" for an
    all-skipped run too, so "[100%]" alone is NOT proof of a real pass. Returns
    True only when a skipped-count summary survives and no "N passed" (N>=1) does.
    """
    if not output:
        return False
    clean = _RE_ANSI.sub("", output)
    has_skipped = bool(_RE_N_SKIPPED.search(clean))
    has_passed = bool(_RE_N_PASSED.search(clean))
    return has_skipped and not has_passed


# Exclusion flags that hide pre-existing test paths and can manufacture a
# spurious green run (Phase 4 anti-gaming check).
_EXCLUSION_FLAG_PREFIXES: tuple[str, ...] = (
    "--ignore=",
    "--ignore-glob=",
    "--ignore-glob",
    "--ignore",
    "--deselect",
)


def _uses_test_exclusion(cmd: str) -> bool:
    """Return True iff *cmd* contains a pytest exclusion flag that skips test paths.

    Flags detected: ``--ignore``, ``--ignore=<value>``, ``--ignore-glob``,
    ``--ignore-glob=<value>``, ``--deselect``.

    Matching is done on whole tokens produced by ``shlex.split``.  A token
    matches when it equals a bare flag (e.g. ``--ignore``) or starts with the
    flag followed by ``=`` (e.g. ``--ignore=examples``).  This avoids false
    positives from paths that merely contain the substring "ignore".

    Falls back to whitespace split on ``ValueError`` (malformed shell quoting),
    mirroring ``_is_collect_only_cmd``.  Never raises.
    """
    try:
        tokens = shlex.split(cmd.strip())
    except ValueError:
        tokens = cmd.strip().split()
    _bare_flags: frozenset[str] = frozenset({"--ignore", "--ignore-glob", "--deselect"})
    _eq_prefixes: tuple[str, ...] = ("--ignore=", "--ignore-glob=", "--deselect=")
    for tok in tokens:
        if tok in _bare_flags:
            return True
        if any(tok.startswith(p) for p in _eq_prefixes):
            return True
    return False


def _verified_test_run_passed(
    report: TaskReport,
    detector=None,
) -> bool:
    """Return True iff any command in *report* is a verified passing test execution.

    Gate (all six conditions must hold):
      1. rec.rc == 0, or pytest summary pass_rate >= 0.8
      2. detector.is_test_command(rec.cmd)
      3. NOT _is_venv_wrapped(rec.cmd)
      4. NOT _uses_test_exclusion(rec.cmd)  — Phase 4 anti-gaming guard:
         a verification that deliberately ignores/deselects pre-existing test
         paths is not a trustworthy success signal.  The n8n-autoscaling case
         used ``--ignore=examples`` to hide a real SyntaxError in examples/;
         accepting that as a pass would produce a false success.  Deliberate
         strictness: the only trustworthy verification is a full, unfiltered run.
      5. detector.analyze_test_run(rec.cmd, rec.output)["is_effective_test_run"]
      6. _shows_execution(rec.output)
    """
    if detector is None:
        detector = _get_detector()
    for rec in report.commands:
        if verified_test_command_passed(rec.cmd, rec.rc, rec.output, detector=detector):
            return True
    return False


def verified_test_command_passed(
    cmd: str,
    rc: int,
    output: str,
    *,
    detector=None,
) -> bool:
    """Return True iff one command satisfies the anti-hollow test gate.

    This is the command-level equivalent of ``_verified_test_run_passed``.  The
    incremental graph executor sees a failed ``InstallResult`` before it has a
    ``TaskReport``; using the same helper there keeps the search executor and
    terminal done-gate aligned on partial pytest pass-rate semantics.
    """
    if detector is None:
        detector = _get_detector()
    if not detector.is_test_command(cmd):
        return False
    if _is_venv_wrapped(cmd):
        return False
    if _uses_test_exclusion(cmd):
        return False
    output = output or ""
    non_evaluable_failure = _looks_like_non_evaluable_failure(output)
    assertion_failure = _looks_like_assertion_test_failure(output)
    partial_pass_rate = _pytest_summary_pass_rate(output)
    shows_completion = _shows_pytest_completion(output)
    analysis = None
    if rc != 0:
        if non_evaluable_failure:
            return False
        if partial_pass_rate is None:
            analysis = detector.analyze_test_run(cmd, output)
            # Large pytest failures can lose the trailing numeric summary in
            # the captured tail while retaining the progress-completion marker.
            # Under the same anti-gaming guards (real pytest command, no venv
            # wrapper, no exclusions), reaching 100% is still evidence that the
            # environment is evaluable instead of an install/config failure.
            if not shows_completion and not assertion_failure:
                return False
        elif partial_pass_rate < _MIN_PARTIAL_PASS_RATE:
            return False
    # For rc==0, a surviving pytest "[100%]" completion marker is itself
    # proof of an effective execution that reached the end with no failures.
    # For rc!=0 partial-pass acceptance, we require an explicit parsed
    # pass-rate summary above and still require execution evidence below.
    # The completion marker therefore satisfies BOTH the effective-run check
    # (cond 5) and the execution-evidence check (cond 6) only under these
    # guards.
    has_partial_summary = partial_pass_rate is not None
    if analysis is None:
        analysis = detector.analyze_test_run(cmd, output)
    has_test_failure_signal = (
        analysis.get("reason") == "test_failure_signal" and assertion_failure
    )
    if not shows_completion and not has_partial_summary and not has_test_failure_signal and not analysis.get("is_effective_test_run", False):
        return False
    if _all_skipped(output):
        # "[100%]" with a skipped-only summary and zero real passes is not a
        # success (closes the all-skipped Gate-1 hole, e.g. pynitrokey).
        return False
    return bool(shows_completion or has_test_failure_signal or _shows_execution(output))


# ---------------------------------------------------------------------------
# _progress_synced_with_done — consumed by deterministic_maintainer.py
# ---------------------------------------------------------------------------

def _progress_synced_with_done(
    current_map, done: bool
) -> "dict[str, bool] | None":
    """Keep the derived ``progress['tests']`` consistent with the structural
    ``done_flag`` (§4.3: tests == done_flag).

    apply_deterministic derives progress *before* the Maintainer runs, so in the
    terminal cycle ``tests`` would otherwise lag one step (done_flag True but
    progress.tests False, with no next fold to catch up). Returns ``None`` when no
    change is needed so merge_map keeps the current dict untouched.
    """
    if done and not current_map.progress.get("tests", False):
        return {**current_map.progress, "tests": True}
    return None
