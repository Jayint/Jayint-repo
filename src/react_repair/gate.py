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


def _count(text: str, word: str) -> int:
    m = re.search(rf"(\d+)\s+{word}\b", text)
    return int(m.group(1)) if m else 0


def test_verdict(output: str, *, threshold: float = 0.8) -> TestOutcome:
    text = _ANSI.sub("", output or "")
    passed = _count(text, "passed")
    failed = _count(text, "failed")
    errors = _count(text, "errors?")            # "1 error" / "2 errors"
    executed = passed + failed + errors          # skipped excluded from the denominator
    ok = executed >= 1 and passed / executed >= threshold
    return TestOutcome(ok=ok, passed=passed, executed=executed, output=output or "")


# Not a pytest test despite the name: this is the production verdict fn. Without
# this, any test module doing `from ...gate import test_verdict` mis-collects it
# as a test (its first param `output` becomes an unknown fixture -> collection error).
test_verdict.__test__ = False
