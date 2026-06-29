"""Two-gate observability (Stage 1): derive the maturity gates as named results.

installability (= ebsr)  — provisional, derived from the graph's installable partition.
testability   (= pass_rate) — binding, wraps the existing host-verified test run.

Pure / read-only: nothing is written back to the graph. Gate state is DERIVED,
never persisted, so this module cannot perturb certification (anti-hollow holds
trivially — no SATISFIED is ever written here).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.envstate.constants import VERIFY_TEST_CMD

_EVIDENCE_CAP = 500


@dataclass(frozen=True)
class GateResult:
    name: str          # "installability" | "testability"
    passed: bool
    command: str
    provisional: bool
    evidence: str = ""


def evaluate_testability_gate(run_tests_verified: Callable[[], bool]) -> GateResult:
    """Wrap the host-verified test run (= pass_rate gate). Binding, not provisional.

    run_tests_verified is the orchestrator's `_run_tests_verified` closure: a real
    `python -m pytest -q` gated by the anti-hollow `_verified_test_run_passed`
    (rejects collect-only / zero-tests / all-skipped). Called exactly once.
    """
    passed = bool(run_tests_verified())
    return GateResult(
        name="testability",
        passed=passed,
        command=VERIFY_TEST_CMD,
        provisional=False,
        evidence=(
            "pass_rate>=0.8 (anti-hollow verified)"
            if passed
            else "verified test gate not passed"
        ),
    )
