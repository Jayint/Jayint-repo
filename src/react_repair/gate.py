"""Gate 2 (testability) verdict for the react arm (spec §5). Count-based, ≥80% of
executed tests pass. `executed >= 1` is the whole anti-hollow guard: zero-collected
and all-skipped runs return rc 0 but have no real passes."""
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
