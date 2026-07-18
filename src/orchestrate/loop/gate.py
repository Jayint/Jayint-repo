"""The loop's verify-gate concerns (spec §4C loop/gate.py): the two maturity gates,
the pytest-outcome fingerprint, and the per-generation verify memo. Folded (3b-3)
from gates + gate_signature + verify_cache — three read-only helpers the run_v3
driver leans on around the VERIFY_TEST_CMD run; each stays independent (no cross-refs).

Two-gate observability (Stage 1+7): derive the maturity gates as named results.

installability (= ebsr) — BINDING when a real per-cycle replay result is
    available (Model B: run_v3's sole executor is fresh full-script replay
    every cycle, so the canonical path always has one — see orchestrator.py's
    ``_last_replay_result``). Falls back to the provisional graph-frontier
    heuristic only when no replay is supplied (the ``block_emit`` ablation).
testability   (= pass_rate) — binding, wraps the existing host-verified test run.

Pure / read-only: nothing is written back to the graph. Gate state is DERIVED,
never persisted, so this module cannot perturb certification (anti-hollow holds
trivially — no SATISFIED is ever written here).
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from graph.compile.emit import partition
from graph.model import DepGraph
from src.orchestrate.loop.constants import VERIFY_TEST_CMD

if TYPE_CHECKING:
    from src.orchestrate.loop.sandbox import InstallResult

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


def evaluate_installability_gate(
    graph: "DepGraph | None",
    replay: "InstallResult | None" = None,
) -> GateResult:
    """Installability (= ebsr) gate.

    BINDING path (``replay is not None``): under Model B, ``run_v3``'s sole
    executor is a fresh full-script replay from base every cycle — there is no
    separate terminal-replay step, so the latest cycle's ``InstallResult`` IS
    the installability proof. ``passed`` is exactly ``replay.rc == 0``.

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
) -> tuple[GateResult, GateResult]:
    """The two gates in ladder order: (installability, testability).

    ``replay`` threads the orchestrator's latest per-cycle replay result
    (``run_v3``'s ``_last_replay_result``) into the installability gate so it
    is binding on the canonical path. ``None`` only on the ``block_emit``
    ablation, which falls back to the provisional graph-frontier heuristic.
    """
    return (
        evaluate_installability_gate(graph, replay=replay),
        evaluate_testability_gate(run_tests_verified),
    )


# === gate_signature.py: normalized pytest-outcome fingerprint (powers run_v3 no-progress giveup) ===
# pytest short-test-summary lines: "FAILED path::test - ExcClass: msg" / "ERROR path::test".
# Capture kind + node id + (optional) exception class; DROP the variable message.
_SUMMARY_LINE_RE = re.compile(
    r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+([A-Za-z_][\w.]*))?\s*$",
    re.MULTILINE,
)
# The final "=== N failed, M passed, K error[s] in T s ===" band (pytest -q always prints it).
_SUMMARY_BAND_RE = re.compile(
    r"^=+.*\b(?:failed|passed|error|errors|no tests ran)\b.*=+$",
    re.MULTILINE,
)
_COUNT_RE = re.compile(
    r"\b(\d+)\s+(failed|passed|errors?|skipped|xfailed|xpassed|deselected|warnings?)\b"
)

# Volatile tokens stripped from the FALLBACK tail so an identical crash is stable.
_VOLATILE_SUBS = (
    (re.compile(r"\x1b\[[0-9;]*m"), ""),                 # ANSI colour
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),           # hex addresses
    (re.compile(r"\b\d+(?:\.\d+)?s\b"), "Ns"),           # durations "3.42s" / "12s"
    (re.compile(r"(?:/private)?/tmp/\S+"), "/tmp/PATH"),  # pytest tmp dirs
    (re.compile(r"\bline\s+\d+\b"), "line N"),           # traceback line numbers
    (re.compile(r"\[gw\d+\]"), "[gw]"),                  # xdist worker tags
    (re.compile(r"\bpid:?\s*\d+\b", re.IGNORECASE), "pid N"),
)


def _digest(payload: str) -> str:
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:16]


def outcome_signature(passed: bool, out: str) -> str:
    """Stable fingerprint of one VERIFY_TEST_CMD run.

    ``passed`` is the VERIFIED gate result (``done_gate._verified_test_run_passed``),
    NOT the raw pytest rc — so a hollow pass (zero tests collected, all skipped)
    signs as a FAILURE and can still trip the no-progress detector. A verified
    pass always signs as the sentinel ``"pass"`` (never equal to any failure
    signature). Two failing runs sign identically iff their normalized failing
    node-id set, exception classes, and outcome counts match.
    """
    if passed:
        return "pass"
    text = out or ""
    fails = sorted(
        f"{kind} {nid} {exc or ''}".rstrip()
        for kind, nid, exc in _SUMMARY_LINE_RE.findall(text)
    )
    band = "\n".join(_SUMMARY_BAND_RE.findall(text))
    counts = sorted(f"{n} {word}" for n, word in _COUNT_RE.findall(band))
    if fails or counts:
        return "fail:" + _digest("\n".join(fails) + "\x00" + "\n".join(counts))
    # No pytest summary at all (collection crash, install error, segfault,
    # non-pytest command): fall back to a volatility-normalized tail so an
    # identical crash is still stable cycle-to-cycle.
    tail = "\n".join(ln for ln in text.splitlines() if ln.strip())[-2000:]
    for rx, repl in _VOLATILE_SUBS:
        tail = rx.sub(repl, tail)
    return "fail:" + _digest(tail)


def next_stall(prev_sig: str | None, sig: str, stall: int) -> int:
    """Advance the consecutive-identical-failing-signature counter.

    A verified pass (``sig == "pass"``) or a CHANGED failing signature resets the
    run's momentum; an unchanged failing signature extends the stall. Returns the
    new stall length (number of consecutive cycles sharing ``sig``).

        pass                       -> 0        (suite green / progress possible)
        first sight of a failure   -> 1
        same failure as last cycle -> stall + 1
    """
    if sig == "pass":
        return 0
    if prev_sig is not None and sig == prev_sig:
        return stall + 1
    return 1


# === verify_cache.py: per-container-generation VERIFY_TEST_CMD memo ===
class VerifyTestCache:
    """Memoize ``exec_test()`` per container generation.

    exec_test: () -> (ok: bool, out: str)   raw ``sandbox_execute(VERIFY_TEST_CMD)``.
    gen:       () -> int                     current container generation (monotonic).
    """

    def __init__(
        self,
        exec_test: Callable[[], tuple[bool, str]],
        gen: Callable[[], int],
    ) -> None:
        self._exec_test = exec_test
        self._gen = gen
        self._token: int | None = None
        self._result: tuple[bool, str] | None = None

    def run(self) -> tuple[bool, str]:
        g = self._gen()
        if self._result is not None and self._token == g:
            return self._result
        result = self._exec_test()
        self._token = g
        self._result = result
        return result
