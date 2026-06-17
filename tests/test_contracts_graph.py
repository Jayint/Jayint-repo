from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import ContractStatusEvent, Edge, Node


def _graph():
    return ContractGraph(
        nodes=(
            Node("contract:goal:t", "Contract", {"level": "goal", "required": True}),
            Node("contract:a", "Contract", {"level": "atomic"}),
            Node("contract:dead", "Contract", {"level": "atomic"}, invalidated=True),
        ),
        edges=(Edge("contract:goal:t", "depends_on", "contract:a"),),
        status_events=(
            ContractStatusEvent("contract:a", "unknown", "envrev:000"),
            ContractStatusEvent("contract:a", "satisfied", "envrev:002", ("cmd:5",)),
        ),
    )


def test_empty_is_falsy_and_serializes():
    g = ContractGraph.empty()
    assert g.nodes == () and g.edges == () and g.status_events == ()
    assert g.to_dict() == {"nodes": [], "edges": [], "contract_status_events": []}


def test_get_node_skips_nothing_but_active_filters_invalidated():
    g = _graph()
    assert g.node("contract:dead") is not None
    ids = {n.id for n in g.active_nodes()}
    assert "contract:dead" not in ids and "contract:a" in ids


def test_nodes_by_type_and_required_goal_contracts():
    g = _graph()
    assert len(g.nodes_by_type("Contract")) == 2  # active only
    req = g.required_goal_contracts()
    assert [n.id for n in req] == ["contract:goal:t"]


def test_latest_status_returns_last_event():
    g = _graph()
    assert g.latest_status("contract:a").status == "satisfied"
    assert g.latest_status("contract:missing") is None


def test_out_in_edges():
    g = _graph()
    assert [e.target for e in g.out_edges("contract:goal:t", "depends_on")] == ["contract:a"]
    assert [e.source for e in g.in_edges("contract:a", "depends_on")] == ["contract:goal:t"]


def test_full_roundtrip():
    g = _graph()
    assert ContractGraph.from_dict(g.to_dict()) == g
