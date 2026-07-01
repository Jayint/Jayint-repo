import dataclasses

from python_deps.depgraph.emit import MAX_EMIT_ATTEMPTS, partition
from python_deps.depgraph.schema import (
    Attempt, DepGraph, Edge, EdgeType, Layer, Node, NodeType, State, DiscoveredBy,
)


def _pkg(name, *, state=State.MISSING, version="1.0", bfs=None):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=state, version=version,
                check_command=f'python -c "import {name}"', build_from_source=bfs)


def _tool(name, *, state=State.MISSING, apt="build-essential"):
    return Node(id=f"tool:{name}", type=NodeType.TOOL, name=name, layer=Layer.TOOLCHAIN,
                discovered_by=DiscoveredBy.PROBE, state=state,
                check_command=f"command -v {name}",
                fix_candidates=(f"apt:{apt}",), chosen_fix=f"apt:{apt}")


def test_partition_buckets_basic():
    g = DepGraph(nodes=(
        _pkg("flask", state=State.SATISFIED),     # certified
        _pkg("numpy"),                            # emittable (resolved, has version)
        _pkg("ghost", version=None),              # frontier (unresolved)
        _tool("gcc"),                             # emittable (single apt fix)
    ))
    p = partition(g)
    assert {n.name for n in p.certified} == {"flask"}
    assert {n.name for n in p.emittable} == {"numpy", "gcc"}
    assert {n.name for n in p.frontier} == {"ghost"}


def test_partition_conflict_pair_is_frontier():
    g = DepGraph(
        nodes=(_pkg("fastavro"), _pkg("avro")),
        edges=(Edge(src="pkg:fastavro", dst="pkg:avro", relation=EdgeType.CONFLICTS_WITH),),
    )
    p = partition(g)
    assert {n.name for n in p.frontier} == {"fastavro", "avro"}
    assert p.emittable == ()


def test_partition_build_from_source_waits_for_toolchain():
    lxml = _pkg("lxml", bfs=True)
    libxml = Node(id="syslib:libxml2", type=NodeType.SYSTEM_LIB, name="libxml2.so.2",
                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                  check_command="ldconfig -p | grep libxml2",
                  fix_candidates=("apt:libxml2-dev",), chosen_fix="apt:libxml2-dev")
    g = DepGraph(
        nodes=(lxml, libxml),
        edges=(Edge(src="pkg:lxml", dst="syslib:libxml2", relation=EdgeType.REQUIRES),),
    )
    # toolchain MISSING -> lxml is frontier, libxml is emittable
    p = partition(g)
    assert {n.name for n in p.emittable} == {"libxml2.so.2"}
    assert {n.name for n in p.frontier} == {"lxml"}
    # toolchain SATISFIED -> lxml becomes emittable
    g2 = g.with_node(libxml.with_state(State.SATISFIED))
    p2 = partition(g2)
    assert "lxml" in {n.name for n in p2.emittable}


def test_partition_ignores_non_installable_types():
    g = DepGraph(nodes=(
        Node(id="test:goal", type=NodeType.TEST, name="repo_tests_pass", layer=Layer.TESTS,
             discovered_by=DiscoveredBy.GOAL, state=State.MISSING),
        Node(id="imp:foo", type=NodeType.IMPORT, name="foo", layer=Layer.NAMING,
             discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING),
    ))
    p = partition(g)
    assert p.certified == () and p.emittable == () and p.frontier == ()


def test_partition_unversioned_package_is_frontier():
    # R6: version=None means unresolved -> LLM's call -> must land in frontier, not emittable
    g = DepGraph(nodes=(_pkg("requests", version=None),))
    p = partition(g)
    assert {n.name for n in p.frontier} == {"requests"}
    assert p.emittable == ()


def test_wheel_package_waits_for_its_runtime_syslib():
    # A wheel (build_from_source=False) still dlopens native libs at import time,
    # so it must wait for a MISSING required SystemLib just like a source build.
    opencv = _pkg("opencv-python", version="4.9.0.80", bfs=False)
    libgl = Node(id="syslib:libGL.so.1", type=NodeType.SYSTEM_LIB, name="libGL.so.1",
                 layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                 check_command="ldconfig -p | grep libGL.so.1")
    g = DepGraph(
        nodes=(opencv, libgl),
        edges=(Edge(src="pkg:opencv-python", dst="syslib:libGL.so.1", relation=EdgeType.REQUIRES),),
    )
    part = partition(g)
    emittable_ids = {n.id for n in part.emittable}
    assert "pkg:opencv-python" not in emittable_ids  # BUG today: it IS emittable


def test_soft_syslib_edge_does_not_block_package_emission():
    # An advisory/LLM-proposed SOFT Package -> SystemLib hint (Edge.data["hard"]
    # is False) must never block emission (invariant #10; mirrors
    # schedule._dependencies_satisfied / test_soft_edge_seam.py). Only HARD
    # requires edges gate _toolchain_ready.
    numpy = _pkg("numpy", version="1.26.0", bfs=False)
    libfoo = Node(id="syslib:libfoo.so.1", type=NodeType.SYSTEM_LIB, name="libfoo.so.1",
                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                  check_command="ldconfig -p | grep libfoo.so.1")
    g = DepGraph(
        nodes=(numpy, libfoo),
        edges=(Edge(src="pkg:numpy", dst="syslib:libfoo.so.1", relation=EdgeType.REQUIRES,
                     data={"hard": False}),),
    )
    part = partition(g)
    assert "pkg:numpy" in {n.id for n in part.emittable}


def test_partition_demotes_repeatedly_failed_emit_to_frontier():
    # Fix #3: a resolved package that has failed to emit MAX_EMIT_ATTEMPTS times must
    # stop being emittable and escalate to the frontier (no infinite re-emit loop).
    fails = tuple(
        Attempt(command="python3 -m pip install --break-system-packages numpy==1.0",
                outcome="failed", check="emit", cycle=c)
        for c in range(MAX_EMIT_ATTEMPTS)
    )
    looped = dataclasses.replace(_pkg("numpy"), attempts=fails)
    # one short of the cap is still emittable; at the cap it is demoted
    nearly = dataclasses.replace(_pkg("numpy"), attempts=fails[:1])

    p = partition(DepGraph(nodes=(looped,)))
    assert {n.name for n in p.frontier} == {"numpy"}
    assert p.emittable == ()

    if MAX_EMIT_ATTEMPTS > 1:
        p2 = partition(DepGraph(nodes=(nearly,)))
        assert {n.name for n in p2.emittable} == {"numpy"}
