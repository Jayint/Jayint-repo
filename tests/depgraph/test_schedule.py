"""Tests for confirmed-service scheduling and start_recipe rendering (Task 5).

Tests for:
  - confirmed MISSING SERVICE with satisfied SystemLib prereq becomes actionable
    when allow_services=True; excluded by default.
  - Inferred services (no service_confidence) never surface.
  - ObligationPacket.start_recipe is populated from node.data.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _provisioning_graph(service_state, syslib_state):
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType,
    )
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=service_state, check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed",
                     "start_recipe": {"start": "START_CMD"}})
    sysl = Node(id="syslib:postgresql", type=NodeType.SYSTEM_LIB, name="postgresql",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=syslib_state, check_command="command -v pg_ctlcluster",
                chosen_fix="apt:postgresql")
    g = DepGraph().with_node(svc).with_node(sysl)
    return g.with_edge(Edge(src="service:postgres", dst="syslib:postgresql",
                            relation=EdgeType.REQUIRES, origin="service"))


def test_confirmed_service_actionable_only_when_allowed_and_prereq_satisfied():
    from python_deps.depgraph.schema import State
    from python_deps.depgraph.schedule import scheduler_frontier
    g = _provisioning_graph(State.MISSING, State.SATISFIED)
    assert [n.id for n in scheduler_frontier(g, allow_services=True)] == ["service:postgres"]
    assert scheduler_frontier(g) == ()                        # default off: excluded
    g2 = _provisioning_graph(State.MISSING, State.MISSING)   # prereq not installed
    assert scheduler_frontier(g2, allow_services=True) == ()  # blocked by SystemLib


def test_packet_renders_start_recipe():
    from python_deps.depgraph.schema import State
    from python_deps.depgraph.schedule import scheduler_frontier, frame_obligation
    from src.envstate.graph_scheduler import packet_to_task
    g = _provisioning_graph(State.MISSING, State.SATISFIED)
    node = scheduler_frontier(g, allow_services=True)[0]
    task = packet_to_task(frame_obligation(g, node))
    assert any("START_CMD" in f for f in task.facts)


def test_inferred_service_never_actionable():
    """A SERVICE without service_confidence='confirmed' is never scheduled."""
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State,
    )
    from python_deps.depgraph.schedule import scheduler_frontier
    svc = Node(id="service:redis", type=NodeType.SERVICE, name="redis",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=State.MISSING, check_command="redis-cli ping",
               data={})   # no service_confidence key
    g = DepGraph().with_node(svc)
    assert scheduler_frontier(g, allow_services=True) == ()


def test_frame_obligation_populates_start_recipe():
    """frame_obligation must copy start_recipe from node.data into the packet."""
    from python_deps.depgraph.schema import State
    from python_deps.depgraph.schedule import scheduler_frontier, frame_obligation
    g = _provisioning_graph(State.MISSING, State.SATISFIED)
    node = scheduler_frontier(g, allow_services=True)[0]
    packet = frame_obligation(g, node)
    assert packet.start_recipe == {"start": "START_CMD"}


def test_frame_obligation_start_recipe_none_when_absent():
    """frame_obligation must not crash when node.data has no start_recipe."""
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State,
    )
    from python_deps.depgraph.schedule import frame_obligation
    node = Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.MISSING, check_command="python -c 'import requests'")
    g = DepGraph().with_node(node)
    packet = frame_obligation(g, node)
    assert packet.start_recipe is None
