"""Host-side anti-gaming gate (Repo2Run lever).

The repair agent is scored on pytest pass-rate, and the ratbench scorer counts uncollectable
modules as ERRORS in the denominator. So the cheapest way to raise the score WITHOUT fixing the
environment is to make pytest stop collecting the tests that error — write a `pytest.ini` /
`conftest.py` that `--ignore`s / narrows `testpaths`, or delete the offending test files. That drops
the denominator (podman-compose: 440/512=0.86 → 440/440=1.0) with zero real capability change.

This module detects that class of edit STATICALLY from the shell text an edit introduces into
setup.sh, so the loop can refuse it before it ever builds — deterministic, no extra container round
-trip. We only ever scan setup.sh (never the repo's own committed config), and only the lines an edit
ADDS, so a repo-native pytest.ini can't trip it — only an agent-authored narrowing does.

The prompt's INTEGRITY clause asks the agent not to do this; the podman trace proved the ask is not
enough (the agent's own reasoning flagged "that's faking a pass," then relabeled the same edit
"environment configuration" and did it anyway). This gate is the enforcement the prompt can't be.
"""
from __future__ import annotations

import re

# A path token that names a test target (a test file or a tests/ dir). Shared by the destruction
# patterns so "testdata/", "pytest-mock", "latest/" etc. (no real test path) don't false-positive.
_TEST_PATH = r"(?:test_\w+\.py|\w+_test\.py|/tests?/|(?<![\w-])tests?/)"

# Each entry: (regex, human reason). Matched line-agnostically against the ADDED shell text. A hit
# means the edit shrinks what pytest collects — gaming, not repair. Tuned to fire on unambiguous
# exclusion directives / test destruction and NOT on legitimate setup (pip --ignore-installed, a
# pythonpath/markers-only pytest.ini, rm of build artifacts).
_NARROW_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # pytest CLI exclusion flags. `--ignore`/`--ignore-glob` require a `=`/space right after (so
    # pip's `--ignore-installed`/`--ignore-requires-python`, which have `-` next, are excluded) AND a
    # value that names a test path — the only reason to ignore a test path is to drop tests.
    (re.compile(r"--ignore(?:-glob)?[=\s]+\S*test", re.I),
     "pytest --ignore excludes test paths from collection"),
    (re.compile(r"--deselect[=\s]", re.I),
     "pytest --deselect drops specific tests from the run"),
    (re.compile(r"-k\s+['\"]?\s*not\b", re.I),
     "pytest -k 'not ...' filters tests out of the run"),
    # ini / setup.cfg / pyproject / conftest collection-narrowing keys
    (re.compile(r"\btestpaths\b\s*=", re.I),
     "testpaths restricts pytest to a subset of the suite"),
    (re.compile(r"collect_ignore(?:_glob)?\s*[:=]", re.I),
     "conftest collect_ignore skips test modules"),
    (re.compile(r"\bnorecursedirs\b\s*=[^\n]*test", re.I),
     "norecursedirs excludes test directories"),
    # conftest programmatic deselection
    (re.compile(r"pytest_collection_modifyitems", re.I),
     "conftest hook mutates the collected item set"),
    (re.compile(r"items\s*(?:\[\s*:\s*\]\s*=|\.clear\s*\()", re.I),
     "conftest clears/replaces the collected items"),
    # destroying, moving, or blanking test files
    (re.compile(r"\b(?:rm|unlink)\b[^\n]*" + _TEST_PATH, re.I),
     "removes test files"),
    (re.compile(r"\bfind\b[^\n]*-name[^\n]*test[^\n]*-delete", re.I),
     "find-deletes test files"),
    (re.compile(r"\bmv\b[^\n]*(?<![\w-])tests?\b", re.I),
     "moves the tests directory out of collection"),
    (re.compile(r"(?:>>?|\btee\b|\btruncate\b|sed\s+-i)[^\n]*(?:test_\w+|\w+_test)\.py", re.I),
     "overwrites or edits a test file"),
]


def detect_test_narrowing(text: str) -> str | None:
    """Return a human-readable reason if `text` (a blob of shell setup.sh lines) narrows what the
    test suite collects — an --ignore/testpaths/deselect config write, a conftest collection hook,
    or test-file destruction — else None. Pure and side-effect free."""
    if not text:
        return None
    for pat, reason in _NARROW_PATTERNS:
        if pat.search(text):
            return reason
    return None


def narrowing_reason(old_script: str, new_script: str) -> str | None:
    """Gate a setup.sh edit: return why it narrows the collected test set, scanning ONLY the lines
    this edit ADDS (present in new, absent from old). A pre-existing narrowing line — or no change —
    returns None, so the gate punishes the edit that introduces gaming, not one that merely coexists
    with it."""
    old_lines = set((old_script or "").splitlines())
    added = [ln for ln in (new_script or "").splitlines() if ln not in old_lines]
    return detect_test_narrowing("\n".join(added))
