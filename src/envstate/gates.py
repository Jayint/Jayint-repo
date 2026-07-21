"""Two-gate observability (Stage 1+7): derive the maturity gates as named results.

installability (= ebsr) — BINDING when a raw-base full-script replay result is
    available. Incremental search supplies it only at terminal certification;
    the fresh ablation supplies one every cycle. Falls back to the provisional
    graph-frontier heuristic when no replay is available yet.
testability   (= tests executed) — binding, wraps the existing host-verified
    test run.  The outer evaluator, not this gate, owns the pass-rate score.

Pure / read-only: nothing is written back to the graph. Gate state is DERIVED,
never persisted, so this module cannot perturb certification (anti-hollow holds
trivially — no SATISFIED is ever written here).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from python_deps.depgraph.emit import partition
from python_deps.depgraph.schema import DepGraph
from src.envstate.constants import VERIFY_TEST_CMD

if TYPE_CHECKING:
    from src.sandbox import InstallResult

_EVIDENCE_CAP = 500


@dataclass(frozen=True)
class GateResult:
    name: str          # "installability" | "testability"
    passed: bool
    command: str
    provisional: bool
    evidence: str = ""


def evaluate_testability_gate(
    run_tests_verified: Callable[[], bool],
    *,
    command: str = VERIFY_TEST_CMD,
) -> GateResult:
    """Wrap the host-verified test execution gate. Binding, not provisional.

    run_tests_verified is the orchestrator's `_run_tests_verified` closure: a real
    `python -m pytest -q` gated by the anti-hollow `_verified_test_run_passed`
    (rejects collect-only / zero-tests / all-skipped). Called exactly once.
    """
    passed = bool(run_tests_verified())
    return GateResult(
        name="testability",
        passed=passed,
        command=command,
        provisional=False,
        evidence=(
            "genuine tests executed (anti-hollow verified)"
            if passed
            else "genuine test execution not verified"
        ),
    )


def evaluate_installability_gate(
    graph: "DepGraph | None",
    replay: "InstallResult | None" = None,
) -> GateResult:
    """Installability (= ebsr) gate.

    BINDING path (``replay is not None``): the latest raw-base full-script
    replay is the installability proof. ``passed`` is exactly ``replay.rc == 0``.

    PROVISIONAL fallback (``replay is None``): derived from the graph's
    installable partition — passed ⇔ nothing emittable AND nothing stuck in
    the installable frontier (every installable node is SATISFIED). Used only
    by the ``block_emit`` ablation, which has no per-cycle replay to bind to.
    Read-only in both paths: never written back to the graph.
    """
    if replay is not None:
        if replay.rc == 0:
            evidence = "fresh replay rc=0"
        else:
            evidence = f"fresh replay failed: {replay.failing_command}"
        return GateResult(
            name="installability",
            passed=(replay.rc == 0),
            command="fresh-from-base setup.sh replay",
            provisional=False,
            evidence=evidence[:_EVIDENCE_CAP],
        )
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
    replay: "InstallResult | None" = None,
    *,
    test_command: str = VERIFY_TEST_CMD,
) -> tuple[GateResult, GateResult]:
    """The two gates in ladder order: (installability, testability).

    ``replay`` threads the orchestrator's latest full-replay result into the
    installability gate. ``None`` during incremental search falls back to the
    provisional graph-frontier heuristic; a successful exit always has a
    binding terminal replay.
    """
    return (
        evaluate_installability_gate(graph, replay=replay),
        evaluate_testability_gate(run_tests_verified, command=test_command),
    )
