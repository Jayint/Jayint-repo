"""Deterministic Maintainer: the v3 done-gate, no LLM.

The v3/graph-scheduler arm's world-model is the dep-graph; this maintainer only sets
done_flag + syncs progress from host-verified test evidence (``_v3_done_gate``). It never
finalizes on LLM/action say-so — done requires ``_verified_test_run_passed``.

(The legacy typed-reasoning blocker-extraction path — ``build_blocker_patch``/``maintain`` — was
excised in Phase 0 of the src/ stage-refactor together with its graph layer.)
"""
from __future__ import annotations

from .done_gate import _progress_synced_with_done, _verified_test_run_passed
from .world_model import TaskReport, WorldModelMap, merge_map


def _v3_done_gate(current_map: WorldModelMap, report: TaskReport) -> WorldModelMap:
    """v3 done-gate: set done_flag + sync progress from host-verified test evidence.

    The dep-graph is the sole world-model in v3. Anti-hollow: still requires
    ``_verified_test_run_passed`` (host evidence), never finalizes on LLM/action say-so.
    """
    done = current_map.done_flag or _verified_test_run_passed(report)
    progress_update = _progress_synced_with_done(current_map, done)
    if done == current_map.done_flag and progress_update is None:
        return current_map  # no-op fast path
    return merge_map(current_map, done_flag=done, progress=progress_update)


class DeterministicMaintainer:
    """Duck-typed stand-in for Maintainer (exposes ``.update``): the v3 done-gate.

    ``v3_only`` is retained for call-site compatibility (all live callers pass
    ``v3_only=True``); the done-gate is now the only path — the legacy typed-reasoning
    ``maintain`` branch was excised with its graph layer in Phase 0.
    """

    def __init__(self, *, v3_only: bool = False) -> None:
        self._v3_only = v3_only

    def update(self, current_map: WorldModelMap, report: TaskReport) -> WorldModelMap:
        return _v3_done_gate(current_map, report)
