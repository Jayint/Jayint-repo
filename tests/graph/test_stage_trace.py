"""Tests for the per-stage construction tracer (graph.debug.stage_trace).

The tracer wraps each numbered construction stage where it is LOOKED UP (a
module-level name in graph.python.pipeline), captures each stage's return value
in call order, and diffs successive graph snapshots so a real run shows exactly
what each boundary added / flipped. These tests exercise the pure core (snapshot,
diff) and the wrap/capture/restore mechanism with a fake module -- no Docker.
"""

from __future__ import annotations

import sys
import types

from graph.model import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from graph.debug.stage_trace import (
    StageRecord,
    StageTracer,
    diff_snapshots,
    snapshot_graph,
)


def _pkg(node_id: str, name: str, state: State) -> Node:
    return Node(
        id=node_id,
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=state,
    )


def _import(node_id: str, name: str, *, unresolved: bool) -> Node:
    return Node(
        id=node_id,
        type=NodeType.IMPORT,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.UNKNOWN,
        data={"unresolved": unresolved},
    )


def test_diff_reports_added_state_and_unresolved_changes():
    prev = DepGraph(
        nodes=(
            _import("import:flask", "flask", unresolved=True),
            _pkg("pkg:flask", "flask", State.MISSING),
        )
    )
    curr = DepGraph(
        nodes=(
            _import("import:flask", "flask", unresolved=False),   # flag flip
            _pkg("pkg:flask", "flask", State.SATISFIED),          # state flip
            _pkg("syslib:libpq", "libpq", State.MISSING),         # added
        )
    )

    d = diff_snapshots(snapshot_graph(prev), snapshot_graph(curr))

    assert [n["id"] for n in d["added"]] == ["syslib:libpq"]
    assert d["removed"] == []
    assert d["state_changes"] == [
        {"id": "pkg:flask", "from": "missing", "to": "satisfied"}
    ]
    assert d["unresolved_changes"] == [
        {"id": "import:flask", "from": True, "to": False}
    ]


def test_diff_reports_added_edges():
    a = _pkg("pkg:a", "a", State.MISSING)
    b = _pkg("pkg:b", "b", State.MISSING)
    prev = DepGraph(nodes=(a, b))
    curr = DepGraph(
        nodes=(a, b),
        edges=(Edge(src="pkg:a", dst="pkg:b", relation=EdgeType.REQUIRES),),
    )

    d = diff_snapshots(snapshot_graph(prev), snapshot_graph(curr))

    assert d["added_edges"] == [["pkg:a", "pkg:b", "requires"]]


def test_tracer_captures_returns_in_call_order_and_restores_originals():
    mod = types.ModuleType("fake_pipeline_mod")

    def make_roots():
        return [(None, "flask"), (None, "requests")]

    def make_graph():
        return DepGraph(nodes=(_pkg("pkg:flask", "flask", State.MISSING),))

    mod.make_roots = make_roots
    mod.make_graph = make_graph
    sys.modules["fake_pipeline_mod"] = mod
    try:
        targets = [
            ("fake_pipeline_mod", "make_roots", "R declared-roots"),
            ("fake_pipeline_mod", "make_graph", "G scan"),
        ]
        with StageTracer(targets=targets) as tracer:
            # Simulate the pipeline calling module-level names, roots THEN graph.
            roots = mod.make_roots()
            graph = mod.make_graph()

        # originals restored on exit
        assert mod.make_roots is make_roots
        assert mod.make_graph is make_graph
        # captured in call order
        assert [r.label for r in tracer.records] == ["R declared-roots", "G scan"]
        assert tracer.records[0].kind == "roots"
        assert tracer.records[1].kind == "graph"
        # return values pass through UNCHANGED (wrapper is transparent)
        assert roots == [(None, "flask"), (None, "requests")]
        assert isinstance(graph, DepGraph)
        assert tracer.records[0].value == [(None, "flask"), (None, "requests")]
    finally:
        del sys.modules["fake_pipeline_mod"]


def test_tracer_missing_target_fails_loud():
    # A drifted/renamed stage name must fail loudly, not silently capture less.
    import pytest

    with pytest.raises((AttributeError, ValueError)):
        with StageTracer(targets=[("graph.python.pipeline", "no_such_stage", "X")]):
            pass


def test_default_targets_all_resolve_against_the_real_pipeline():
    # Every stage name the tracer wants to wrap must exist on the real modules,
    # and all must be restored on exit -- this is the drift guard for my
    # hand-typed DEFAULT_TARGETS against pipeline.py / orchestrate.py.
    import importlib

    from graph.debug.stage_trace import DEFAULT_TARGETS

    originals = {
        (m, a): getattr(importlib.import_module(m), a)
        for (m, a, _label) in DEFAULT_TARGETS
    }
    with StageTracer() as tracer:  # raises AttributeError if any name drifted
        for (m, a, _label) in DEFAULT_TARGETS:
            wrapped = getattr(importlib.import_module(m), a)
            assert wrapped is not originals[(m, a)]  # actually wrapped
    for (m, a, _label) in DEFAULT_TARGETS:
        assert getattr(importlib.import_module(m), a) is originals[(m, a)]
    assert len(DEFAULT_TARGETS) == 17  # +2: seed_runtime_tools (P1), add_ctypes_runtime_libs (P2)


def test_build_trace_diffs_each_graph_against_previous():
    g1 = DepGraph(nodes=(_pkg("pkg:a", "a", State.MISSING),))
    g2 = DepGraph(
        nodes=(
            _pkg("pkg:a", "a", State.MISSING),
            _pkg("pkg:b", "b", State.MISSING),
        )
    )
    tracer = StageTracer(targets=[])
    tracer.records = [
        StageRecord(label="s1", fn="f1", kind="graph", value=g1),
        StageRecord(label="s2", fn="f2", kind="graph", value=g2),
    ]

    trace = tracer.build_trace()

    assert [t["label"] for t in trace] == ["s1", "s2"]
    # first graph diffs against the empty baseline -> pkg:a added
    assert [n["id"] for n in trace[0]["diff"]["added"]] == ["pkg:a"]
    # second graph diffs against the first -> only pkg:b added
    assert [n["id"] for n in trace[1]["diff"]["added"]] == ["pkg:b"]
