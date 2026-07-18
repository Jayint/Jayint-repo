"""Observability sink for the react arm (spec §15; fresh — NOT arm C's repair_log). One object
threaded as `log`, three roles: (1) design-point tags to stdout [DESIGN:*] proving control
flow; (2) a structured per-step trace (`.trace`) — prompts, compaction, run/test — appended to
JSONL when `trace_path` is set and always kept in memory; (3) a run-end `.summary` coverage.
Stdout gated by REACT_VERBOSE (off → quiet)."""
from __future__ import annotations

import json
import os

DESIGN = {
    "RUN":       "§2 run the WHOLE build script fresh from base",
    "CERTIFY":   "§9 host checks flip node state (install-tier only, no double pytest)",
    "TEST_GATE": "§5 host-owned done: ≥80% of executed tests pass",
    "PLAN":      "§4 agent emits ONE move (explore|patch)",
    "EXPLORE":   "§4 read-only investigation (no container mutation)",
    "PATCH":     "§4 agent's mutation = a replacement build script; re-run fresh",
    "COMPRESS":  "§6 observation compression (per-run context management)",
    "DONE":      "§5 script green AND tests pass the gate — host-verified",
    "GIVEUP":    "§11 max_steps hit — honest stop with best-effort script",
}


class ReactLog:
    def __init__(self, silent: bool | None = None, trace_path: str | None = None):
        self.silent = (os.getenv("REACT_VERBOSE") != "1") if silent is None else silent
        self.events: list[tuple[str, str]] = []
        self.records: list[dict] = []
        self._fh = open(trace_path, "w") if trace_path else None

    def d(self, tag: str, msg: str) -> None:
        self.events.append((tag, msg))
        if self.silent:
            return
        print(f"  [DESIGN:{tag:<10}] {msg}")
        inv = DESIGN.get(tag, "")
        if inv:
            print(f"   {'':<12}└─ {inv}")

    def trace(self, phase: str, **fields) -> None:
        rec = {"phase": phase, **fields}
        self.records.append(rec)
        if self._fh is not None:
            self._fh.write(json.dumps(rec, default=str) + "\n")
            self._fh.flush()

    def count(self, tag: str) -> int:
        return sum(1 for t, _ in self.events if t == tag)

    def summary(self) -> str:
        line = " ".join(f"{t}×{self.count(t)}" for t in sorted({t for t, _ in self.events}))
        if not self.silent:
            print(f"  --- coverage: {line} ---")
        return line

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
