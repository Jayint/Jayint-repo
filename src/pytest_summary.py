"""Parse a pytest / unittest text summary into the ``run_pytest_results.json`` schema.

The radical agent runs tests inside its *live sandbox* and observes the raw output,
but never turns that output into a numeric pass-rate. This module does exactly that,
emitting the SAME shape as the RATBench runner's ``run_pytest_results.json``::

    {"summary": {"total_tests": N, "passed": P, "skipped": S, ...},
     "error_breakdown": {"ModuleNotFoundError": n, ...}}

so the in-sandbox (live-container) result can be scored by the identical pass-rate
formula used for the cleanroom rebuild (``scripts/compute_essr.py::official_pass_rate``).
That makes the in-build (build-agent) number directly comparable to the cleanroom number;
the gap between them is synthesis loss.

Pure functions only — no I/O, no dependencies beyond the standard library — so the
parser is shared by both the agent (capture-at-observation) and the scorer.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# A pytest final-summary line ends with " in <float>s", e.g.
#   "===== 73 passed, 1 failed, 2 skipped in 4.48s ====="
#   "= 1 failed, 4 passed in 0.50s ="
#   "===== no tests ran in 0.01s ====="
# (For long runs pytest appends "(0:01:05)" after the seconds; the prefix still matches.)
_PYTEST_SUMMARY_LINE_RE = re.compile(r"\bin\s+[\d.]+\s*s\b", re.IGNORECASE)
_NO_TESTS_RAN_RE = re.compile(r"\bno tests ran\b", re.IGNORECASE)

# Per-outcome counts within a pytest summary line. ``deselected`` is intentionally
# NOT collected: deselected tests are not executed and do not belong in the total.
_PYTEST_OUTCOME_RE = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b",
    re.IGNORECASE,
)

# unittest:  "Ran 5 tests in 0.10s" then "OK" / "FAILED (failures=1, errors=2)" / "OK (skipped=2)"
_UNITTEST_RAN_RE = re.compile(r"^\s*Ran\s+(\d+)\s+tests?\s+in\s", re.IGNORECASE | re.MULTILINE)
_UNITTEST_FAILURES_RE = re.compile(r"failures=(\d+)")
_UNITTEST_ERRORS_RE = re.compile(r"\berrors=(\d+)")
_UNITTEST_SKIPPED_RE = re.compile(r"skipped=(\d+)")

# error_breakdown inputs — mirror compute_essr's pass_rate_exclude_code_issues counter.
_MODULENOTFOUND_RE = re.compile(r"\bModuleNotFoundError\b")
_IMPORTERROR_RE = re.compile(r"\bImportError\b")


def parse_pytest_summary(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse a test-run summary out of raw command output.

    Returns a dict in the ``run_pytest_results.json`` schema, or ``None`` when no
    pytest/unittest summary can be found. A pytest summary, when present, takes
    precedence over a unittest ``Ran N tests`` line.
    """
    if not text or not isinstance(text, str):
        return None

    pytest_line = _find_pytest_summary_line(text)
    if pytest_line is not None:
        return _build_from_pytest_line(pytest_line, text)

    return _parse_unittest(text)


def pass_rate_of(result: Optional[Dict[str, Any]]) -> float:
    """pass_rate identical to compute_essr.official_pass_rate: passed / (total - skipped).

    Returns 0.0 when there is no effective test (mirrors the honest scorer, which
    rejects the official scorer's phantom all-timeout 1.0).
    """
    if not result:
        return 0.0
    summary = result.get("summary", {}) or {}
    total = summary.get("total_tests", 0) or 0
    passed = summary.get("passed", 0) or 0
    skipped = summary.get("skipped", 0) or 0
    effective_total = total - skipped
    return passed / effective_total if effective_total > 0 else 0.0


_COLLECT_ONLY_RE = re.compile(r"\s*--collect-only\b")
_FORCED_FULL_SUITE_FALLBACK = "python -m pytest -q --disable-warnings"


def strip_collect_only(command: Optional[str]) -> str:
    """Remove the ``--collect-only`` flag so a verification command becomes an executing run."""
    if not command:
        return ""
    return _COLLECT_ONLY_RE.sub("", command).strip()


def derive_forced_test_command(
    candidates: Optional[list],
    fallback: str = _FORCED_FULL_SUITE_FALLBACK,
) -> str:
    """Choose a real (executing) test command for the RAT-style forced final run.

    Takes the agent's candidate test commands (verified first), strips ``--collect-only`` so a
    collect-only verification becomes an actual run, and returns the first usable one. Falls back
    to a full-suite ``python -m pytest`` when no candidate yields an executing command.
    """
    for c in candidates or []:
        if c and isinstance(c, str) and c.strip():
            stripped = strip_collect_only(c)
            if stripped:
                return stripped
    return fallback


def select_best_attempt(attempts: Optional[list]) -> Optional[Dict[str, Any]]:
    """Pick the RAT-comparable run among captured in-sandbox test attempts.

    Prefers the fullest suite (largest ``effective_total``), then higher ``pass_rate``,
    then the later ``step_index`` — i.e. the run RAT's end-of-loop full-suite pytest would
    have scored, rather than a narrow high-passing subset that would overstate success.
    """
    if not attempts:
        return None
    return max(
        attempts,
        key=lambda a: (
            a.get("effective_total", 0) or 0,
            a.get("pass_rate", 0.0) or 0.0,
            a.get("step_index", 0) or 0,
        ),
    )


def _find_pytest_summary_line(text: str) -> Optional[str]:
    """Return the LAST line that looks like a pytest final-summary line.

    Taking the last one means a later full run supersedes an earlier partial run.
    """
    found = None
    for line in text.splitlines():
        if not _PYTEST_SUMMARY_LINE_RE.search(line):
            continue
        if _PYTEST_OUTCOME_RE.search(line) or _NO_TESTS_RAN_RE.search(line):
            found = line
    return found


def _build_from_pytest_line(line: str, full_text: str) -> Dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0}
    for num, word in _PYTEST_OUTCOME_RE.findall(line):
        n = int(num)
        w = word.lower()
        if w.startswith("error"):
            counts["errors"] += n
        elif w in counts:
            counts[w] += n
    total = sum(counts.values())
    summary = {"total_tests": total, **counts}
    return {
        "summary": summary,
        "error_breakdown": _error_breakdown(full_text),
        "parse_method": "in_sandbox_pytest_text",
        "raw_summary_line": line.strip(),
    }


def _parse_unittest(text: str) -> Optional[Dict[str, Any]]:
    match = _UNITTEST_RAN_RE.search(text)
    if not match:
        return None
    total = int(match.group(1))
    failures = _first_int(_UNITTEST_FAILURES_RE, text)
    errors = _first_int(_UNITTEST_ERRORS_RE, text)
    skipped = _first_int(_UNITTEST_SKIPPED_RE, text)
    passed = max(total - failures - errors - skipped, 0)
    summary = {
        "total_tests": total,
        "passed": passed,
        "failed": failures,
        "errors": errors,
        "skipped": skipped,
        "xfailed": 0,
        "xpassed": 0,
    }
    return {
        "summary": summary,
        "error_breakdown": _error_breakdown(text),
        "parse_method": "in_sandbox_unittest_text",
        "raw_summary_line": match.group(0).strip(),
    }


def _first_int(pattern: re.Pattern, text: str) -> int:
    m = pattern.search(text)
    return int(m.group(1)) if m else 0


def _error_breakdown(text: str) -> Dict[str, int]:
    breakdown: Dict[str, int] = {}
    mnf = len(_MODULENOTFOUND_RE.findall(text))
    if mnf:
        breakdown["ModuleNotFoundError"] = mnf
    imp = len(_IMPORTERROR_RE.findall(text))
    if imp:
        breakdown["ImportError"] = imp
    return breakdown
