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
from src.envstate.env_classifier import make_construction_classifier, _normalize


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


def test_classifier_returns_graph_unchanged_on_junk():
    g = _graph_with_pkg()
    out = make_construction_classifier(lambda m: "not json")(g, "/nonexistent-repo")
    assert out is g                                   # best-effort: junk -> unchanged
