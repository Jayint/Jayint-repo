# tests/test_contracts_apply.py
from src.envstate.contracts.apply import apply_patch
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import ContractStatusEvent, Edge, Node
from src.envstate.contracts.patch import GraphPatch


def test_add_nodes_edges_events_appends():
    g0 = ContractGraph()
    g1 = apply_patch(
        g0,
        GraphPatch(
            add_nodes=(Node("contract:a", "Contract", {"level": "atomic"}),),
            add_edges=(Edge("req:x", "implies_contract", "contract:a"),),
            add_status_events=(ContractStatusEvent("contract:a", "unknown", "envrev:000"),),
        ),
    )
    assert g0.nodes == ()  # original untouched (immutable)
    assert len(g1.nodes) == 1 and len(g1.edges) == 1 and len(g1.status_events) == 1


def test_update_node_replaces_in_place_by_id():
    g0 = ContractGraph(nodes=(Node("contract:a", "Contract", {"validation_state": "validator_unknown"}),))
    g1 = apply_patch(
        g0, GraphPatch(update_nodes=(Node("contract:a", "Contract", {"validation_state": "validator_confirmed"}),))
    )
    assert g1.node("contract:a").data["validation_state"] == "validator_confirmed"
    assert len(g1.nodes) == 1


def test_invalidate_marks_not_deletes():
    g0 = ContractGraph(
        nodes=(Node("contract:a", "Contract", {}),),
        edges=(Edge("a", "declares", "b"),),
    )
    g1 = apply_patch(
        g0,
        GraphPatch(invalidate_nodes=("contract:a",), invalidate_edges=(Edge("a", "declares", "b"),)),
    )
    assert g1.node("contract:a").invalidated is True
    assert len(g1.nodes) == 1  # not deleted
    assert g1.edges[0].invalidated is True
