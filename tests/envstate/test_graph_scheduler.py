"""Tests for packet_to_task start_recipe rendering (Task 5).

Verifies that when an ObligationPacket carries a start_recipe with a start
command (and optionally createdb), packet_to_task renders them as fact lines.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
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


def test_packet_to_task_renders_start_command():
    """packet_to_task includes the start command in facts when start_recipe has 'start'."""
    from python_deps.depgraph.schema import State
    from python_deps.depgraph.schedule import scheduler_frontier, frame_obligation
    from src.envstate.graph_scheduler import packet_to_task
    g = _provisioning_graph(State.MISSING, State.SATISFIED)
    node = scheduler_frontier(g, allow_services=True)[0]
    task = packet_to_task(frame_obligation(g, node))
    assert any("START_CMD" in f for f in task.facts)


def test_packet_to_task_renders_createdb_when_present():
    """packet_to_task includes createdb fact when start_recipe has 'createdb'."""
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType,
    )
    from python_deps.depgraph.schedule import frame_obligation
    from src.envstate.graph_scheduler import packet_to_task
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=State.MISSING, check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed",
                     "start_recipe": {"start": "pg_ctlcluster 14 main start",
                                      "createdb": "createdb myapp"}})
    sysl = Node(id="syslib:postgresql", type=NodeType.SYSTEM_LIB, name="postgresql",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.SATISFIED, check_command="command -v pg_ctlcluster",
                chosen_fix="apt:postgresql")
    g = (DepGraph().with_node(svc).with_node(sysl)
         .with_edge(Edge(src="service:postgres", dst="syslib:postgresql",
                         relation=EdgeType.REQUIRES, origin="service")))
    packet = frame_obligation(g, svc)
    task = packet_to_task(packet)
    start_facts = [f for f in task.facts if "pg_ctlcluster 14 main start" in f]
    createdb_facts = [f for f in task.facts if "createdb myapp" in f]
    assert start_facts, "start command must appear in facts"
    assert createdb_facts, "createdb command must appear in facts when present"


def test_done_blocked_until_promoted_service_certified():
    from python_deps.depgraph.schema import State
    from src.envstate.graph_scheduler import next_decision
    # service MISSING but capped (ineligible from frontier) and tests "pass"
    # (the 1 pre-existing unit test hollow trap) -> must NOT be done
    g = _provisioning_graph(State.MISSING, State.SATISFIED)   # helper from Task 5 test
    handed = {"service:postgres": 3}  # at cap → ineligible, frontier empty
    decision, _ = next_decision(g, run_tests=lambda: True, allow_services=True,
                                handed=handed, attempt_cap=3)
    assert decision.action != "done"

    g_ok = _provisioning_graph(State.SATISFIED, State.SATISFIED)
    decision_ok, _ = next_decision(g_ok, run_tests=lambda: True, allow_services=True)
    assert decision_ok.action == "done"


def test_done_unchanged_for_non_service_graph():
    from python_deps.depgraph.schema import DepGraph
    from src.envstate.graph_scheduler import next_decision
    decision, _ = next_decision(DepGraph(), run_tests=lambda: True, allow_services=True)
    assert decision.action == "done"


def test_packet_to_task_no_start_recipe_no_extra_facts():
    """packet_to_task does not add start_recipe facts when start_recipe is None."""
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State,
    )
    from python_deps.depgraph.schedule import frame_obligation
    from src.envstate.graph_scheduler import packet_to_task
    node = Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.MISSING, check_command="python -c 'import requests'")
    g = DepGraph().with_node(node)
    packet = frame_obligation(g, node)
    task = packet_to_task(packet)
    # No fact should mention "start the service"
    assert not any("start the service" in f for f in task.facts)


def test_packet_to_task_renders_binding_facts():
    """A binding obligation is rendered as ONE atomic, &&-joined fact that runs
    BOTH setup commands and does NOT surface the host check_command."""
    from src.envstate.graph_scheduler import packet_to_task
    from python_deps.depgraph.schedule import ObligationPacket
    alter_user = "runuser -u postgres -- psql -c \"ALTER USER postgres PASSWORD 'postgres'\""
    bind_profile = "echo 'export DB_STRING=...' > /etc/profile.d/zz_service_bind.sh"
    packet = ObligationPacket(
        node_id="config:DB_STRING", node_type="config", tier=6, layer="config",
        goal="bind DB_STRING",
        evidence="bind DB_STRING to in-image postgres",
        check_command='psql "postgresql://postgres:postgres@127.0.0.1:5432/appdb" -c "select 1"',
        bind_recipe={"var": "DB_STRING",
                     "url": "postgresql://postgres:postgres@127.0.0.1:5432/appdb",
                     "alter_user": alter_user,
                     "bind_profile": bind_profile})
    task = packet_to_task(packet)
    binding_facts = [f for f in task.facts
                     if "ALTER USER postgres PASSWORD" in f or "/etc/profile.d/zz_service_bind.sh" in f]
    # (b) exactly ONE binding-related fact (not two).
    assert len(binding_facts) == 1, f"expected one binding fact, got {binding_facts!r}"
    fact = binding_facts[0]
    # (a) BOTH setup commands appear in the SAME single fact, &&-joined.
    assert "ALTER USER postgres PASSWORD" in fact
    assert "/etc/profile.d/zz_service_bind.sh" in fact
    assert f"{alter_user} && {bind_profile}" in fact
    # (c) the binding fact does NOT surface the host check_command / probe.
    assert "select 1" not in fact
    assert "psql \"postgresql" not in fact


def test_packet_to_task_no_bind_recipe_no_extra_facts():
    """Non-binding obligations (bind_recipe None) render no binding facts."""
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State,
    )
    from python_deps.depgraph.schedule import frame_obligation
    from src.envstate.graph_scheduler import packet_to_task
    node = Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.MISSING, check_command="python -c 'import requests'")
    g = DepGraph().with_node(node)
    task = packet_to_task(frame_obligation(g, node))
    assert not any("ALTER USER" in f for f in task.facts)
    assert not any("/etc/profile.d" in f for f in task.facts)
