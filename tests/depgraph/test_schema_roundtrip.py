"""to_dict -> from_dict must be lossless. The graph cache depends on it."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)


def _rich_graph() -> DepGraph:
    return (DepGraph()
            .with_node(Node(id="pkg:psycopg2==2.9.12", type=NodeType.PACKAGE, name="psycopg2",
                            layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                            version="2.9.12", state=State.MISSING,
                            build_from_source=True))
            .with_node(Node(id="pkg:Pillow==10.3", type=NodeType.PACKAGE, name="Pillow",
                            layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                            version="10.3", state=State.SATISFIED,
                            build_from_source=False))       # False, NOT None -- blocks() reads this
            .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                            layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                            state=State.MISSING, check_command="command -v pg_config",
                            chosen_fix="apt:libpq-dev",
                            fix_candidates=("apt:libpq-dev", "apt:postgresql-server-dev-all"),
                            evidence='Error: pg_config executable not found.',
                            provenance="runtime ingest",
                            data={"runtime_confidence": "runtime-deterministic"}))
            .with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="binary:pg_config",
                            relation=EdgeType.REQUIRES, origin="resolver",
                            marker='python_version >= "3.9"', data={"hard": True})))


def test_roundtrip_is_lossless():
    g = _rich_graph()
    back = DepGraph.from_dict(g.to_dict())
    assert back.to_dict() == g.to_dict()


def test_roundtrip_preserves_ENUMS_not_their_string_values():
    back = DepGraph.from_dict(_rich_graph().to_dict())
    n = back.get("binary:pg_config")
    assert n.type is NodeType.TOOL          # `is`, not `==` -- a str would pass ==
    assert n.state is State.MISSING
    assert n.discovered_by is DiscoveredBy.RUNTIME
    e = back.edges[0]
    assert e.relation is EdgeType.REQUIRES


def test_roundtrip_preserves_build_from_source_FALSE_distinctly_from_NONE():
    """`False` (a known wheel) and `None` (build mode unknown) mean different things to
    `blocks()`: a missing build TOOL blocks the second and not the first. A deserializer that
    collapses them silently flips every wheel's verdict in the cached corpus."""
    back = DepGraph.from_dict(_rich_graph().to_dict())
    assert back.get("pkg:Pillow==10.3").build_from_source is False
    assert back.get("pkg:psycopg2==2.9.12").build_from_source is True
    assert back.get("binary:pg_config").build_from_source is None


def test_roundtrip_preserves_edge_marker_and_data():
    back = DepGraph.from_dict(_rich_graph().to_dict())
    e = back.edges[0]
    assert e.marker == 'python_version >= "3.9"'
    assert e.data.get("hard") is True


def test_roundtrip_survives_JSON():
    import json
    g = _rich_graph()
    back = DepGraph.from_dict(json.loads(json.dumps(g.to_dict())))
    assert back.to_dict() == g.to_dict()


def test_from_dict_covers_every_serialized_field():
    """If someone adds a field to Node.to_dict and forgets from_dict, the cache silently loses it
    and the block grader quietly measures the wrong graph. Fail loudly instead."""
    g = _rich_graph()
    d = g.to_dict()
    back = DepGraph.from_dict(d).to_dict()
    for node in d["nodes"]:
        assert node in back["nodes"], f"field dropped in round-trip: {node}"
