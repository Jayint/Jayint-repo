"""Evaluation target helpers for benchmark-specific success semantics."""

from __future__ import annotations

from typing import Any


REPO2RUN_TARGET = "repo2run"
RATBENCH_TARGET = "ratbench"


def normalize_evaluation_target(target: Any = None) -> str:
    """Return the canonical evaluation target name."""
    if isinstance(target, dict):
        for key in ("evaluation_target", "benchmark_mode", "benchmark", "target", "name", "metric"):
            value = target.get(key)
            if value:
                return normalize_evaluation_target(value)
        return REPO2RUN_TARGET

    normalized = str(target or "").strip().lower().replace("_", "-")
    if normalized in {"rat", "ratbench", "essr", "rat-bench"}:
        return RATBENCH_TARGET
    return REPO2RUN_TARGET


def is_ratbench_target(target: Any = None) -> bool:
    return normalize_evaluation_target(target) == RATBENCH_TARGET


def coerce_benchmark_target(target: Any = None) -> dict[str, Any]:
    """Normalize target metadata while preserving optional benchmark clues."""
    if isinstance(target, dict):
        result = dict(target)
    else:
        result = {}
        if target:
            result["name"] = str(target)
    result["evaluation_target"] = normalize_evaluation_target(target)
    return result
