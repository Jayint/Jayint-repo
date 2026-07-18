"""block_parity: emit-parity sweep + metamorphic properties + reference oracle —
plan Task 6. Every test here is OFFLINE (no Docker, no network, no LLM) — it either
hand-builds a small graph fixture (unit-level, fast TDD) or loads the REAL cached
corpus `graph_cache.py` minted under Docker (integration-level, the actual measurement
this task exists to produce).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.view.graph_context import (  # noqa: E402
    ACTIONABLE, BLOCKED, SATISFIED_OK, UNCERTIFIED, WAITING, verdict,
)
from graph.model import binary_id, package_id  # noqa: E402
from graph.model import (  # noqa: E402
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)

from src.eval.graph_quality.block_parity import (  # noqa: E402
    CAUSE_KNOWN_WHEEL_TOOL, CAUSE_NON_CAPABILITY_DEP, CAUSE_UNATTRIBUTED,
    _emit_parity_for_graph, check_conflict_edge_blocks_never_stars,
    check_known_wheel_ignores_missing_tool, check_satisfying_root_moves_star_up,
    check_softened_edge_child_loses_star, check_unknown_state_never_actionable,
    emit_parity_report, reference_oracle_report, run_metamorphic_suite, verdict_ref,
)
from src.eval.graph_quality.graph_cache import load_graphs  # noqa: E402


def _test_node_only() -> DepGraph:
    return DepGraph().with_node(Node(
        id="test:repo_tests_pass", type=NodeType.TEST, name="repo_tests_pass",
        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL,
    ))


# --------------------------------------------------------------------------- #
# Check 1 — emit parity, unit-level: the checker must not cry wolf on correct
# graphs, AND must correctly wire the three rules it claims to check.
# --------------------------------------------------------------------------- #

def test_emit_parity_is_silent_on_a_well_formed_graph():
    """A graph with a satisfied leaf, a waiting package, an actionable tool, a known
    wheel skipping its tool, and a conflicted package — all built the way production
    would leave them — must produce ZERO divergences."""
    tool_ok = Node(id=binary_id("gcc"), type=NodeType.TOOL, name="gcc", layer=Layer.TOOLCHAIN,
                    discovered_by=DiscoveredBy.RUNTIME, state=State.SATISFIED,
                    chosen_fix="apt:build-essential")
    pkg_waiting = Node(id=package_id("waiting-pkg", "1.0"), type=NodeType.PACKAGE,
                        name="waiting-pkg", layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                        version="1.0", state=State.MISSING, build_from_source=True)
    tool_missing = Node(id=binary_id("missing-tool"), type=NodeType.TOOL, name="missing-tool",
                         layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                         state=State.MISSING, chosen_fix="apt:missing-tool-dev")
    pkg_wheel = Node(id=package_id("wheel-pkg", "1.0"), type=NodeType.PACKAGE, name="wheel-pkg",
                      layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN, version="1.0",
                      state=State.MISSING, build_from_source=False)
    pkg_conflict_a = Node(id=package_id("conflict-a", "1.0"), type=NodeType.PACKAGE,
                           name="conflict-a", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                           version="1.0", state=State.MISSING, build_from_source=False)
    pkg_conflict_b = Node(id=package_id("conflict-b", "2.0"), type=NodeType.PACKAGE,
                           name="conflict-b", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                           version="2.0", state=State.MISSING, build_from_source=False)

    graph = (
        _test_node_only()
        .with_node(tool_ok).with_node(pkg_waiting).with_node(tool_missing)
        .with_node(pkg_wheel).with_node(pkg_conflict_a).with_node(pkg_conflict_b)
        .with_edge(Edge(src=pkg_waiting.id, dst=tool_missing.id, relation=EdgeType.REQUIRES,
                         origin="test", data={"hard": True}))
        .with_edge(Edge(src=pkg_wheel.id, dst=tool_missing.id, relation=EdgeType.REQUIRES,
                         origin="test", data={"hard": True}))
        .with_edge(Edge(src=pkg_conflict_a.id, dst=pkg_conflict_b.id,
                         relation=EdgeType.CONFLICTS_WITH, origin="test"))
    )
    divergences = _emit_parity_for_graph("fixture", graph)
    assert divergences == []


def test_emit_parity_report_is_sliced_by_node_type_and_rule_never_bare():
    graphs = {"fixture": _test_node_only()}
    report = emit_parity_report(graphs)
    assert "by_slice" in report
    assert "total_divergences" in report
    assert report["total_divergences"] == 0
    assert report["by_slice"] == {}


# --------------------------------------------------------------------------- #
# Check 2 — metamorphic properties, unit-level (fast, hand-built base graph).
# --------------------------------------------------------------------------- #

def test_property_satisfying_root_moves_star_up_holds():
    row = check_satisfying_root_moves_star_up(_test_node_only())
    assert row["holds"] is True, row


def test_property_conflict_edge_blocks_never_stars_holds():
    row = check_conflict_edge_blocks_never_stars(_test_node_only())
    assert row["holds"] is True, row


def test_property_known_wheel_ignores_missing_tool_holds():
    row = check_known_wheel_ignores_missing_tool(_test_node_only())
    assert row["holds"] is True, row


def test_property_unknown_state_never_actionable_holds():
    row = check_unknown_state_never_actionable(_test_node_only())
    assert row["holds"] is True, row


def test_property_softened_edge_child_loses_star_holds():
    row = check_softened_edge_child_loses_star(_test_node_only())
    assert row["holds"] is True, row


def test_run_metamorphic_suite_runs_all_five_and_reports_each_by_name():
    rows = run_metamorphic_suite({"fixture": _test_node_only()})
    names = {row["property"] for row in rows}
    assert names == {
        "satisfying_root_moves_star_up",
        "conflict_edge_blocks_never_stars",
        "known_wheel_ignores_missing_tool",
        "unknown_state_never_actionable",
        "softened_edge_child_loses_star",
    }
    assert all(row["holds"] for row in rows), rows


# --------------------------------------------------------------------------- #
# Check 3 — reference oracle, unit-level: the oracle must AGREE with verdict() on
# the population it is faithful to the spec over, and its ONE documented, expected
# blind spot (Package -> Package hard edges) must be mechanically detected, not
# silently absorbed.
# --------------------------------------------------------------------------- #

def test_verdict_ref_agrees_on_a_plain_satisfied_node():
    node = Node(id=package_id("sat", "1.0"), type=NodeType.PACKAGE, name="sat",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="1.0",
                state=State.SATISFIED)
    graph = _test_node_only().with_node(node)
    assert verdict_ref(graph, node) == SATISFIED_OK == verdict(graph, node)


def test_verdict_ref_agrees_on_a_conflicted_node():
    a = Node(id=package_id("a", "1.0"), type=NodeType.PACKAGE, name="a", layer=Layer.PIP,
             discovered_by=DiscoveredBy.RESOLVER, version="1.0", state=State.MISSING,
             build_from_source=False)
    b = Node(id=package_id("b", "1.0"), type=NodeType.PACKAGE, name="b", layer=Layer.PIP,
             discovered_by=DiscoveredBy.RESOLVER, version="1.0", state=State.MISSING,
             build_from_source=False)
    graph = (_test_node_only().with_node(a).with_node(b)
             .with_edge(Edge(src=a.id, dst=b.id, relation=EdgeType.CONFLICTS_WITH, origin="test")))
    assert verdict_ref(graph, a) == BLOCKED == verdict(graph, a)


def test_verdict_ref_agrees_on_an_unknown_node():
    node = Node(id=package_id("unk", "1.0"), type=NodeType.PACKAGE, name="unk", layer=Layer.PIP,
                discovered_by=DiscoveredBy.STATIC_SCAN, version="1.0", state=State.UNKNOWN)
    graph = _test_node_only().with_node(node)
    assert verdict_ref(graph, node) == UNCERTIFIED == verdict(graph, node)


def test_verdict_ref_agrees_on_a_missing_tool_root():
    node = Node(id=binary_id("gcc"), type=NodeType.TOOL, name="gcc", layer=Layer.TOOLCHAIN,
                discovered_by=DiscoveredBy.RUNTIME, state=State.MISSING,
                chosen_fix="apt:build-essential")
    graph = _test_node_only().with_node(node)
    assert verdict_ref(graph, node) == ACTIONABLE == verdict(graph, node)


def test_verdict_ref_agrees_on_a_package_waiting_on_a_missing_syslib():
    """SystemLib blocking is the ONE dependency-type rule §6.3's raw pseudocode DOES
    get right by omission — it treats every hard REQUIRES edge as blocking, and a
    SystemLib dependency really is unconditionally blocking in the real `blocks()`
    too, so this shape must agree."""
    pkg = Node(id=package_id("needs-lib", "1.0"), type=NodeType.PACKAGE, name="needs-lib",
               layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN, version="1.0",
               state=State.MISSING, build_from_source=True)
    lib = Node(id="syslib:libfoo.so.1", type=NodeType.SYSTEM_LIB, name="libfoo",
               layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
               chosen_fix="apt:libfoo-dev")
    graph = (_test_node_only().with_node(pkg).with_node(lib)
             .with_edge(Edge(src=pkg.id, dst=lib.id, relation=EdgeType.REQUIRES, origin="test",
                              data={"hard": True})))
    assert verdict_ref(graph, pkg) == WAITING == verdict(graph, pkg)


def test_verdict_ref_KNOWN_DIVERGENCE_non_capability_dependency():
    """🔴 DOCUMENTED divergence #1: the shipped `blocks()` only ever blocks on a
    SystemLib or a Tool dependency. A Package dependency (pip resolves its own tree in
    one shot; emit does not gate on these either) is waved through — and so, by the
    same bare `return False` tail, is an Import / Project / Runtime dependency. Design
    spec §6.3's own pseudocode has no such narrowing: it blocks on ANY hard, marker-true
    REQUIRES edge to an unsatisfied dep. `verdict_ref` says WAITING; the real `verdict()`
    says ACTIONABLE. Surfaced honestly, never absorbed by copying the refinement into
    the oracle."""
    parent = Node(id=package_id("needs-pkg", "1.0"), type=NodeType.PACKAGE, name="needs-pkg",
                  layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN, version="1.0",
                  state=State.MISSING, build_from_source=True)
    child = Node(id=package_id("dep-pkg", "1.0"), type=NodeType.PACKAGE, name="dep-pkg",
                 layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="1.0",
                 state=State.MISSING, build_from_source=False)
    graph = (_test_node_only().with_node(parent).with_node(child)
             .with_edge(Edge(src=parent.id, dst=child.id, relation=EdgeType.REQUIRES,
                              origin="test", data={"hard": True})))
    assert verdict_ref(graph, parent) == WAITING
    assert verdict(graph, parent) == ACTIONABLE
    assert verdict_ref(graph, parent) != verdict(graph, parent)


def test_the_TEST_GOAL_NODE_is_ACTIONABLE_while_its_project_is_UNCERTIFIED():
    """🔴 THE REAL-CORPUS FACE OF DIVERGENCE #1, and the reason it is not merely
    academic. In EVERY graph in the cached corpus the TEST goal node hard-requires the
    repo's own `project:` node, which construction leaves UNKNOWN (no check_command).
    The shipped `blocks()` does not block on a Project dependency, so the goal node
    itself comes out ACTIONABLE — the graph's own definition of "the agent can act
    HERE, nothing is missing beneath it" — while the thing beneath it is uncertified.
    Reported, NOT fixed (`src/python_deps/` is the code under test)."""
    project = Node(id="project:fixture", type=NodeType.PROJECT, name="fixture",
                   layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                   state=State.UNKNOWN, data={"installable": True})
    test_node = Node(id="test:repo_tests_pass", type=NodeType.TEST, name="repo_tests_pass",
                     layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.MISSING)
    graph = (DepGraph().with_node(test_node).with_node(project)
             .with_edge(Edge(src=test_node.id, dst=project.id, relation=EdgeType.REQUIRES,
                              origin="project", data={"hard": True})))
    assert verdict(graph, project) == UNCERTIFIED     # the dep is NOT certified...
    assert verdict(graph, test_node) == ACTIONABLE    # ...yet the goal says "act here"
    assert verdict_ref(graph, test_node) == WAITING   # the spec's own definition disagrees


def test_verdict_ref_KNOWN_DIVERGENCE_known_wheel_tool_exemption():
    """🔴 DOCUMENTED divergence #2: a known wheel's missing Tool dependency. Real
    `blocks()` exempts it (0d3542c — a wheel dlopens a .so but never invokes a
    compiler, and `emit._toolchain_ready` agrees); §6.3's raw pseudocode never checks
    `build_from_source` at all."""
    pkg = Node(id=package_id("wheel-pkg", "1.0"), type=NodeType.PACKAGE, name="wheel-pkg",
               layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN, version="1.0",
               state=State.MISSING, build_from_source=False)
    tool = Node(id=binary_id("gcc"), type=NodeType.TOOL, name="gcc", layer=Layer.TOOLCHAIN,
                discovered_by=DiscoveredBy.RUNTIME, state=State.MISSING,
                chosen_fix="apt:build-essential")
    graph = (_test_node_only().with_node(pkg).with_node(tool)
             .with_edge(Edge(src=pkg.id, dst=tool.id, relation=EdgeType.REQUIRES,
                              origin="test", data={"hard": True})))
    assert verdict_ref(graph, pkg) == WAITING
    assert verdict(graph, pkg) == ACTIONABLE
    assert verdict_ref(graph, pkg) != verdict(graph, pkg)


def test_reference_oracle_report_is_sliced_by_cause_and_type_never_bare():
    graphs = {"fixture": _test_node_only()}
    report = reference_oracle_report(graphs)
    assert report["total_disagreements"] == 0
    assert report["by_cause"] == {}
    assert report["by_node_type"] == {}


# --------------------------------------------------------------------------- #
# Integration — the REAL measurement, over the REAL cached corpus (T5). This is
# what the commit message reports.
# --------------------------------------------------------------------------- #

def test_emit_parity_over_the_real_cached_corpus_is_zero():
    """🔴 THE PASS BAR. If this fails, that is a genuine bug in `src/python_deps/`
    (off-limits to fix here) -- report it, do not narrow the check."""
    graphs = load_graphs()
    assert graphs, "no cached graphs -- run graph_cache.mint() and commit graphs/*.json first"
    report = emit_parity_report(graphs)
    assert report["total_divergences"] == 0, report["by_slice"]


def test_metamorphic_properties_all_hold_over_the_real_cached_corpus():
    graphs = load_graphs()
    assert graphs
    rows = run_metamorphic_suite(graphs)
    failed = [r["property"] for r in rows if not r["holds"]]
    assert not failed, rows


def test_reference_oracle_over_the_real_cached_corpus_has_only_documented_causes():
    """Disagreements ARE expected here — the shipped `blocks()` is strictly more
    permissive than the spec that defines it (see the module docstring). So this does
    not assert zero. It asserts something stronger and more useful: every single
    disagreement is one of the two MECHANICALLY attributed causes, and NONE is
    unattributed. An unattributed one would mean a THIRD, undiscovered divergence class
    between `verdict()` and its own written definition — which is exactly the class of
    bug this oracle exists to catch, and it must fail loudly, not average away."""
    graphs = load_graphs()
    assert graphs
    report = reference_oracle_report(graphs)
    assert report["by_cause"].get(CAUSE_UNATTRIBUTED, 0) == 0, (
        "an UNATTRIBUTED disagreement means a third, undiagnosed divergence class exists "
        f"between verdict() and verdict_ref(): {report['sample']}"
    )
    assert set(report["by_cause"]) <= {CAUSE_NON_CAPABILITY_DEP, CAUSE_KNOWN_WHEEL_TOOL}
