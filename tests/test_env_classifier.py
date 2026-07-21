# tests/test_env_classifier.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import json
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
from python_deps.depgraph.ids import package_id
from src.envstate.env_classifier import make_construction_classifier, _normalize, _SYSTEM_PROMPT


def _graph_with_pkg():
    return DepGraph().with_node(Node(id=package_id("psycopg2", "2.9.9"), type=NodeType.PACKAGE,
        name="psycopg2", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="2.9.9"))


def test_normalize_maps_recalled_shape():
    out = _normalize({"requirements": [
        {"id": "service:postgres", "type": "Service", "layer": "services",
         "state": "HINT", "check_command": None, "evidence_refs": ["pkg.00"]}]})
    req = out["patch"]["add_requirements"][0]
    assert req["promotion"] == "hint"            # state HINT -> promotion hint (lowercased)
    assert req["evidence_ref"] == "pkg.00"       # evidence_refs[0] -> evidence_ref


def test_classifier_appends_soft_service_node():
    g = _graph_with_pkg()
    # the bundle for this graph contains a package hit "pkg.00" (psycopg2)
    llm_json = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "candidate", "check_command": None, "evidence_refs": ["pkg.00"],
         "rationale": "psycopg2 implies postgres"}],
        "add_edges": [{"source": package_id("psycopg2", "2.9.9"), "target": "service:postgres",
                       "relation": "requires", "hard": True}]})   # LLM says hard; classifier forces soft
    classify = make_construction_classifier(lambda messages: llm_json)
    out = classify(g, "/nonexistent-repo")
    svc = out.get("service:postgres")
    assert svc is not None and svc.type is NodeType.SERVICE and svc.state is State.MISSING
    assert svc.data.get("promotion") == "candidate"
    # the edge is SOFT despite the LLM asking for hard
    edge = next(e for e in out.edges if e.src == package_id("psycopg2", "2.9.9")
                and e.dst == "service:postgres")
    assert edge.data.get("hard") is False


def test_classifier_drops_ungrounded_requirement():
    g = _graph_with_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "service:redis", "type": "Service", "name": "redis", "layer": "services",
         "state": "hint", "evidence_refs": ["does.not.exist"]}]})   # ungrounded -> dropped
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    assert out.get("service:redis") is None


def test_classifier_drops_unknown_service_kind():
    g = _graph_with_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "service:git", "type": "Service", "name": "git", "layer": "services",
         "state": "hint", "evidence_refs": ["pkg.00"]}]})
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    assert out.get("service:git") is None


def test_classifier_returns_graph_unchanged_on_junk():
    g = _graph_with_pkg()
    out = make_construction_classifier(lambda m: "not json")(g, "/nonexistent-repo")
    assert out is g                                   # best-effort: junk -> unchanged


def test_one_illegal_promotion_does_not_void_valid_siblings():
    # An illegal promotion ("active") on one grounded req must NOT void the whole batch:
    # _sanitize drops the bad req (so admit's all-or-nothing gate stays clean) and the
    # valid sibling is still admitted.
    g = _graph_with_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "candidate", "evidence_refs": ["pkg.00"]},
        {"id": "service:redis", "type": "Service", "name": "redis", "layer": "services",
         "state": "active", "evidence_refs": ["pkg.00"]}]})   # "active" illegal -> dropped, not voiding
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    assert out.get("service:postgres") is not None        # valid sibling survives
    assert out.get("service:redis") is None               # illegal-promotion req dropped


def test_malformed_value_types_do_not_void_valid_siblings():
    g = _graph_with_pkg()
    llm_json = json.dumps({
        "requirements": [
            {"id": "service:postgres", "type": "Service", "name": "postgres",
             "layer": "services", "state": "candidate", "evidence_refs": ["pkg.00"]},
            {"id": "service:redis", "type": "Service", "name": "redis",
             "layer": "services", "promotion": ["candidate"],
             "evidence_refs": ["pkg.00"]},
            {"id": "config:BAD", "type": ["Config"], "name": "BAD",
             "layer": "config", "state": "hint", "evidence_refs": [["pkg.00"]]},
        ],
        "add_edges": [
            {"source": package_id("psycopg2", "2.9.9"),
             "target": "service:postgres", "relation": ["requires"]},
        ],
    })
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    assert out.get("service:postgres") is not None
    assert out.get("service:redis") is None
    assert out.get("config:BAD") is None
    assert not out.edges


def test_non_read_only_check_command_req_is_dropped_not_voiding():
    # A grounded req with a mutating check_command would be gate-rejected; _sanitize drops
    # it so a valid sibling still admits.
    g = _graph_with_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "hint", "evidence_refs": ["pkg.00"]},
        {"id": "config:BAD", "type": "Config", "name": "BAD", "layer": "config",
         "state": "hint", "check_command": "pip install evil", "evidence_refs": ["pkg.00"]}]})
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    assert out.get("service:postgres") is not None
    assert out.get("config:BAD") is None


def test_trivial_check_req_is_dropped_without_voiding_valid_sibling():
    g = _graph_with_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "hint", "evidence_refs": ["pkg.00"]},
        {"id": "config:BAD", "type": "Config", "name": "BAD", "layer": "config",
         "state": "hint", "check_command": "true", "evidence_refs": ["pkg.00"]}]})
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    assert out.get("service:postgres") is not None
    assert out.get("config:BAD") is None


def test_quoted_nonempty_config_check_is_admitted():
    g = _graph_with_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "config:DATABASE_URL", "type": "Config", "name": "DATABASE_URL",
         "layer": "config", "state": "candidate",
         "check_command": 'test -n "$DATABASE_URL"', "evidence_refs": ["pkg.00"]}]})
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    config = out.get("config:DATABASE_URL")
    assert config is not None
    assert config.check_command == 'test -n "$DATABASE_URL"'


def _graph_with_psycopg3():
    return DepGraph().with_node(Node(id=package_id("psycopg", "3.1"), type=NodeType.PACKAGE,
        name="psycopg", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="3.1"))


def test_prompt_mentions_node_id_anchoring():
    assert "node_id" in _SYSTEM_PROMPT


def test_invalid_relation_edge_dropped_not_voiding():
    # an edge with an invalid relation must be dropped, leaving the valid node admitted
    g = _graph_with_psycopg3()
    llm = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "candidate", "evidence_refs": ["pkg.00"]}],
        "add_edges": [{"source": package_id("psycopg", "3.1"), "target": "service:postgres",
                       "relation": "depends_on", "hard": True}]})   # bad relation
    out = make_construction_classifier(lambda m: llm)(g, "/nonexistent-repo")
    assert out.get("service:postgres") is not None         # node still admitted (batch not voided)
    # the only proposed edge to service:postgres was the invalid one -> it must be dropped entirely
    assert not any(e.dst == "service:postgres" for e in out.edges)


def test_valid_relation_edge_survives_soft():
    g = _graph_with_psycopg3()
    llm = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "candidate", "evidence_refs": ["pkg.00"]}],
        "add_edges": [{"source": package_id("psycopg", "3.1"), "target": "service:postgres",
                       "relation": "requires", "hard": True}]})
    out = make_construction_classifier(lambda m: llm)(g, "/nonexistent-repo")
    e = next(e for e in out.edges if e.dst == "service:postgres")
    assert e.relation.value == "requires" and e.data.get("hard") is False
