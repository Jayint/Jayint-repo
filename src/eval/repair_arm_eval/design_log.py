"""Design-point logger. Every line is tagged with the spec guarantee it demonstrates so a
later agent can grep the log and verify each key design is really implemented."""
from __future__ import annotations

DESIGN = {
    "RENDER":           "graph → build script (graph is the source of truth)",
    "CLEAN_REPLAY":     "§N2/§4 every replay is a fresh run from a clean base",
    "HOST_CERTIFY":     "§7 truth is host-only + revocable (only a check flips state)",
    "LOCALIZE":         "§7 per-node failure localization",
    "DIAGNOSE":         "§5.1 route repo-internal/residual/invalid OUT of env-repair",
    "SESSION_START":    "§5.2 ONE sustained session per error",
    "SESSION_PROBE":    "§5.3 agent may run READ-ONLY probes (never mutate the container)",
    "GATE":             "§5.3/§7 typed-patch gate is the trust boundary (agent can't certify)",
    "SESSION_PATCH":    "§5.3 agent's only mutation = a typed graph patch",
    "MEMORY":           "§G2/§5.2 the agent SEES its full prior history (compounding memory)",
    "PROGRESS":         "§5.4 single structured progress rule (replaces 4 counters + 2 caps)",
    "SESSION_RESOLVED": "§5.4 session ends when the host confirms the error is gone",
    "SESSION_STALL":    "§5.4 honest bounded give-up (stall/turn-cap)",
    "ATTEMPTS_PERSIST": "§13.2 session transcript persisted to the node's attempts axis",
    "DONE":             "§5.1 done = clean replay green (host-verified)",
    "GIVEUP":           "§5.1 global give-up (same error unrepaired)",
}


class DesignLog:
    def __init__(self, silent: bool = False):
        self.events: list[tuple[str, str]] = []
        self.silent = silent

    def d(self, tag: str, msg: str) -> None:
        self.events.append((tag, msg))
        if self.silent:
            return
        print(f"  [DESIGN:{tag:<16}] {msg}")
        inv = DESIGN.get(tag, "")
        if inv:
            print(f"   {'':<18}└─ {inv}")

    def count(self, tag: str) -> int:
        return sum(1 for t, _ in self.events if t == tag)
