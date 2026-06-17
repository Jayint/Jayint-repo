# tests/test_contracts_schema.py
from src.envstate.contracts import schema


def test_edge_rules_cover_every_edge_type():
    for et in schema.EdgeType:
        assert et.value in schema.EDGE_RULES, f"missing edge rule for {et}"


def test_declares_edge_endpoints():
    src_types, tgt_types = schema.EDGE_RULES["declares"]
    assert schema.NodeType.REPO_ARTIFACT.value in src_types
    assert schema.NodeType.REQUIREMENT.value in tgt_types


def test_transition_targets_three_node_types():
    _src, tgt_types = schema.EDGE_RULES["targets"]
    assert tgt_types == frozenset(
        {
            schema.NodeType.CONTRACT.value,
            schema.NodeType.FAILURE.value,
            schema.NodeType.OPEN_PROBLEM.value,
        }
    )


def test_host_and_maintainer_node_sets_are_disjoint():
    assert not (schema.HOST_OWNED_NODE_TYPES & schema.MAINTAINER_NODE_TYPES)
    # Capability is host-only (locked decision 3).
    assert schema.NodeType.CAPABILITY.value in schema.HOST_OWNED_NODE_TYPES
    assert schema.NodeType.CONTRACT.value in schema.MAINTAINER_NODE_TYPES


def test_redact_secrets_masks_common_tokens():
    text = "export OPENAI_API_KEY=sk-ABCDEF1234567890 and TOKEN=ghp_aaaabbbbccccdddd"
    out = schema.redact_secrets(text)
    assert "sk-ABCDEF1234567890" not in out
    assert "ghp_aaaabbbbccccdddd" not in out
    assert "[REDACTED]" in out
