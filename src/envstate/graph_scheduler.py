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
    if packet.start_recipe and packet.start_recipe.get("start"):
        facts.append("start the service in-image (run, then the host re-checks "
                     f"`{packet.check_command}`): {packet.start_recipe['start']}")
        if packet.start_recipe.get("createdb"):
            facts.append("then create the bound database: "
                         f"{packet.start_recipe['createdb']}")
    if packet.bind_recipe:
        br = packet.bind_recipe
        au, bp = br.get("alter_user"), br.get("bind_profile")
        if au and bp:
            facts.append("Run this single command to configure the in-image database "
                         "(the host verifies it automatically afterward — do not run any check yourself): "
                         f"{au} && {bp}")
        elif au or bp:
            facts.append("Run this single command to configure the in-image database "
                         "(the host verifies it automatically afterward): "
                         f"{au or bp}")
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
    *,
    allow_services: bool | None = None,
) -> tuple[PlannerDecision, str | None]:
    """Decide the next action from the certified graph (no LLM).

    Returns (decision, chosen_obligation_id). chosen_id is the node handed to the
    agent (so the caller can bump its oscillation counter), or None for the
    done / discover-task branches.

    allow_services: when None (default), resolved from env var
    DOCKERAGENT_ENABLE_SERVICE_PROVISION; pass True/False explicitly in tests.
    """
    import os
    handed = handed or {}
    if allow_services is None:
        allow_services = os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"
    frontier = scheduler_frontier(graph, allow_services=allow_services) if graph is not None else ()
    eligible = [n for n in frontier if handed.get(n.id, 0) < attempt_cap]
    if eligible:
        node = eligible[0]
        decision = PlannerDecision(
            action="task", task=packet_to_task(frame_obligation(graph, node))
        )
        return decision, node.id
    from python_deps.depgraph.schema import NodeType, State
    if run_tests():
        promoted_unsatisfied = [
            n for n in (graph.nodes if graph is not None else ())
            if n.type is NodeType.SERVICE
            and n.data.get("start_recipe")
            and n.state is not State.SATISFIED
        ]
        if allow_services and promoted_unsatisfied:
            # Anti-hollow: tests "passing" while a required in-image service is not
            # host-certified up is the 1-unit-test-rides-to-0.2 trap (design §10).
            return PlannerDecision(action="task", task=_discover_task()), None
        return PlannerDecision(
            action="done", reason="graph-scheduler: frontier clean, tests pass"
        ), None
    return PlannerDecision(action="task", task=_discover_task()), None
