"""The per-error notebook (spec 2026-07-08 §5.2) + the single progress rule (§5.4) +
attempts-axis persistence (§13.2).

A ``RepairSession`` is one sustained conversation scoped to one error. Its ``steps`` are the
compounding memory the agent reasons over — the fix for the amnesiac cold-repair problem.
Pure: no Docker, no LLM, no globals."""
from __future__ import annotations

from dataclasses import dataclass, field

from python_deps.depgraph.schema import Attempt
from src.envstate.repair_types import ReplayResult


@dataclass
class Step:
    kind: str                        # "probe" | "patch"
    summary: str
    cap: str | None = None
    accepted: bool | None = None
    replay: ReplayResult | None = None
    progress: bool | None = None
    output: str = ""                 # probe result (read-only investigation output)


@dataclass
class RepairSession:
    seed_node: str
    seed_cap: str
    steps: list = field(default_factory=list)

    def probed(self, cap) -> bool:
        return any(s.kind == "probe" and s.cap == cap for s in self.steps)

    def render_for_agent(self) -> str:
        """What the agent SEES each turn: the full running log (the compounding memory)."""
        if not self.steps:
            return f"(fresh) failing node {self.seed_node}, missing {self.seed_cap}"
        parts = []
        for i, s in enumerate(self.steps):
            tail = ""
            if s.kind == "patch" and s.replay is not None:
                tail = "→ok" if s.replay.ok else f"→{s.replay.failing_cap}"
            elif s.kind == "probe" and s.output:
                tail = f"→{s.output}"
            parts.append(f"{i + 1}.{s.summary}{tail}")
        return " | ".join(parts)


def made_progress(session: RepairSession, result: ReplayResult) -> bool:
    """Spec §5.4: the SINGLE progress rule (replaces 4 counters + 2 turn caps).

    Progress iff the error is resolved, or the failing NODE or its missing capability
    changed vs a baseline — the last patch's replay, or (on the session's FIRST patch,
    where there is no prior replay yet) the session's own SEED signature. Comparing the
    first patch against the seed — instead of unconditionally crediting it — means a
    no-op patch (e.g. SessionAgent's empty-PatchProposal fallback) is correctly judged
    as no-progress even on turn 1, so the stall rule can actually end it honestly.

    Node identity is compared alongside cap: fixing node A can legitimately reveal a
    DIFFERENT failing node B (spec §13.1, "the session follows the failure forward") —
    that must also register as progress, not just a same-node signature change."""
    if result.ok:
        return True
    last = next((s for s in reversed(session.steps)
                 if s.kind == "patch" and s.replay is not None), None)
    baseline_node = last.replay.failing_node if last is not None else session.seed_node
    baseline_cap = last.replay.failing_cap if last is not None else session.seed_cap
    return result.failing_node != baseline_node or result.failing_cap != baseline_cap


def persist_session_to_attempts(graph, session: RepairSession, node_id: str):
    """Spec §13.2: fold each PATCH step onto the target node's ``attempts`` axis
    (durable, to_dict-serialized graph state). Probes are investigation, not attempts."""
    node = graph.get(node_id)
    if node is None:
        return graph
    for s in session.steps:
        if s.kind != "patch":
            continue
        outcome = "succeeded" if (s.replay and s.replay.ok) else "failed"
        node = node.with_attempt(Attempt(command=s.summary, outcome=outcome,
                                         check="repair_session", cycle=len(node.attempts)))
    return graph.with_node(node)
