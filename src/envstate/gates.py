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

from python_deps.depgraph.emit import partition
from python_deps.depgraph.schema import DepGraph
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


def evaluate_installability_gate(graph: "DepGraph | None") -> GateResult:
    """Provisional installability (= ebsr) derived from the graph's installable partition.

    Passed ⇔ nothing emittable AND nothing stuck in the installable frontier
    (every installable node is SATISFIED). PROVISIONAL: the binding ebsr is a
    fresh-from-base replay of the rendered setup.sh (later stage); a live, already-
    populated container makes a full-script run hollow (installs are no-ops, so
    ordering bugs hide). Read-only: never written back to the graph.
    """
    if graph is None:
        return GateResult(
            name="installability",
            passed=False,
            command="(provisional: graph installable frontier)",
            provisional=True,
            evidence="no dep graph",
        )
    part = partition(graph)
    remaining = part.emittable + part.frontier
    passed = not remaining
    if passed:
        evidence = "all installable nodes SATISFIED"
    else:
        evidence = ("unsatisfied: " + ", ".join(n.id for n in remaining))[:_EVIDENCE_CAP]
    return GateResult(
        name="installability",
        passed=passed,
        command="(provisional: graph installable frontier; binding ebsr = fresh setup.sh replay, later stage)",
        provisional=True,
        evidence=evidence,
    )


def evaluate_gates(
    graph: "DepGraph | None",
    run_tests_verified: Callable[[], bool],
) -> tuple[GateResult, GateResult]:
    """The two gates in ladder order: (installability, testability)."""
    return (
        evaluate_installability_gate(graph),
        evaluate_testability_gate(run_tests_verified),
    )
