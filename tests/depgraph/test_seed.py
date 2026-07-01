"""Predicted native-node seeding (``seed.py``).

After resolve, packages with a known native footprint (a
``tables.PACKAGE_TO_SYSTEM_DEPS`` hit) or a from-source build risk get
*predicted* ``Tool`` / ``SystemLib`` nodes BEFORE the build runs.  Predictions
are resolver-origin, ``UNKNOWN`` state, carry a non-empty apt fix candidate, and
hang off the owning ``Package`` by a ``requires`` edge.  All pure (no executor).
"""

from __future__ import annotations

from python_deps.depgraph.ids import package_id, syslib_id, tool_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from python_deps.depgraph.seed import seed_predicted_native


def _package(name: str, version: str, *, build_from_source=None) -> Node:
    return Node(
        id=package_id(name, version),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        version=version,
        check_command=f"python -m pip show {name}",
        build_from_source=build_from_source,
    )


def test_seed_predicts_runtime_syslib_for_opencv():
    pkg = _package("opencv-python", "4.9.0.80")
    graph = DepGraph().with_node(pkg)

    out = seed_predicted_native(graph)

    # Canonical rule: the SONAME is the identity for a SystemLib node (it is the
    # observable ldd/import_probe key); the apt package lives in chosen_fix, not
    # the id — so seed and the later probe stages land on the SAME node.
    lib = out.get(syslib_id("libGL.so.1"))
    assert lib is not None
    assert lib.type is NodeType.SYSTEM_LIB
    assert lib.layer is Layer.SYSTEM
    assert lib.discovered_by is DiscoveredBy.RESOLVER  # a prediction
    assert lib.state is State.UNKNOWN
    assert lib.name == "libGL.so.1"
    assert lib.fix_candidates == ("apt:libgl1",)
    assert lib.chosen_fix == "apt:libgl1"
    # and the second runtime lib in the chain
    assert out.get(syslib_id("libglib-2.0.so.0")) is not None
    # owning package requires the predicted lib
    deps = {d.id for d in out.requires_of(pkg.id)}
    assert syslib_id("libGL.so.1") in deps
    assert syslib_id("libglib-2.0.so.0") in deps


def test_seed_predicts_build_tool_for_psycopg2():
    pkg = _package("psycopg2", "2.9.9")
    graph = DepGraph().with_node(pkg)

    out = seed_predicted_native(graph)

    tool = out.get(tool_id("libpq-dev"))
    assert tool is not None
    assert tool.type is NodeType.TOOL  # -dev package -> build toolchain need
    assert tool.layer is Layer.TOOLCHAIN
    assert tool.discovered_by is DiscoveredBy.RESOLVER
    assert tool.state is State.UNKNOWN
    assert tool.fix_candidates == ("apt:libpq-dev",)
    deps = {d.id for d in out.requires_of(pkg.id)}
    assert tool_id("libpq-dev") in deps


def test_seed_predicts_generic_toolchain_for_from_source_only():
    # A from-source package with no table hit still predicts a compiler toolchain.
    pkg = _package("obscurelib", "1.0.0", build_from_source=True)
    graph = DepGraph().with_node(pkg)

    out = seed_predicted_native(graph)

    tool = out.get(tool_id("build-essential"))
    assert tool is not None
    assert tool.type is NodeType.TOOL
    assert tool.fix_candidates == ("apt:build-essential",)
    deps = {d.id for d in out.requires_of(pkg.id)}
    assert tool_id("build-essential") in deps


def test_seed_no_prediction_for_pure_python_package():
    pkg = _package("requests", "2.31.0")  # no table hit, not from-source
    graph = DepGraph().with_node(pkg)

    out = seed_predicted_native(graph)

    assert [n for n in out.nodes if n.type in (NodeType.TOOL, NodeType.SYSTEM_LIB)] == []


def test_seed_dedupes_shared_predicted_node_across_packages():
    a = _package("opencv-python", "4.9.0.80")
    b = _package("opencv-python-headless", "4.9.0.80")
    graph = DepGraph().with_node(a).with_node(b)

    out = seed_predicted_native(graph)

    # opencv-python-headless is not in the table; only opencv-python predicts.
    libs = [n for n in out.nodes if n.id == syslib_id("libGL.so.1")]
    assert len(libs) == 1


def test_seed_predicted_edges_are_resolver_origin():
    pkg = _package("opencv-python", "4.9.0.80")
    graph = DepGraph().with_node(pkg)

    out = seed_predicted_native(graph)

    pred_edges = [
        e
        for e in out.edges
        if e.dst == syslib_id("libGL.so.1") and e.relation is EdgeType.REQUIRES
    ]
    assert pred_edges and all(e.origin == "resolver" for e in pred_edges)


def test_seed_returns_new_graph_originals_unchanged():
    pkg = _package("opencv-python", "4.9.0.80")
    graph = DepGraph().with_node(pkg)

    out = seed_predicted_native(graph)

    assert out is not graph
    assert graph.get(syslib_id("libGL.so.1")) is None
