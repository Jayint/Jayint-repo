"""Termination taxonomy + evidence builders for the run loop (spec §4E R5, peeled from run.py).

Home of ``TerminationReason`` — the ONE canonical definition (a duplicated enum makes member
identity diverge across a re-imported module and flips v3 stop-reasons, e.g. planner_done ->
max_cycles; guarded by tests/test_constants_single_source.py). ``run.py`` imports these back
rather than redefining them. Depends only on the ``constants`` leaf, so importing this never
drags in the run loop.
"""
import enum

from src.orchestrate.loop.constants import VERIFY_TEST_CMD


# ---------------------------------------------------------------------------
# Internal termination taxonomy (v3 / graph-scheduler arm)
# ---------------------------------------------------------------------------

class TerminationReason(enum.Enum):
    """Typed termination cause for the v1/v3 loops.

    Maps to the external stop_reason strings via ``_to_stop_reason``. Introduced
    so *internal* code (logging, future extension) can distinguish the three
    logically-distinct giveup causes. The external contract still collapses all
    three to ``"planner_giveup"`` — callers observing stop_reason strings cannot
    tell them apart, by design.
    """
    DONE            = "done"
    DONE_FLAG       = "done_flag"
    GIVEUP_RESIDUAL = "giveup_residual"   # LLM giveup or runtime divergence
    GIVEUP_BUDGET   = "giveup_budget"     # LLM repair turns exhausted
    GIVEUP_STUCK    = "giveup_stuck"      # no new graph obligations (discover rounds)
    GIVEUP_NO_PROGRESS = "giveup_no_progress"  # verified test-gate signature unchanged
                                           # for NO_PROGRESS_CYCLES cycles despite repair
                                           # activity — a residual/phantom the graph
                                           # cannot close (design: residual-giveup-fix.md)
    GIVEUP_CONFIG   = "giveup_config"     # targeted obligation but no exec_readonly/client:
                                           # typed repair is impossible, and there is no
                                           # free-text fallback to silently downgrade to
    GIVEUP_REPLAY   = "giveup_replay"     # a success door (scheduler "done" OR maintainer
                                           # done_flag) opened but the latest per-cycle
                                           # replay (Model B's sole executor) did not
                                           # reproduce from base (None or rc!=0) — a build
                                           # that doesn't build is never reported as success
                                           # (guarded by `_finalize_if_replayed` in run_v3)
    MAX_CYCLES      = "max_cycles"


_TERMINATION_TO_STOP_REASON: dict[TerminationReason, str] = {
    TerminationReason.DONE:            "planner_done",
    TerminationReason.DONE_FLAG:       "done_flag",
    TerminationReason.GIVEUP_RESIDUAL: "planner_giveup",
    TerminationReason.GIVEUP_BUDGET:   "planner_giveup",
    TerminationReason.GIVEUP_STUCK:    "planner_giveup",
    TerminationReason.GIVEUP_NO_PROGRESS: "planner_giveup",
    TerminationReason.GIVEUP_CONFIG:   "planner_giveup",
    TerminationReason.GIVEUP_REPLAY:   "planner_giveup",
    TerminationReason.MAX_CYCLES:      "max_cycles",
}


def _to_stop_reason(reason: TerminationReason) -> str:
    """Map an internal ``TerminationReason`` to the external stop-reason string.

    The returned string is identical to the literal that was previously
    hard-coded at each return site — callers observing stop_reason strings
    are unaffected.
    """
    return _TERMINATION_TO_STOP_REASON[reason]


# ---------------------------------------------------------------------------
# Evidence builders (peeled from run_v3): wrap a failing install / test-gate run
# as a single citable EvidenceBundle so the repair proposer can reference it.
# ---------------------------------------------------------------------------
def _build_install_evidence(result, failed_id, cycle):
    """Wrap a FAILED binding-install InstallResult as a single-item EvidenceBundle so the
    repair proposer sees the install stderr (RepairScope.failed_output) AND can cite it
    (PatchGate requires every proposed requirement/script-patch to reference a known evidence
    id). The install runs from a fresh-from-base container = 'fresh_replay'.

    ``failed_id`` is whatever localize_install_failure resolved — a #@node id OR a #@block id —
    so it is written to BOTH Evidence.node_id and Evidence.block_id. run_structured_repair looks
    the id up as a block_id, and build_repair_scope copies stderr only when
    ``ev.block_id == failed_block.block_id``; setting block_id keeps the stderr visible on a
    #@block failure, while node_id stays correct for the common graph-node case.
    """
    from src.orchestrate.loop.evidence import Evidence, EvidenceBundle
    ev = Evidence(
        evidence_id=f"install.{cycle}.{failed_id or 'unknown'}",
        container_kind="fresh_replay",
        command=result.failing_command or "(install script)",
        rc=result.rc,
        output_excerpt=(result.stderr or "")[-2000:],
        cycle=cycle,
        node_id=failed_id,
        block_id=failed_id,
    )
    return EvidenceBundle().with_item(ev)


def _build_testgate_evidence(out, cycle, target_id):
    """Wrap the latest FAILING VERIFY_TEST_CMD output as a single citable Evidence
    item so a task-branch obligation repair with no attached manual block sees the
    real failure text AND can cite it — instead of the empty EvidenceBundle() that
    forces the proposer to hallucinate an evidence_ref against an empty citation
    list before finding the add_providers loophole. Mirrors _build_install_evidence
    (which threads install stderr on the binding path)."""
    from src.orchestrate.loop.evidence import Evidence, EvidenceBundle
    if not out:
        return EvidenceBundle()
    ev = Evidence(
        evidence_id=f"testgate.{cycle}.{target_id or 'unknown'}",
        container_kind="fresh_replay",
        command=VERIFY_TEST_CMD,
        rc=1,
        output_excerpt=(out or "")[-2000:],
        cycle=cycle,
        node_id=target_id,
        block_id=target_id,
    )
    return EvidenceBundle().with_item(ev)
