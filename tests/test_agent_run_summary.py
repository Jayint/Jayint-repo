"""Tests for DockerAgent._collect_confirmed_in_image_services.

Task 10: agent-side handoff field for the in-image services eval.
"""
import sys
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))


def test_run_summary_emits_confirmed_in_image_services(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    import agent as agent_mod
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
               check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed",
                     "start_recipe": {"start": "S", "wait": "W", "createdb": "C",
                                      "certify": "pg_isready -h 127.0.0.1 -p 5432",
                                      "port": 5432, "db": "appdb"}})
    a._final_dep_graph = DepGraph().with_node(svc)
    services = a._collect_confirmed_in_image_services()
    assert services and services[0]["kind"] == "postgres"
    assert services[0]["start"] == "S" and services[0]["db"] == "appdb"


def test_no_field_when_service_not_satisfied(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    import agent as agent_mod
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
               check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed", "start_recipe": {"start": "S"}})
    a._final_dep_graph = DepGraph().with_node(svc)
    assert a._collect_confirmed_in_image_services() == []


def test_no_field_when_arm_off(monkeypatch):
    monkeypatch.delenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", raising=False)
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    import agent as agent_mod
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
               check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed",
                     "start_recipe": {"start": "S", "port": 5432}})
    a._final_dep_graph = DepGraph().with_node(svc)
    assert a._collect_confirmed_in_image_services() == []


def test_no_field_when_service_not_confirmed(monkeypatch):
    """A SATISFIED service that is not confidence=confirmed must not be emitted."""
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    import agent as agent_mod
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    svc = Node(id="service:redis", type=NodeType.SERVICE, name="redis",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
               data={"service_confidence": "candidate",
                     "start_recipe": {"start": "redis-server", "port": 6379}})
    a._final_dep_graph = DepGraph().with_node(svc)
    assert a._collect_confirmed_in_image_services() == []


def test_no_field_when_no_start_recipe(monkeypatch):
    """A confirmed+SATISFIED service without start_recipe must not be emitted."""
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    import agent as agent_mod
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    svc = Node(id="service:mysql", type=NodeType.SERVICE, name="mysql",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
               data={"service_confidence": "confirmed"})
    a._final_dep_graph = DepGraph().with_node(svc)
    assert a._collect_confirmed_in_image_services() == []
