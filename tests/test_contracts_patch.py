from src.envstate.contracts.patch import GraphPatch, parse_graph_patch


def test_empty_patch_from_empty_dict():
    p = parse_graph_patch({})
    assert p == GraphPatch()
    assert p.is_empty()


def test_parse_full_patch():
    p = parse_graph_patch(
        {
            "add_nodes": [{"id": "contract:a", "type": "Contract", "level": "atomic"}],
            "update_nodes": [{"id": "contract:a", "type": "Contract", "validation_state": "validator_confirmed"}],
            "add_edges": [{"source": "req:x", "type": "implies_contract", "target": "contract:a"}],
            "add_status_events": [{"contract_id": "contract:a", "status": "violated", "revision_id": "envrev:001"}],
            "invalidate_nodes": ["contract:old"],
            "invalidate_edges": [{"source": "a", "type": "declares", "target": "b"}],
        }
    )
    assert p.add_nodes[0].id == "contract:a"
    assert p.update_nodes[0].data["validation_state"] == "validator_confirmed"
    assert p.add_edges[0].target == "contract:a"
    assert p.add_status_events[0].status == "violated"
    assert p.invalidate_nodes == ("contract:old",)
    assert p.invalidate_edges[0].source == "a"
    assert not p.is_empty()


def test_parse_tolerates_missing_keys_and_non_lists():
    p = parse_graph_patch({"add_nodes": None, "junk": 1})
    assert p.add_nodes == ()
