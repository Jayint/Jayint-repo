"""Normalized replay outcome shared by the production Docker path and the offline eval.

A failing ``ReplayResult`` doubles as the "error" handed to ``fix_one_error`` — the first
failing command in a clean replay IS the localized error (spec 2026-07-08 §5.1)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayResult:
    ok: bool
    failing_node: str | None = None
    failing_cap: str | None = None
    failing_command: str | None = None
    output: str = ""
