"""Design-point logger for the react arm (fresh — NOT arm C's repair_log). Every line is
tagged with the spec guarantee it demonstrates so a later reader can grep-verify the design."""
from __future__ import annotations

DESIGN = {
    "RUN":       "§2 run the WHOLE build script fresh from base",
    "CERTIFY":   "§9 host checks flip node state (install-tier only, no double pytest)",
    "TEST_GATE": "§5 host-owned done: ≥80% of executed tests pass",
    "PLAN":      "§4 agent emits ONE move (explore|patch)",
    "EXPLORE":   "§4 read-only investigation (no container mutation)",
    "PATCH":     "§4 agent's mutation = a replacement build script; re-run fresh",
    "COMPRESS":  "§6 observation compression (per-run context management)",
    "DONE":      "§5 script green AND tests ≥80% — host-verified",
    "GIVEUP":    "§11 max_steps hit — honest stop with best-effort script",
}


class ReactLog:
    def __init__(self, silent: bool = False):
        self.events: list[tuple[str, str]] = []
        self.silent = silent

    def d(self, tag: str, msg: str) -> None:
        self.events.append((tag, msg))
        if self.silent:
            return
        print(f"  [DESIGN:{tag:<10}] {msg}")
        inv = DESIGN.get(tag, "")
        if inv:
            print(f"   {'':<12}└─ {inv}")

    def count(self, tag: str) -> int:
        return sum(1 for t, _ in self.events if t == tag)
