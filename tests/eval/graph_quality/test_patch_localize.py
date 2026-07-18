"""Unit tests for the patch-localization grader (plan Task 4, design spec §4).

🔴 REWORK: the version of this file committed at 3ee51a6 tested a CIRCULAR grader --
every scenario hand-built the exact `Node`/`Edge` shape (including `chosen_fix`) the
grading formula was then checked against, so `root_hit=3/5` measured the test
author's own fixtures, not the graph. See `patch_localize.py`'s module docstring for
the full account of what a gpt-5.6-terra adversarial review found and what actually
happens once the graph comes from the REAL `enrich`/`expand_discovery`/`certify_only`/
`render_graph_context` chain instead.

None of the tests below hand-build a capability node, a `chosen_fix`, or an edge to a
"root" any more. Each of the 5 real `PILOT_INJECTIONS` is graded on whatever
`patch_localize.scenario_for` produces by actually RUNNING that chain against a seed
containing only declared packages + the TEST node, and real (for two of them,
empirically-reproduced) error text. The one deliberate exception --
`conflict_requests`, whose CONFLICTS_WITH edge only a live, networked resolver could
produce -- is documented at its own test and at `_scenario_conflict_requests`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.graph_context import render_graph_context
from graph.ids import TEST_NODE_ID, binary_id, package_id, syslib_id, tool_id
from graph.model import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)
from src.eval.graph_quality.patch_localize import (
    _base_graph, _declared_pkg, _replay_turn, build_report, grade, grade_stars,
    scenario_for, stars,
)
from src.eval.graph_repair_ablation.oracle import FAILURE_CLASSES, PILOT_INJECTIONS
from src.agent.loop import RunResult
from src.agent.observe import summarize


def _inj(injection_id: str):
    return next(i for i in PILOT_INJECTIONS if i.injection_id == injection_id)


# --------------------------------------------------------------------------- #
# Sanity on the reused oracle -- unchanged by the rework.
# --------------------------------------------------------------------------- #

def test_there_are_exactly_5_injections_one_per_failure_class():
    assert len(FAILURE_CLASSES) == 5
    assert len(PILOT_INJECTIONS) == 5
    assert {i.failure_class for i in PILOT_INJECTIONS} == FAILURE_CLASSES


def test_build_report_never_hides_the_n_or_formats_a_rate():
    rows = build_report()
    assert len(rows) == 5
    for row in rows:
        # star_precision is a plain float in [0, 1] -- never pre-formatted as "NN%".
        assert isinstance(row["star_precision"], float)
        assert 0.0 <= row["star_precision"] <= 1.0
        assert isinstance(row["n_stars"], int)
        assert isinstance(row["not_applicable"], bool)
        assert isinstance(row["unresolved_capability"], bool)


# --------------------------------------------------------------------------- #
# The 5 real PILOT_INJECTIONS, graded on whatever the REAL chain actually produces.
# --------------------------------------------------------------------------- #

def test_syslib_missing_pygraphviz_is_discovered_but_the_render_cannot_anchor_it():
    """`enrich()` DOES mint `syslib:libcgraph.so.6` -- a real `native_library_missing`
    classification off the real error text, no hand-built node anywhere. It is
    UNRESOLVED (`os_resolver.PROVIDER_TABLE` has no `("soname", "libcgraph.so.6")`
    entry) AND unreachable by the render (`_anchor_for_cause` needs a quoted name in
    the pytest cause's detail; a soname ImportError never carries one). Two
    independent, real findings about `src/python_deps/depgraph` -- neither fixed here.
    """
    inj = _inj("syslib_pygraphviz")
    graph, result, causes = scenario_for(inj)

    node = graph.get(syslib_id("libcgraph.so.6"))
    assert node is not None, "enrich() must still mint the capability node from the real error text"
    assert node.chosen_fix is None, "os_resolver.PROVIDER_TABLE has no entry for this soname"

    score = grade(inj, graph, result, causes)
    assert score.n_stars == 0
    assert score.root_hit is False
    assert score.not_applicable is False
    assert score.unresolved_capability is True

    text = render_graph_context(graph, result, causes, {})
    assert "NO GRAPH EXPLANATION" in text
    assert "★" not in text


def test_compiler_absent_pyzmq_hits_the_build_essential_root_via_the_real_resolver():
    """The ONE cell that survives de-circularization: a real BUILD-stream,
    owner-anchored discovery (`owner_node_for_command` finds `pkg:pyzmq` from the
    real `failing_command`) resolved through a real `os_resolver.PROVIDER_TABLE`
    table hit (`("binary","gcc") -> "build-essential"`, verified on disk) -- never
    touching the offline executor's apt-file fallback."""
    inj = _inj("compiler_pyzmq")
    graph, result, causes = scenario_for(inj)

    node = graph.get(binary_id("gcc"))
    assert node is not None
    assert node.chosen_fix == "apt:build-essential"
    assert node.state is State.MISSING

    score = grade(inj, graph, result, causes)
    assert score.root_hit is True
    assert score.n_stars == 1
    assert score.star_precision == 1.0
    assert score.mislocalized is False
    assert score.not_applicable is False
    assert score.unresolved_capability is False


def test_version_conflict_urllib3_renders_BLOCKED_never_starred():
    """VERSION_CONFLICT (repin kind): structurally unstarrable -- `verdict()`
    (`graph_context.py`) returns BLOCKED for a CONFLICTS_WITH node before it can ever
    return ACTIONABLE, and BLOCKED renders `✖`, never `★`. Verified by literally
    rendering the shape (see `_scenario_conflict_requests` for why this ONE scenario
    is hand-built rather than chain-replayed -- a live, networked resolver is what
    would really produce this edge, and that is out of OFFLINE scope)."""
    inj = _inj("conflict_requests")
    graph, result, causes = scenario_for(inj)

    text = render_graph_context(graph, result, causes, {})
    assert "✖ pkg:urllib3==1.20" in text
    assert "MISSING — CANNOT INSTALL" in text
    assert "★ pkg:urllib3==1.20" not in text

    score = grade(inj, graph, result, causes)
    assert score.not_applicable is True
    assert score.n_stars == 0
    assert score.root_hit is False
    assert score.mislocalized is False


def test_overinclude_dotenv_the_phantom_package_has_no_node_the_render_degrades_gracefully():
    """OVERINCLUDE (drop kind): `inject.apply_injection`'s `add_install_pkg` op
    appends the phantom package straight to the ALREADY-RENDERED script -- construction
    never saw it, so no node for it can exist. Run through the REAL chain (no
    special-casing, unlike the conflict scenario): `owner_node_for_command` finds no
    owner, `classify_observation` deliberately ignores pip's own "no matching
    distribution" verdict, `enrich()` appends nothing, and the render's own "BUILD
    FAILED -- NO GRAPH EXPLANATION" degrade path fires -- verified, not assumed."""
    inj = _inj("overinclude_dotenv")
    graph, result, causes = scenario_for(inj)

    assert all(not (n.id.startswith("binary:") or n.id.startswith("syslib:")) for n in graph.nodes)

    text = render_graph_context(graph, result, causes, {})
    assert "BUILD FAILED — NO GRAPH EXPLANATION" in text

    score = grade(inj, graph, result, causes)
    assert score.not_applicable is True
    assert score.n_stars == 0
    assert score.root_hit is False


def test_tool_absent_semrel_the_real_gitpython_message_is_invisible_to_the_classifier():
    """TOOL_ABSENT (python-semantic-release / GitPython). The real, EMPIRICALLY
    reproduced GitPython import-time failure (`python3 -m venv`, install GitPython,
    hide `git` from $PATH, `import git`) raises
    `ImportError: Failed to initialize: Bad git executable.` --
    `classify_tool_error` recognizes only `"<name>: not found"`-shaped and `"<name>
    executable not found"`-shaped messages; this matches neither, so `enrich()`
    appends NOTHING. Not an unresolved capability -- no capability at all. `git` is
    also absent from `PROVIDER_TABLE`, but that fact is never even reached."""
    inj = _inj("tool_semrel")
    graph, result, causes = scenario_for(inj)

    assert graph.get(binary_id("git")) is None
    assert {n.id for n in graph.nodes} == {TEST_NODE_ID, package_id("GitPython", "3.1.43")}

    score = grade(inj, graph, result, causes)
    assert score.n_stars == 0
    assert score.root_hit is False
    assert score.not_applicable is False
    assert score.unresolved_capability is False

    text = render_graph_context(graph, result, causes, {})
    assert "NO GRAPH EXPLANATION" in text
    assert "★" not in text


def test_root_hit_is_1_of_3_over_the_valid_denominator_once_the_graph_is_real():
    """The honest headline, spelled out so a change to any one scenario is caught
    here first. 🔴 De-circularizing dropped root_hit from a CIRCULAR 3/5 to an
    HONEST 1/3 -- and 3, not 5, IS the valid denominator: VERSION_CONFLICT and
    OVERINCLUDE are structurally unstarrable by construction (see their own tests
    above), not render defects, and counting them as misses would itself be
    dishonest. n=5 (3 in-scope) is still a SMOKE TEST, not a rate."""
    rows = build_report()
    valid = [r for r in rows if not r["not_applicable"]]
    assert len(valid) == 3
    assert sum(1 for r in valid if r["root_hit"]) == 1

    by_class = {r["failure_class"]: r for r in rows}
    assert by_class["COMPILER_ABSENT"]["root_hit"] is True
    assert by_class["SYSLIB_MISSING"]["root_hit"] is False
    assert by_class["TOOL_ABSENT"]["root_hit"] is False
    assert by_class["VERSION_CONFLICT"]["not_applicable"] is True
    assert by_class["OVERINCLUDE"]["not_applicable"] is True


# --------------------------------------------------------------------------- #
# Star precision -- measured on a REAL multi-candidate render, not fabricated
# arithmetic. None of the 5 injections above naturally produces more than one
# simultaneous ★ (a build-fail turn has exactly one failing_command anchor), so
# precision needs its own, sixth scenario to have any discriminating power at all.
# --------------------------------------------------------------------------- #

_MULTI_MISSING_PYTEST_OUTPUT = """
______________ ERROR collecting tests/test_api.py ______________
Traceback:
tests/test_api.py:1: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'

______________ ERROR collecting tests/test_cli.py ______________
Traceback:
tests/test_cli.py:1: in <module>
    import click
E   ModuleNotFoundError: No module named 'click'

______________ ERROR collecting tests/test_output.py ______________
Traceback:
tests/test_output.py:1: in <module>
    import rich
E   ModuleNotFoundError: No module named 'rich'

______________ ERROR collecting tests/test_templates.py ______________
Traceback:
tests/test_templates.py:1: in <module>
    import jinja2
E   ModuleNotFoundError: No module named 'jinja2'
"""


def test_star_precision_is_measured_on_a_real_render_with_multiple_candidates():
    """root-hit@1 alone is a DISHONEST metric: a graph that stars everything and
    happens to include the right one scores 100%. Four independently-missing
    declared packages, each surfaced through its own real pytest ModuleNotFoundError
    in ONE turn, give the render four simultaneous, genuinely rendered ★ candidates
    -- the number below is MEASURED against the real renderer, not assumed."""
    seed = (_base_graph()
            .with_node(_declared_pkg("requests", "2.31.0"))
            .with_node(_declared_pkg("click", "8.1.7"))
            .with_node(_declared_pkg("rich", "13.7.0"))
            .with_node(_declared_pkg("jinja2", "3.1.4")))
    causes = tuple(summarize(_MULTI_MISSING_PYTEST_OUTPUT))
    result = RunResult(ok=True)
    graph = _replay_turn(seed, result=result, causes=causes)

    star_ids = stars(graph, result, causes)
    assert star_ids == frozenset({
        package_id("requests", "2.31.0"), package_id("click", "8.1.7"),
        package_id("rich", "13.7.0"), package_id("jinja2", "3.1.4"),
    })

    score = grade_stars(star_ids, "click", graph)
    assert score.n_stars == 4
    assert score.root_hit is True
    assert score.star_precision == 0.25, (
        "measured against the real render, not assumed: only 1 of the 4 real stars "
        "matches the 'click' target"
    )


# --------------------------------------------------------------------------- #
# Grading-formula regression guards -- these test grade_stars() itself on a small,
# clearly-labelled hand-built shape, exactly as the plan's own Step 1 snippet does;
# they are not claims about what the real 5-injection replay produces (see the
# real-render tests above for that).
# --------------------------------------------------------------------------- #

def test_a_conflicted_root_is_MISLOCALIZED_if_starred():
    """`emit._is_emittable` already refuses to emit a node sitting on a CONFLICTS_WITH
    edge -- no install ever works for it. A regression guard on the GRADING FORMULA
    itself: even though the real render never actually produces this shape (see
    `test_version_conflict_urllib3_renders_BLOCKED_never_starred` above -- verdict()
    renders such a node ✖, never ★), `grade_stars` must still catch it if it ever did.
    """
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


# --------------------------------------------------------------------------- #
# The COLLAPSE property -- rebuilt to actually mint the node via the real producers.
# --------------------------------------------------------------------------- #

def test_the_COLLAPSE_shows_exactly_one_star_when_two_failures_share_a_root():
    """The arm's headline structural claim (design spec §4.3 "collapse rate") and the
    regression guard for a real, already-shipped bug (`b5a9f65`, "TOOL node
    fracture": one producer minting `tool:pg_config` while another minted
    `binary:pg_config`).

    Seeds ONLY the two declared packages -- no pg_config node anywhere -- and drives
    TWO independent `_replay_turn` calls, one per package's own real build failure, so
    the capability node is minted by the SAME real discovery path
    (`runtime_classify._id_for_discovery`'s TOOL branch -> `capability_id("binary",
    "pg_config")`) from two different owners. That is what would actually catch a
    re-fracture; the old version (one node, hand-built, pointed at by both packages
    by construction) could not have -- it only proved the renderer dedups an
    already-correct graph.
    """
    seed = (_base_graph()
            .with_node(_declared_pkg("psycopg2", "2.9.12"))
            .with_node(_declared_pkg("asyncpg", "0.29.0")))
    pg_config_missing = "Error: pg_config executable not found.\n"

    g1 = _replay_turn(
        seed,
        result=RunResult(ok=False, failing_command="pip install psycopg2==2.9.12",
                         output=pg_config_missing),
        causes=(),
    )
    g2 = _replay_turn(
        g1,
        result=RunResult(ok=False, failing_command="pip install asyncpg==0.29.0",
                         output=pg_config_missing),
        causes=(),
    )

    pg_config_nodes = [n for n in g2.nodes if n.name == "pg_config"]
    assert len(pg_config_nodes) == 1, "two independent discoveries must collapse onto ONE node"
    assert pg_config_nodes[0].id == binary_id("pg_config")
    assert g2.get(tool_id("pg_config")) is None, "the fracture guard: no tool:pg_config twin"

    requires_pg_config = {
        e.src for e in g2.edges
        if e.relation is EdgeType.REQUIRES and e.dst == binary_id("pg_config")
    }
    assert requires_pg_config == {package_id("psycopg2", "2.9.12"), package_id("asyncpg", "0.29.0")}

    result2 = RunResult(ok=False, failing_command="pip install asyncpg==0.29.0",
                        output=pg_config_missing)
    star_ids = stars(g2, result2, ())
    assert star_ids == frozenset({binary_id("pg_config")})
