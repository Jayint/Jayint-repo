import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType  # noqa: E402
from python_deps.depgraph.ids import service_id, config_id  # noqa: E402
from python_deps.depgraph.schedule import scheduler_frontier  # noqa: E402


def _graph(service_state):
    svc = Node(id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=service_state,
               check_command="pg_isready", fix_candidates=("service:postgres:14",),
               chosen_fix="service:postgres:14", evidence="x", provenance="x",
               data={"service_confidence": "confirmed", "start_recipe": {"start": "x"}})
    binding = Node(id=config_id("DB_STRING"), type=NodeType.CONFIG, name="DB_STRING",
                   layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
                   check_command='psql "u" -c "select 1"', fix_candidates=("env:DB_STRING=u",),
                   chosen_fix="env:DB_STRING=u", evidence="x", provenance="service binding",
                   data={"binding": True})
    edge = Edge(src=binding.id, dst=svc.id, relation=EdgeType.REQUIRES, origin="service")
    return DepGraph(nodes=(svc, binding), edges=(edge,))


def test_binding_in_frontier_when_service_satisfied_and_allowed():
    ids = {n.id for n in scheduler_frontier(_graph(State.SATISFIED), allow_services=True)}
    assert config_id("DB_STRING") in ids


def test_binding_excluded_when_service_unsatisfied():
    ids = {n.id for n in scheduler_frontier(_graph(State.MISSING), allow_services=True)}
    assert config_id("DB_STRING") not in ids


def test_binding_excluded_off_arm():
    ids = {n.id for n in scheduler_frontier(_graph(State.SATISFIED), allow_services=False)}
    assert config_id("DB_STRING") not in ids
