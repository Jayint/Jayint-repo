import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State  # noqa: E402
from python_deps.depgraph.ids import service_id, config_id  # noqa: E402


def _graph(binding_state=State.SATISFIED, with_binding=True):
    svc = Node(id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
               check_command="pg_isready -h 127.0.0.1 -p 5432", fix_candidates=("service:postgres:14",),
               chosen_fix="service:postgres:14", evidence="x", provenance="x",
               data={"service_confidence": "confirmed", "port": 5432, "db": "appdb",
                     "bound_config": "DB_STRING",
                     "start_recipe": {"start": "pg_ctlcluster ...", "wait": "w",
                                      "createdb": "runuser -u postgres -- createdb appdb",
                                      "certify": "pg_isready -h 127.0.0.1 -p 5432", "port": 5432, "db": "appdb"}})
    nodes = [svc]
    if with_binding:
        binding = Node(id=config_id("DB_STRING"), type=NodeType.CONFIG, name="DB_STRING",
                       layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=binding_state,
                       check_command='psql "u" -c "select 1"',
                       fix_candidates=("env:DB_STRING=postgresql://postgres:postgres@127.0.0.1:5432/appdb",),
                       chosen_fix="env:DB_STRING=postgresql://postgres:postgres@127.0.0.1:5432/appdb",
                       evidence="x", provenance="service binding",
                       data={"binding": True, "bind_recipe": {"var": "DB_STRING",
                             "url": "postgresql://postgres:postgres@127.0.0.1:5432/appdb"}})
        nodes.append(binding)
    return DepGraph(nodes=tuple(nodes), edges=())


def _agent():
    import agent as agent_mod
    return agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)


def test_confirmed_services_includes_var_and_url(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    da = _agent()
    da._final_dep_graph = _graph()
    out = da._collect_confirmed_in_image_services()
    assert out and out[0]["var"] == "DB_STRING"
    assert out[0]["url"] == "postgresql://postgres:postgres@127.0.0.1:5432/appdb"


def test_no_binding_node_means_no_var_url(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    da = _agent()
    da._final_dep_graph = _graph(with_binding=False)
    out = da._collect_confirmed_in_image_services()
    assert out and "var" not in out[0] and "url" not in out[0]


def test_unsatisfied_binding_means_no_var_url(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    da = _agent()
    da._final_dep_graph = _graph(binding_state=State.UNKNOWN)
    out = da._collect_confirmed_in_image_services()
    assert out and "var" not in out[0] and "url" not in out[0]


def test_off_arm_returns_empty(monkeypatch):
    monkeypatch.delenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", raising=False)
    da = _agent()
    da._final_dep_graph = _graph()
    assert da._collect_confirmed_in_image_services() == []
