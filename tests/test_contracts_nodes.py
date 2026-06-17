# tests/test_contracts_nodes.py
import dataclasses

import pytest

from src.envstate.contracts.nodes import (
    ContractStatusEvent,
    Edge,
    Node,
    edge_from_dict,
    edge_to_dict,
    event_from_dict,
    event_to_dict,
    node_from_dict,
    node_to_dict,
)


def test_node_is_frozen():
    n = Node(id="contract:x", type="Contract", data={"subject": "torch"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        n.id = "other"  # type: ignore[misc]


def test_node_roundtrip():
    n = Node(id="contract:x", type="Contract", data={"subject": "torch", "level": "atomic"})
    assert node_from_dict(node_to_dict(n)) == n


def test_node_from_dict_defaults_data_and_invalidated():
    n = node_from_dict({"id": "artifact:a", "type": "RepoArtifact"})
    assert n.data == {}
    assert n.invalidated is False


def test_edge_roundtrip():
    e = Edge(source="a", type="declares", target="b")
    assert edge_from_dict(edge_to_dict(e)) == e


def test_status_event_roundtrip():
    ev = ContractStatusEvent(
        contract_id="contract:x",
        status="violated",
        revision_id="envrev:003",
        evidence_ids=("failure:1",),
        summary="boom",
    )
    assert event_from_dict(event_to_dict(ev)) == ev


def test_status_event_evidence_defaults_to_empty_tuple():
    ev = event_from_dict({"contract_id": "c", "status": "unknown", "revision_id": "envrev:000"})
    assert ev.evidence_ids == ()
