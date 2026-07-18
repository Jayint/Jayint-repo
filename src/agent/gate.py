"""Gate 2 (testability) verdict + the host-side anti-gaming gate for the react arm (spec §5).

`test_verdict` is the count-based ≥80%-of-executed-tests-pass verdict; `executed >= 1` is the
anti-hollow guard (zero-collected and all-skipped runs return rc 0 but have no real passes).

Folded in from the former anti_cheat.py (§4A consolidation): the STATIC anti-gaming detectors that
refuse a setup.sh edit which fakes a pass without fixing the environment — collection-narrowing
(--ignore / testpaths / --deselect / conftest hooks / test-file destruction) and self-install of the
repo's own package from a package index instead of the local checkout. Both scan ONLY the lines an
edit ADDS, so a repo-native pytest.ini or a seed self-install can't trip them — only an agent-authored
edit does. The section banner below keeps the full rationale (both vectors were found in LIVE runs)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class TestOutcome:
    ok: bool
    passed: int
    executed: int
    output: str = ""
    collected: int = 0     # the full test population — see `_collected` for why this is DERIVED, not
                           # read off a "collected N items" line (VERIFY_TEST_CMD is `pytest -q`, which
                           # never prints one). > passed+failed when tests are skipped/deselected.
    # The raw component counts. `executed` fuses them into the gate denominator, which is right for
    # SCORING but wrong for DISPLAY: "TESTS 0/5 passed" on a repo with 297 tests and 5 unimportable
    # modules reads as "5 tests exist, none passed", which is false. Keep the parts so an observation
    # can report what pytest actually said.
    failed: int = 0
    errors: int = 0        # collection errors — modules that failed to IMPORT, so never collected
    skipped: int = 0


# Not a pytest test despite the "Test" prefix: this is a plain dataclass. Without
# this, any test module doing `from ...gate import TestOutcome` makes pytest try to
# collect it as a test class (PytestCollectionWarning: cannot collect test class).
TestOutcome.__test__ = False


def _count(text: str, word: str) -> int:
    m = re.search(rf"(\d+)\s+{word}\b", text)
    return int(m.group(1)) if m else 0


_COLLECTED = re.compile(r"collected (\d+) item")     # pytest: "collected 200 items [/ N errors]"


def _collected(text: str, passed: int, failed: int, skipped: int) -> int:
    """The size of the collected test population.

    `collected N items` is only printed on pytest's DEFAULT reporter — and VERIFY_TEST_CMD is
    `python -m pytest -q`, which never prints it. Reading only that line therefore made `collected`
    permanently 0 for every react run (dead field, dead header suffix). So: honor an explicit line
    when one exists (non-`-q` callers), else derive from the summary counts.

    Collection ERRORS are excluded on purpose — a module that failed to import was never collected."""
    m = _COLLECTED.search(text)
    if m:
        return int(m.group(1))
    return passed + failed + skipped + _count(text, "xfailed") + _count(text, "xpassed")


def test_verdict(output: str, *, threshold: float = 0.8) -> TestOutcome:
    text = _ANSI.sub("", output or "")
    passed = _count(text, "passed")
    failed = _count(text, "failed")
    errors = _count(text, "errors?")            # "1 error" / "2 errors"
    skipped = _count(text, "skipped")
    executed = passed + failed + errors          # skipped excluded from the denominator
    ok = executed >= 1 and passed / executed >= threshold
    return TestOutcome(ok=ok, passed=passed, executed=executed, output=output or "",
                       collected=_collected(text, passed, failed, skipped),
                       failed=failed, errors=errors, skipped=skipped)


# Not a pytest test despite the name: this is the production verdict fn. Without
# this, any test module doing `from ...gate import test_verdict` mis-collects it
# as a test (its first param `output` becomes an unknown fixture -> collection error).
test_verdict.__test__ = False


# ======================================================================================
# Anti-gaming gate (host-side; Repo2Run lever) — folded from anti_cheat.py (§4A consolidation).
#
# Host-side anti-gaming gate (Repo2Run lever).
#
# The repair agent is scored on pytest pass-rate, and the ratbench scorer counts uncollectable
# modules as ERRORS in the denominator. So the cheapest way to raise the score WITHOUT fixing the
# environment is to make pytest stop collecting the tests that error — write a `pytest.ini` /
# `conftest.py` that `--ignore`s / narrows `testpaths`, or delete the offending test files. That drops
# the denominator (podman-compose: 440/512=0.86 → 440/440=1.0) with zero real capability change.
#
# This module detects that class of edit STATICALLY from the shell text an edit introduces into
# setup.sh, so the loop can refuse it before it ever builds — deterministic, no extra container round
# -trip. We only ever scan setup.sh (never the repo's own committed config), and only the lines an edit
# ADDS, so a repo-native pytest.ini can't trip it — only an agent-authored narrowing does.
#
# The prompt's INTEGRITY clause asks the agent not to do this; the podman trace proved the ask is not
# enough (the agent's own reasoning flagged "that's faking a pass," then relabeled the same edit
# "environment configuration" and did it anyway). This gate is the enforcement the prompt can't be.
#
# ======================================================================================

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


# ── Self-install gate: the repo's OWN package pulled from an INDEX instead of the checkout ──────
#
# The second false-green vector, found in a LIVE run: instead of `pip install -e .`, the agent shipped
# `pip install pytest itsdangerous freezegun` — installing the PUBLISHED itsdangerous from PyPI. The
# suite then went 297/297 green and the host certified DONE... against code that is NOT in the repo.
# The checkout at /app/src was never installed; `import itsdangerous` resolved to site-packages.
#
# The collection gate above cannot see this (collection GREW, 68 -> 297), and the pass-rate gate is
# perfectly satisfied. But the environment was never reproduced — any repo whose package is on PyPI
# can be "solved" this way. So the host, which KNOWS the repo's own distribution name, rejects it.
_PIP_INSTALL = re.compile(
    r"(?:\buv\s+pip|\bpython3?\s+-m\s+pip|\bpip3?)\s+install\b(.*)", re.IGNORECASE)
# flags that consume the NEXT token as their value (so it is not a requirement name)
_VALUE_FLAGS = frozenset({
    "-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url",
    "-f", "--find-links", "-t", "--target", "--prefix", "--root", "--python",
})


def _normalize_dist(name: str) -> str:
    """PEP 503 normalization — `Its_Dangerous` and `its.dangerous` are the same distribution."""
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower())


def _requirement_names(args: str) -> list[str]:
    """The bare requirement NAMES in a `pip install` argument string. Flags (and their values), local
    paths (`.`, `.[test]`, `/app`, `./pkg`), archives and VCS/URL installs are all skipped — those are
    legitimate ways to install the checkout; only an INDEX requirement yields a name here."""
    head = re.split(r"&&|\|\||;|\||>", args or "")[0]        # stop at a shell operator
    names: list[str] = []
    skip_next = False
    for tok in head.split():
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            if tok in _VALUE_FLAGS:
                skip_next = True
            continue                                          # flags carry no requirement name (incl. -e)
        if tok.startswith((".", "/", "~", "$", "git+", "http://", "https://", "file:")) or "/" in tok:
            continue                                          # a local path / archive / VCS install
        if tok.endswith((".whl", ".tar.gz", ".zip")):
            continue
        names.append(re.split(r"[\[<>=!~;]", tok, maxsplit=1)[0])   # strip extras + version specifier
    return names


def self_install_reason(script: str, project_name: str | None) -> str | None:
    """Return why *script* installs the project under test from a package index instead of the local
    checkout, else None. `project_name` is the repo's own distribution name (pyproject/setup.cfg),
    which the HOST knows and the agent cannot argue with. Installing the checkout — `pip install -e .`,
    `pip install .`, `pip install -e .[test]`, a path, or a VCS/URL — never trips this."""
    target = _normalize_dist(project_name or "")
    if not target:
        return None
    for line in (script or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _PIP_INSTALL.search(stripped)
        if not m:
            continue
        for name in _requirement_names(m.group(1)):
            if _normalize_dist(name) == target:
                return (f"installs the project under test (`{project_name}`) from a package index "
                        f"instead of the repo's own checkout")
    return None


def added_self_install_reason(old_script: str, new_script: str,
                              project_name: str | None) -> str | None:
    """Gate an EDIT: scan only the lines it ADDS — the same scoping rule as `narrowing_reason`, and
    for the same reason. Whole-script scanning would mean that once a self-install exists anywhere in
    setup.sh (e.g. it came in with the seed), EVERY later edit is rejected and the agent can never dig
    out. Scoped to the added lines: the edit that INTRODUCES the shortcut is rejected, an unrelated
    edit that merely coexists with a pre-existing one is not, and a delete that REMOVES it always
    passes (a removal adds nothing)."""
    old_lines = set((old_script or "").splitlines())
    added = [ln for ln in (new_script or "").splitlines() if ln not in old_lines]
    return self_install_reason("\n".join(added), project_name)
