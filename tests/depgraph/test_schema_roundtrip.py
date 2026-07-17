"""to_dict -> from_dict must be lossless. The graph cache depends on it.

The block grader mints real graphs ONCE under Docker, writes them to JSON, and then loads them
back offline forever after. Every measurement it makes is against the deserialized graph. So a
`from_dict` that silently drops a field does not fail loudly -- it hands the grader a DIFFERENT
graph than the one that was minted and every number after that is confidently wrong.

That is why the drift guard below is written the way it is. A round-trip test whose fixture
leaves fields at their defaults cannot catch a dropped read: `from_dict` forgetting
`artifact=d.get("artifact")` still reconstructs `artifact=None`, `to_dict` writes `None` again,
and the dicts compare equal. The guard needs a fixture where EVERY field is non-default -- and a
second guard to make sure it stays that way as fields get added.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import types
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from graph.model import (
    Attempt, DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, Phase, State,
    Strength,
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


# --- the maximal fixture: every field of every serialized dataclass, at a NON-default value ---

_MAX_NODE = "pkg:serde==1.0.219"
_OTHER_NODE = "pkg:serde==0.9.0"


def _maximal_graph() -> DepGraph:
    """Every field non-default, so that DROPPING any single read changes the serialized dict."""
    maximal = Node(
        id=_MAX_NODE, type=NodeType.PACKAGE, name="serde",
        layer=Layer.PIP, discovered_by=DiscoveredBy.AUDIT,
        state=State.SATISFIED,
        version="1.0.219",
        check_command="python -c 'import serde'",
        evidence="ModuleNotFoundError: No module named 'serde'",
        fix_candidates=("pip:serde", "apt:python3-serde"),
        chosen_fix="pip:serde",
        attempts=(Attempt(command="pip install serde==1.0.219", outcome="failed",
                          check="python -c 'import serde'", cycle=2),),
        provenance="phase-A repair overlay",
        discovered_cycle=3,
        certified_cycle=4,
        build_from_source=True,
        artifact="serde-1.0.219-cp312-cp312-linux_aarch64.whl",
        hash="sha256:c0ffee",
        resolved_python="3.12",
        resolved_platform="linux_aarch64",
        exclude_newer="2026-01-01T00:00:00Z",
        ecosystem="rust",                 # non-default => to_dict EMITS the omit-if-default key
        data={"unsat_core": True},
        setup_commands=("cargo build --release",),
        strength=Strength.HARD,
        phase=Phase.GATE,
    )
    other = dataclasses.replace(maximal, id=_OTHER_NODE, version="0.9.0", ecosystem="python")
    tool = Node(id="binary:cc", type=NodeType.TOOL, name="cc",
                layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.PROBE)
    return (DepGraph()
            .with_node(maximal).with_node(other).with_node(tool)
            # requires: carries the marker (a marker on conflicts_with would be meaningless)
            .with_edge(Edge(src=_MAX_NODE, dst="binary:cc", relation=EdgeType.REQUIRES,
                            origin="resolver", marker='sys_platform == "linux"',
                            data={"hard": True}))
            # conflicts_with: the only way to exercise a NON-default relation (Package->Package)
            .with_edge(Edge(src=_MAX_NODE, dst=_OTHER_NODE, relation=EdgeType.CONFLICTS_WITH,
                            origin="resolver", marker=None, data={"bound": "<1.0"})))


def _defaulted_fields(cls) -> dict:
    """Field name -> default, for fields that HAVE one. Required fields are always serialized."""
    out = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            out[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            out[f.name] = f.default_factory()               # type: ignore[misc]
    return out


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
    g = _rich_graph()
    back = DepGraph.from_dict(json.loads(json.dumps(g.to_dict())))
    assert back.to_dict() == g.to_dict()


def test_from_dict_covers_every_serialized_field():
    """The drift guard. Every field of the maximal fixture is non-default, so a `from_dict` that
    forgets to read ANY one of them -- on a Node, an Edge, or an Attempt -- reconstructs a
    different value and this comparison fails. Compares the WHOLE graph dict, edges included:
    guarding only `nodes` would let an Edge field be dropped in silence."""
    g = _maximal_graph()
    d = json.loads(json.dumps(g.to_dict()))       # through JSON: no tuples, no enums
    assert DepGraph.from_dict(d).to_dict() == g.to_dict()


def test_the_maximal_fixture_leaves_NO_field_at_its_default():
    """Guard the guard.

    The test above can only catch a dropped read for a field the fixture actually varies. Add a
    field to Node tomorrow, leave it at its default here, and the drift guard goes quietly blind
    to it -- the exact silent-rot failure it exists to prevent. So: assert that every defaulted
    field of every serialized dataclass is exercised at a non-default value by SOME instance in
    the maximal graph. A new field fails this test until the fixture varies it.
    """
    graph = _maximal_graph()
    instances = {
        Node: graph.nodes,
        Edge: graph.edges,
        Attempt: tuple(a for n in graph.nodes for a in n.attempts),
    }
    for cls, objs in instances.items():
        assert objs, f"maximal graph has no {cls.__name__} to exercise"
        for name, default in _defaulted_fields(cls).items():
            varied = any(getattr(o, name) != default for o in objs)
            assert varied, (
                f"{cls.__name__}.{name} is left at its default ({default!r}) everywhere in the "
                f"maximal fixture, so the round-trip guard CANNOT catch from_dict dropping it. "
                f"Give it a non-default value in _maximal_graph()."
            )


def test_roundtrip_rebuilds_TUPLES_and_MAPPINGS_not_the_lists_JSON_gives_back():
    """JSON has no tuple and no mapping proxy. A list where a tuple belongs makes the frozen
    dataclass unhashable and compares unequal against a freshly built node."""
    back = DepGraph.from_dict(json.loads(json.dumps(_maximal_graph().to_dict())))
    n = back.get(_MAX_NODE)
    assert isinstance(n.fix_candidates, tuple)
    assert isinstance(n.setup_commands, tuple)
    assert isinstance(n.attempts, tuple)
    assert isinstance(n.attempts[0], Attempt)     # not a raw dict
    assert isinstance(n.data, types.MappingProxyType)
    assert isinstance(back.edges[0].data, types.MappingProxyType)
    assert isinstance(back.nodes, tuple) and isinstance(back.edges, tuple)
    assert n.strength is Strength.HARD and n.phase is Phase.GATE
    assert n.ecosystem == "rust"                  # the omit-if-default key survives
    assert back.get(_OTHER_NODE).ecosystem == "python"   # ...and its absence still defaults
    assert n == _maximal_graph().get(_MAX_NODE)   # equal to a freshly BUILT node, field for field


def test_node_has_no_tier_field():
    from graph.model import Node, NodeType, Layer, DiscoveredBy
    n = Node(id="x", type=NodeType.PACKAGE, name="x", layer=Layer.PIP,
             discovered_by=DiscoveredBy.RESOLVER)
    assert not hasattr(n, "tier")
    assert "tier" not in n.to_dict()


def test_module_node_is_legal_requires_src_and_dst():
    from graph.model import (
        DepGraph, Node, Edge, NodeType, EdgeType, Layer, DiscoveredBy,
    )
    imp = Node(id="import:foo", type=NodeType.IMPORT, name="foo",
               layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN)
    mod = Node(id="module:pkg/foo.py", type=NodeType.MODULE, name="pkg/foo.py",
               layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN)
    ext = Node(id="import:numpy", type=NodeType.IMPORT, name="numpy",
               layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN)
    g = DepGraph().with_node(imp).with_node(mod).with_node(ext)
    # import -> module : the config-lane satisfied-by (Module as dst)
    g = g.with_edge(Edge(src="import:foo", dst="module:pkg/foo.py",
                         relation=EdgeType.REQUIRES, origin="scan"))
    # module -> import : the bridge / container (Module as src)
    g = g.with_edge(Edge(src="module:pkg/foo.py", dst="import:numpy",
                         relation=EdgeType.REQUIRES, origin="scan"))
    assert any(e.dst == "module:pkg/foo.py" for e in g.edges)
    assert any(e.src == "module:pkg/foo.py" for e in g.edges)


def test_module_node_type_exists_and_file_is_gone():
    from graph.model import NodeType
    assert NodeType.MODULE.value == "Module"
    assert not hasattr(NodeType, "FILE")
