"""Unit tests for the patch-localization grader (plan Task 4, design spec §4).

Grades the RENDER, not an agent: is the ★ (ACTIONABLE marker) `render_graph_context`
actually emits on the RIGHT node? root-hit@1 alone is a DISHONEST metric -- a graph
that stars everything scores 100% on it -- so star precision is asserted alongside
root_hit in every test here, exactly as the task demands.

The three tests the plan names verbatim come first. The rest exercise the 5 real
`PILOT_INJECTIONS` end to end through the actual `render_graph_context` pipeline (no
mocking of the render itself anywhere in this file).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from python_deps.depgraph.ids import TEST_NODE_ID, binary_id, package_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)
from src.eval.graph_quality.patch_localize import (
    build_report, grade, grade_stars, scenario_for, stars,
)
from src.eval.graph_repair_ablation.oracle import FAILURE_CLASSES, PILOT_INJECTIONS
from src.react_repair.loop import RunResult
from src.react_repair.pytest_summary import Cause


# --------------------------------------------------------------------------- #
# The three tests the plan names (Task 4 Step 1) -- written first, watched to fail.
# --------------------------------------------------------------------------- #

def test_star_precision_punishes_a_graph_that_stars_everything():
    """root-hit@1 alone is a DISHONEST metric: a graph that stars 12 nodes and happens
    to include the right one scores 100%. Precision is what makes the metric mean
    something -- and THAT is the number we report."""
    graph = DepGraph().with_node(Node(
        id="binary:pg_config", type=NodeType.TOOL, name="pg_config", layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.RUNTIME, state=State.MISSING,
        chosen_fix="apt:libpq-dev", check_command="command -v pg_config",
    ))
    star_ids = frozenset({"binary:pg_config", *(f"pkg:noise{i}" for i in range(11))})
    score = grade_stars(star_ids, "apt:libpq-dev", graph)
    assert score.root_hit is True
    assert score.n_stars == 12
    assert score.star_precision < 0.1


def test_a_conflicted_root_is_MISLOCALIZED_if_starred():
    """`emit._is_emittable` already refuses to emit a node sitting on a CONFLICTS_WITH
    edge -- no install ever works for it. A ★ there tells the agent to `pip install X`
    forever. This is a regression guard on the GRADING FORMULA itself: even though the
    real render never actually produces this shape (see
    `test_the_real_render_never_stars_the_conflicted_urllib3_pin` below -- verdict()
    renders such a node ✖, never ★), `grade_stars` must still catch it if it ever did."""
    graph = (DepGraph()
             .with_node(Node(id="pkg:urllib3==1.20", type=NodeType.PACKAGE, name="urllib3",
                             layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                             version="1.20", state=State.MISSING))
             .with_node(Node(id="pkg:requests==2.31.0", type=NodeType.PACKAGE, name="requests",
                             layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                             version="2.31.0", state=State.SATISFIED))
             .with_edge(Edge(src="pkg:urllib3==1.20", dst="pkg:requests==2.31.0",
                             relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    score = grade_stars(frozenset({"pkg:urllib3==1.20"}), "urllib3", graph)
    assert score.mislocalized is True


def test_the_COLLAPSE_shows_exactly_one_star_when_two_failures_share_a_root():
    """psycopg2 AND asyncpg both need pg_config: the render must converge on ONE ★
    record, not two. This is the arm's headline structural claim (design spec §4.3
    "collapse rate") and NOTHING else in the repo measures it. It is also the
    regression guard for a real, already-shipped bug (`b5a9f65`, "TOOL node fracture"):
    minting `tool:pg_config` and `binary:pg_config` as two separate nodes would destroy
    exactly this property."""
    graph = (DepGraph()
             .with_node(Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                             layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL))
             .with_node(Node(id=package_id("psycopg2", "2.9.12"), type=NodeType.PACKAGE,
                             name="psycopg2", layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                             version="2.9.12", state=State.MISSING, build_from_source=True))
             .with_node(Node(id=package_id("asyncpg", "0.29.0"), type=NodeType.PACKAGE,
                             name="asyncpg", layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                             version="0.29.0", state=State.MISSING, build_from_source=True))
             .with_node(Node(id=binary_id("pg_config"), type=NodeType.TOOL, name="pg_config",
                             layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                             state=State.MISSING, check_command="command -v pg_config",
                             chosen_fix="apt:libpq-dev", fix_candidates=("apt:libpq-dev",),
                             evidence="Error: pg_config executable not found."))
             .with_edge(Edge(src=package_id("psycopg2", "2.9.12"), dst=binary_id("pg_config"),
                             relation=EdgeType.REQUIRES, origin="resolver", data={"hard": True}))
             .with_edge(Edge(src=package_id("asyncpg", "0.29.0"), dst=binary_id("pg_config"),
                             relation=EdgeType.REQUIRES, origin="resolver", data={"hard": True})))
    causes = (
        Cause(exc="ModuleNotFoundError", detail="No module named 'psycopg2'",
              count=4, outcome="ERROR", module="tests/test_psycopg2.py", phase="collect"),
        Cause(exc="ModuleNotFoundError", detail="No module named 'asyncpg'",
              count=3, outcome="ERROR", module="tests/test_asyncpg.py", phase="collect"),
    )
    star_ids = stars(graph, RunResult(ok=True), causes)
    assert star_ids == frozenset({"binary:pg_config"})


# --------------------------------------------------------------------------- #
# The 5 real PILOT_INJECTIONS, end to end through the REAL render_graph_context.
# 🔴 n=5 -- ONE per FAILURE_CLASSES member. A smoke test, never a rate.
# --------------------------------------------------------------------------- #

def test_there_are_exactly_5_injections_one_per_failure_class():
    assert len(FAILURE_CLASSES) == 5
    assert len(PILOT_INJECTIONS) == 5
    assert {i.failure_class for i in PILOT_INJECTIONS} == FAILURE_CLASSES


def test_build_report_never_hides_the_n_or_formats_a_rate():
    rows = build_report()
    assert len(rows) == 5
    for row in rows:
        # star_precision is a plain float in [0, 1] -- never pre-formatted as "NN%"
        # (a formatted string here is how "5 cells" quietly turns into "a rate").
        assert isinstance(row["star_precision"], float)
        assert 0.0 <= row["star_precision"] <= 1.0
        assert isinstance(row["n_stars"], int)


def test_syslib_missing_pygraphviz_hits_the_libgraphviz_dev_root_with_full_precision():
    inj = next(i for i in PILOT_INJECTIONS if i.injection_id == "syslib_pygraphviz")
    graph, result, causes = scenario_for(inj)
    score = grade(inj, graph, result, causes)
    assert score.root_hit is True
    assert score.n_stars == 1
    assert score.star_precision == 1.0
    assert score.mislocalized is False


def test_compiler_absent_pyzmq_hits_the_build_essential_root_with_full_precision():
    inj = next(i for i in PILOT_INJECTIONS if i.injection_id == "compiler_pyzmq")
    graph, result, causes = scenario_for(inj)
    score = grade(inj, graph, result, causes)
    assert score.root_hit is True
    assert score.n_stars == 1
    assert score.star_precision == 1.0
    assert score.mislocalized is False


def test_tool_absent_semrel_hits_the_git_root_with_full_precision():
    inj = next(i for i in PILOT_INJECTIONS if i.injection_id == "tool_semrel")
    graph, result, causes = scenario_for(inj)
    score = grade(inj, graph, result, causes)
    assert score.root_hit is True
    assert score.n_stars == 1
    assert score.star_precision == 1.0
    assert score.mislocalized is False


def test_the_real_render_never_stars_the_conflicted_urllib3_pin():
    """VERSION_CONFLICT (repin kind): root-hit is trivially 0 here, and it is a
    STRUCTURAL fact, not a render defect -- verdict()'s own invariant ("a CONFLICTS_WITH
    node is BLOCKED, never ACTIONABLE") means the render can never put a ★ on a node
    whose only correct fix is a repin. An agent relying solely on "★ = what to fix" gets
    ZERO signal for this failure class and must fall back to reading the raw log."""
    inj = next(i for i in PILOT_INJECTIONS if i.injection_id == "conflict_requests")
    graph, result, causes = scenario_for(inj)
    score = grade(inj, graph, result, causes)
    assert score.n_stars == 0
    assert score.root_hit is False
    assert score.star_precision == 0.0
    assert score.mislocalized is False   # nothing was starred, so nothing to mislocalize


def test_overinclude_dotenv_produces_no_star_the_phantom_package_has_no_node():
    """OVERINCLUDE (drop kind): `inject.apply_injection`'s `add_install_pkg` op appends
    the offending line straight to the ALREADY-RENDERED script, so this package was
    never part of the graph construction ran against. `owner_node_for_command` finds no
    owner, and the render's own graceful-degradation path fires instead of fabricating a
    node. root-hit is 0 for a genuinely different reason than the conflict case above --
    both are honestly reported, never conflated into one "0" that hides why."""
    inj = next(i for i in PILOT_INJECTIONS if i.injection_id == "overinclude_dotenv")
    graph, result, causes = scenario_for(inj)
    score = grade(inj, graph, result, causes)
    assert score.n_stars == 0
    assert score.root_hit is False
    assert score.star_precision == 0.0


def test_root_hit_at_1_across_the_5_real_injections_is_3_of_5_by_construction():
    """The headline number for this smoke test, spelled out so a change to any one
    scenario is caught here first: 3/5 root-hit -- the two misses (VERSION_CONFLICT,
    OVERINCLUDE) are both STRUCTURAL non-installs, not render bugs. 🔴 3/5 is NOT a
    rate; n=5 is a smoke test, one cell per FAILURE_CLASSES member."""
    rows = build_report()
    assert sum(1 for r in rows if r["root_hit"]) == 3
    hits_by_class = {r["failure_class"]: r["root_hit"] for r in rows}
    assert hits_by_class["SYSLIB_MISSING"] is True
    assert hits_by_class["COMPILER_ABSENT"] is True
    assert hits_by_class["TOOL_ABSENT"] is True
    assert hits_by_class["VERSION_CONFLICT"] is False
    assert hits_by_class["OVERINCLUDE"] is False
