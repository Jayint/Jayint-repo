"""The loop's e2e run-trace + proof harness (spec §4C loop/trace.py).

Folded (3b-4) from run_trace + trace_verify + proof — the recorder, the pure
assertions over its snapshot, and the pure proof-harness reducers. A real import
chain (proof -> trace_verify -> run_trace) collapsed into one file; the internal
imports drop out, so this is a net simplification.

``RunTracer`` is the ONE mutable, append-only collector for a `run_v3` call —
the same mutability exception granted to `ActionLedger` (`src/orchestrate/loop/ledger.py`).
It records facts as the loop runs (patchgate outcomes, discover-gate diagnoses,
per-cycle fresh replays, and the legacy/ablation "did this path execute" marks)
and, on exit, freezes them into an immutable `RunTrace` snapshot via
`snapshot()`. Nothing in this module reads or writes graph/world-model state —
it observes and records only.

This module intentionally has zero orchestrator wiring (that is a later,
separate task) — it is pure data plumbing, unit-testable standalone.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PatchGateRecord:
    cycle: int
    failed_block_id: str | None
    evidence_ref: str | None
    accepted: bool
    accepted_node_ids: tuple[str, ...]
    accepted_block_ids: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class DiscoverRecord:
    cycle: int
    command: str
    used_llm_mutation: bool
    new_node_ids: tuple[str, ...]
    diagnosis_modes: tuple[str, ...]


@dataclass(frozen=True)
class FreshReplayRecord:
    ran: bool
    setup_rc: int | None
    failing_command: str | None
    certified_node_ids: tuple[str, ...]
    unsatisfied_node_ids: tuple[str, ...]
    test_rc: int | None
    test_summary: str


@dataclass(frozen=True)
class RunTrace:
    repo: str = ""
    loop_mode: str = "v3_graph_typed_repair"
    used_emit_drain: bool = False
    used_repair_failed_nodes: bool = False
    used_build_agent_run: bool = False
    used_block_emit: bool = False          # block_emit lives only in the ablation; MUST be False in the method
    patchgate: tuple[PatchGateRecord, ...] = ()
    discover: tuple[DiscoverRecord, ...] = ()
    replays: tuple[FreshReplayRecord, ...] = ()   # one per cycle that actually replayed (Model B)
    manual_block_ids: tuple[str, ...] = ()
    stop_reason: str = ""
    gates: dict = field(default_factory=dict)

    @property
    def last_replay(self) -> "FreshReplayRecord | None":
        return self.replays[-1] if self.replays else None

    def to_dict(self) -> dict[str, Any]:
        """Plain, JSON-serializable dict.

        ``dataclasses.asdict`` recurses into dataclass fields and into the
        elements of list/tuple fields, converting each nested dataclass
        instance to a dict in turn — so `patchgate`/`discover`/`replays`
        (each a tuple of frozen records) come back as tuples of plain dicts
        with no dataclass instances left anywhere in the tree. ``last_replay``
        is a derived `@property`, not a dataclass field, so `asdict` does not
        touch it; it is deliberately left out of the dict (recomputable from
        `replays[-1]` by any consumer that has the dict form).
        """
        return dataclasses.asdict(self)


class RunTracer:
    """Append-only host-owned recorder (same mutability exception as ActionLedger)."""

    def __init__(self, repo: str = "") -> None:
        self._repo = repo
        self._used_emit_drain = False
        self._used_repair_failed_nodes = False
        self._used_build_agent_run = False
        self._used_block_emit = False
        self._patchgate: list[PatchGateRecord] = []
        self._discover: list[DiscoverRecord] = []
        self._replays: list[FreshReplayRecord] = []
        self._manual_block_ids: tuple[str, ...] = ()

    def mark_emit_drain(self) -> None:
        self._used_emit_drain = True

    def mark_repair_failed_nodes(self) -> None:
        self._used_repair_failed_nodes = True

    def mark_build_agent_run(self) -> None:
        self._used_build_agent_run = True

    def mark_block_emit(self) -> None:
        self._used_block_emit = True

    def record_patchgate(self, r: PatchGateRecord) -> None:
        self._patchgate.append(r)

    def record_discover(self, r: DiscoverRecord) -> None:
        self._discover.append(r)

    def record_replay(self, r: FreshReplayRecord) -> None:
        self._replays.append(r)

    def set_last_replay_tests(self, test_rc: int, test_summary: str) -> None:
        """Back-fill the LAST recorded replay's test-gate result in place.

        The test gate (``_run_tests_verified`` / ``_run_discover_gate``) runs
        as a SEPARATE call from the fresh-replay executor that produces
        ``FreshReplayRecord``s, so ``record_replay`` always records
        ``test_rc=None``/``test_summary=""`` — the install result only. Once
        the test gate result is known, this replaces the last list entry with
        a copy carrying the test fields (``dataclasses.replace`` — the
        record itself stays frozen; only the list entry, which this recorder
        owns, is swapped for a new one). Under Model B every cycle replays
        BEFORE the scheduler calls the test gate, so there is always a last
        replay to back-fill by the time this is called; if called more than
        once for the same replay, the most recent test run wins. No-op if no
        replay has been recorded yet.
        """
        if not self._replays:
            return
        self._replays[-1] = dataclasses.replace(
            self._replays[-1], test_rc=test_rc, test_summary=test_summary
        )

    def set_manual_blocks(self, ids: tuple[str, ...]) -> None:
        self._manual_block_ids = tuple(ids)

    def snapshot(self, *, stop_reason: str, gates: dict) -> RunTrace:
        return RunTrace(
            repo=self._repo,
            used_emit_drain=self._used_emit_drain,
            used_repair_failed_nodes=self._used_repair_failed_nodes,
            used_build_agent_run=self._used_build_agent_run,
            used_block_emit=self._used_block_emit,
            patchgate=tuple(self._patchgate),
            discover=tuple(self._discover),
            replays=tuple(self._replays),
            manual_block_ids=self._manual_block_ids,
            stop_reason=stop_reason,
            # Defensive copy (Part-1 review Minor): RunTrace is meant to be an
            # immutable, frozen snapshot, but `dict` is itself mutable — storing
            # the caller's live `gates` object by reference would let a
            # post-snapshot mutation of THAT dict retroactively change an
            # already-returned RunTrace. Copy it in.
            gates=dict(gates),
        )


# === trace_verify.py: pure assertions over a RunTrace / rendered artifact (the 3 e2e-proof claims) ===
def verify_canonical_trace(t: RunTrace) -> list[str]:
    errs: list[str] = []
    if t.used_emit_drain:
        errs.append("legacy emit_drain executed in canonical run")
    if t.used_repair_failed_nodes:
        errs.append("legacy repair_failed_nodes executed")
    if t.used_build_agent_run:
        errs.append("free-text build_agent.run executed")
    if t.used_block_emit:
        errs.append("block_emit ablation executed inside the method")
    if t.loop_mode != "v3_graph_typed_repair":
        errs.append(f"non-canonical loop_mode {t.loop_mode!r}")
    if not t.replays:
        errs.append("no fresh replay ran (fresh replay is the sole executor)")
    if t.stop_reason in ("done", "planner_done", "done_flag"):
        last = t.last_replay
        if last is None or not last.ran:
            errs.append("done reached without a fresh replay")
        elif last.setup_rc != 0:
            errs.append(f"done reached but latest fresh replay failed: {last.failing_command}")
        if t.gates.get("installability", {}).get("provisional", True):
            errs.append("installability gate still provisional on a done run")
    for d in t.discover:
        if d.used_llm_mutation:
            errs.append(f"discover cycle {d.cycle} used LLM mutation, not the deterministic gate")
    return errs


def verify_artifact_consistency(script_text: str, manual_block_ids: tuple[str, ...]) -> list[str]:
    errs: list[str] = []
    if "(UNSCHEDULED BLOCKS)" in script_text:
        errs.append("rendered setup.sh contains an UNSCHEDULED BLOCKS section")
    for bid in manual_block_ids:
        if bid not in script_text:
            errs.append(f"governed manual block {bid} missing from final setup.sh")
    return errs


def verify_local_import_guard(t: RunTrace) -> list[str]:
    # No discover cycle produced a package node for a REPO_INTERNAL_REF diagnosis.
    errs: list[str] = []
    for d in t.discover:
        if "repo_internal_reference" in d.diagnosis_modes:
            if any(nid.startswith("pkg:") for nid in d.new_node_ids):
                errs.append(f"discover cycle {d.cycle} added a package node for a repo-local import")
    return errs


# === proof.py: pure proof-harness helpers (finalize_trace / canonical_success / report reducers) ===
# stop_reason values that _to_stop_reason (orchestrator.py) maps success
# terminations to — kept as a local tuple (not imported) so this module has
# no orchestrator dependency; verify_canonical_trace uses the same literal set.
_DONE_REASONS = ("done", "planner_done", "done_flag")


def finalize_trace(
    tracer: "RunTracer",
    stop: str,
    gates_seen: Sequence[Sequence[Any]],
    script_text: str,
) -> tuple[RunTrace, dict[str, list[str]]]:
    """Snapshot ``tracer`` and run all three verifiers against it in one place.

    ``gates_seen`` is the driver's ``gate_observer=gates_seen.append``
    accumulator — a list of ``(installability_gate, testability_gate)``
    tuples, one per ``run_v3`` exit (``enable_gate_observability=True`` fires
    the observer exactly once, on the way out, but this accepts a sequence
    defensively). Only the LAST observation is binding. Each gate object is
    duck-typed (``.name``/``.passed``/``.provisional``/``.evidence``) so this
    module never needs to import ``GateResult``.
    """
    gates = {
        g.name: {"passed": g.passed, "provisional": g.provisional, "evidence": g.evidence}
        for g in (gates_seen[-1] if gates_seen else ())
    }
    trace = tracer.snapshot(stop_reason=stop, gates=gates)
    report = {
        "canonical": verify_canonical_trace(trace),
        "artifact": verify_artifact_consistency(script_text, trace.manual_block_ids),
        "local_import": verify_local_import_guard(trace),
    }
    return trace, report


def canonical_success(trace: RunTrace, script_text: str) -> bool:
    """The strongest per-repo proof claim: done, on a green fresh replay, via
    the canonical loop only, with an artifact-complete script, and no host
    certifier left unsatisfied.

    Intentionally stricter than the loop's own success signal: ``run_v3``'s
    ``_finalize_if_replayed`` (orchestrator.py) gates a `done`/`planner_done`/
    `done_flag` ``stop_reason`` on install rc0 alone, so a run can reach a
    success ``stop_reason`` while still carrying an unsatisfied reciped node
    (or a failing test) — ``canonical_success``, not ``stop_reason``, is the
    paper/report success metric.
    """
    last = trace.last_replay
    return bool(
        trace.stop_reason in _DONE_REASONS
        and last is not None
        and last.setup_rc == 0
        and last.test_rc == 0
        and not verify_canonical_trace(trace)
        and not verify_artifact_consistency(script_text, trace.manual_block_ids)
        and not last.unsatisfied_node_ids
    )


def repo_row(trace: RunTrace) -> dict[str, Any]:
    """One row of the per-repo proof table.

    Trace-only (no ``script_text``): every column here is derivable from the
    recorder's own facts. The one column that needs the rendered artifact
    (``canonical_success``, which folds in ``verify_artifact_consistency``) is
    intentionally NOT part of this row — callers that have the script text
    add it alongside (see ``scripts/run_v3_proof.py``).
    """
    last = trace.last_replay
    legacy_used = bool(
        trace.used_emit_drain
        or trace.used_repair_failed_nodes
        or trace.used_build_agent_run
        or trace.used_block_emit
    )
    added_nodes: set[str] = set()
    for pg in trace.patchgate:
        added_nodes.update(pg.accepted_node_ids)
    for d in trace.discover:
        added_nodes.update(d.new_node_ids)

    fresh_replay = bool(last is not None and last.setup_rc == 0)
    tests_pass = bool(last is not None and last.test_rc == 0)
    passed = trace.stop_reason in _DONE_REASONS and fresh_replay and tests_pass

    return {
        "repo": trace.repo,
        "result": "PASS" if passed else "FAIL",
        "legacy_used": legacy_used,
        "graph_nodes_added": len(added_nodes),
        "patchgate_accepts": sum(1 for pg in trace.patchgate if pg.accepted),
        "manual_blocks": len(trace.manual_block_ids),
        "fresh_replay": fresh_replay,
        "tests_pass": tests_pass,
        "residual_reason": "" if passed else trace.stop_reason,
    }


def aggregate(traces: Sequence[tuple[RunTrace, str]]) -> dict[str, Any]:
    """Reduce ``(RunTrace, script_text)`` pairs (one per repo run) to the
    report's aggregate counters.

    ``script_text`` — each repo's rendered ``setup.sh`` — is needed only for
    ``manual_block_artifact_mismatches`` (``verify_artifact_consistency``);
    every other counter is derivable from the trace alone.
    """
    total = len(traces)
    canonical_loop_runs = sum(
        1 for t, _ in traces if t.loop_mode == "v3_graph_typed_repair"
    )
    legacy_path_violations = sum(1 for t, _ in traces if verify_canonical_trace(t))
    replayed_green = sum(
        1 for t, _ in traces if t.last_replay is not None and t.last_replay.setup_rc == 0
    )
    manual_block_artifact_mismatches = sum(
        1 for t, script in traces if verify_artifact_consistency(script, t.manual_block_ids)
    )
    local_import_false_package_attempts = sum(
        len(verify_local_import_guard(t)) for t, _ in traces
    )
    return {
        "canonical_loop_runs": canonical_loop_runs,
        "legacy_path_violations": legacy_path_violations,
        "fresh_replay_pass_rate": (replayed_green / total) if total else 0.0,
        "manual_block_artifact_mismatches": manual_block_artifact_mismatches,
        "local_import_false_package_attempts": local_import_false_package_attempts,
    }


def _tuplify(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Coerce the named keys back to tuples (JSON round-trips tuples as lists)."""
    out = dict(d)
    for k in keys:
        if k in out and isinstance(out[k], list):
            out[k] = tuple(out[k])
    return out


def trace_from_dict(d: dict[str, Any]) -> RunTrace:
    """Inverse of ``RunTrace.to_dict()`` — reconstruct a frozen ``RunTrace``
    from its JSON-round-tripped dict form (as written to ``--trace-out``).

    ``dataclasses.asdict`` (used by ``to_dict``) turns every tuple field into
    a plain Python tuple already, but a JSON round-trip (``json.dumps`` has no
    tuple type) turns them into lists — this restores tuples on every field
    typed ``tuple[...]`` on the nested records, so a reconstructed ``RunTrace``
    is structurally identical (``==``) to the one that produced the dict.
    """
    patchgate = tuple(
        PatchGateRecord(**_tuplify(p, ("accepted_node_ids", "accepted_block_ids", "errors")))
        for p in d.get("patchgate", ())
    )
    discover = tuple(
        DiscoverRecord(**_tuplify(p, ("new_node_ids", "diagnosis_modes")))
        for p in d.get("discover", ())
    )
    replays = tuple(
        FreshReplayRecord(**_tuplify(p, ("certified_node_ids", "unsatisfied_node_ids")))
        for p in d.get("replays", ())
    )
    return RunTrace(
        repo=d.get("repo", ""),
        loop_mode=d.get("loop_mode", "v3_graph_typed_repair"),
        used_emit_drain=d.get("used_emit_drain", False),
        used_repair_failed_nodes=d.get("used_repair_failed_nodes", False),
        used_build_agent_run=d.get("used_build_agent_run", False),
        used_block_emit=d.get("used_block_emit", False),
        patchgate=patchgate,
        discover=discover,
        replays=replays,
        manual_block_ids=tuple(d.get("manual_block_ids", ())),
        stop_reason=d.get("stop_reason", ""),
        gates=d.get("gates", {}),
    )
