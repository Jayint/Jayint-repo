"""Pure-function helpers for the v3 orchestrator loop.

These free functions hold a few low-state operations so the loop body can call
them without duplicating logic or sharing mutable closure state. (Historically
shared with the retired legacy planner-driven loop; ``run_v3`` is now the sole caller.)
"""
from __future__ import annotations

from src.orchestrate.loop.world_model import WorldModelMap, apply_deterministic


def host_refresh_facts(
    current_map: WorldModelMap,
    probe,
    manifest,
) -> WorldModelMap:
    """Apply the deterministic host probe to ``current_map`` and return the result.

    A no-op — returns ``current_map`` unchanged — when either ``probe`` or
    ``manifest`` is ``None``.  This mirrors the ``if probe is not None and
    manifest is not None`` guard in both loop bodies and makes it a single
    source of truth.

    Args:
        current_map: The current world-model snapshot.
        probe:       Callable returning a fresh env snapshot, or ``None``.
        manifest:    Parsed repo manifest, or ``None``.

    Returns:
        Updated ``WorldModelMap`` with deterministic facts applied, or
        ``current_map`` unchanged if either arg is ``None``.
    """
    if probe is None or manifest is None:
        return current_map
    return apply_deterministic(current_map, probe(), manifest)
