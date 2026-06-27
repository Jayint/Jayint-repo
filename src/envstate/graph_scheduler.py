"""Envstate adapter for the graph scheduler.

Turns the pure scheduling layer's next actionable obligation into a
PlannerDecision the orchestrator already knows how to execute. The graph decides
*what & when*; this module only translates that into the existing Task/Planner
message types. It writes no graph state.
"""
from __future__ import annotations

from typing import Callable

from python_deps.depgraph.schema import DepGraph
from python_deps.depgraph.schedule import (
    ObligationPacket, frame_obligation, scheduler_frontier,
)
from src.envstate.world_model import PlannerDecision, Task


def packet_to_task(packet: ObligationPacket) -> Task:
    facts = []
    if packet.evidence:
        facts.append(f"evidence: {packet.evidence}")
    if packet.depends_on:
        facts.append("depends_on: " + ", ".join(packet.depends_on))
    if packet.certified_context:
        facts.append("already satisfied: " + ", ".join(packet.certified_context))
    return Task(
        goal=packet.goal,
        done_when=packet.check_command,
        layer=packet.layer,
        facts=tuple(facts),
        target_node_ids=(packet.node_id,),
    )


def _discover_task() -> Task:
    # Lazy import: orchestrator imports this module, so import its constant at call
    # time to avoid a circular import and keep a single source of truth.
    from src.envstate.orchestrator import VERIFY_TEST_CMD
    return Task(
        goal=(
            "All known requirements are satisfied but the test suite still fails. "
            "Run the suite, read the failure, and install or provide whatever the "
            "running code actually needs (a missing dynamic import, a system "
            "library, a runtime env var, or a service) until the tests pass."
        ),
        done_when=VERIFY_TEST_CMD,
        layer="tests",
        facts=(),
    )


def next_decision(
    graph: DepGraph | None,
    run_tests: Callable[[], bool],
    handed: dict[str, int] | None = None,
    attempt_cap: int = 3,
) -> tuple[PlannerDecision, str | None]:
    """Decide the next action from the certified graph (no LLM).

    Returns (decision, chosen_obligation_id). chosen_id is the node handed to the
    agent (so the caller can bump its oscillation counter), or None for the
    done / discover-task branches.
    """
    handed = handed or {}
    frontier = scheduler_frontier(graph) if graph is not None else ()
    eligible = [n for n in frontier if handed.get(n.id, 0) < attempt_cap]
    if eligible:
        node = eligible[0]
        decision = PlannerDecision(
            action="task", task=packet_to_task(frame_obligation(graph, node))
        )
        return decision, node.id
    if run_tests():
        return PlannerDecision(
            action="done", reason="graph-scheduler: frontier clean, tests pass"
        ), None
    return PlannerDecision(action="task", task=_discover_task()), None
