"""The graph arm, end to end, against a container that isn't one.

Every other test in this suite hand-builds its graph. That is exactly how two real defects
survived 2001 passing tests:

  * the fixtures set `chosen_fix="apt-get install -y libpq-dev"` (a shell command), while the
    real system stores `chosen_fix="apt:libpq-dev"` (a provider id) — so the renderer was never
    asked to convert, and shipped an unrunnable `fix apt:libpq-dev` line to the agent;
  * the fixtures set a `chosen_fix` at all, while a RUNTIME-discovered capability node arrives
    with none — so nobody noticed the arm never resolved one.

These tests build NOTHING by hand. The graph is what construction leaves, the errors are what a
container emits, and the fix has to survive the whole chain: classify -> enrich -> expand ->
certify -> render -> act. `FakeSandbox` fakes Docker and nothing else.
"""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from python_deps.depgraph.ids import TEST_NODE_ID, package_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)
from src.react_repair.actions import Action
from src.react_repair.entry import build_graph_hooks, docker_adapters
from src.react_repair.fake_sandbox import FakeSandbox
from src.react_repair.history import History
from src.react_repair.log import ReactLog
from src.react_repair.loop import run_react

_SEED = "pip install psycopg2==2.9.12\npip install asyncpg==0.30.0\n"


def _graph() -> DepGraph:
    """What CONSTRUCTION leaves: two declared packages, and nothing at all about pg_config.

    That absence is the point. The build tool is what the arm must DISCOVER; seeding it here
    would test the seed instead of the arm.
    """
    def pkg(name, version):
        return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                    layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN, version=version,
                    state=State.MISSING, build_from_source=True)

    g = (DepGraph()
         .with_node(Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                         layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL))
         .with_node(pkg("psycopg2", "2.9.12"))
         .with_node(pkg("asyncpg", "0.30.0")))
    for name in ("psycopg2", "asyncpg"):
        g = g.with_node(Node(id=f"import:{name}", type=NodeType.IMPORT, name=name,
                             layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                             state=State.MISSING))
    return (g
            .with_edge(Edge(src="import:psycopg2", dst="pkg:psycopg2==2.9.12",
                            relation=EdgeType.REQUIRES, origin="static"))
            .with_edge(Edge(src="import:asyncpg", dst="pkg:asyncpg==0.30.0",
                            relation=EdgeType.REQUIRES, origin="static")))


class _LiteralAgent:
    """Does exactly what the graph tells it and nothing else.

    Deliberately incapable of insight: it copies the `fix` line out of the ★ record. If the graph
    localises correctly, this agent wins; if it does not, no cleverness would save it. That makes
    the test a measurement of the GRAPH, not of a model.
    """

    def __init__(self, graph_context=None):
        self.graph_context = graph_context
        self.contexts: list[str] = []

    def plan(self, history, script, observation, graph, *, result=None, causes=None,
             prev_states=None, **kw):
        ctx = ""
        if self.graph_context is not None:
            ctx = self.graph_context(graph, result, causes or [], prev_states or {}) or ""
        self.contexts.append(ctx)

        for line in ctx.splitlines():
            if line.strip().startswith("fix ") and "apt-get" in line and line.split("fix", 1)[1].strip() not in script:
                cmd = line.split("fix", 1)[1].strip()
                return "t", Action("patch", new_script=f"{cmd}\n{script}"), {}
        return "t", Action("done"), {}


def _run(rung: str, max_steps: int = 6, threshold: float = 0.8):
    sandbox = FakeSandbox()
    reset, run_script, certify, exec_readonly, run_tests = docker_adapters(sandbox, threshold)
    hooks = build_graph_hooks(want_ctx=rung in ("G2", "G3"), want_update=(rung == "G3"),
                              exec_readonly=sandbox.exec_readonly)
    agent = _LiteralAgent(graph_context=hooks.pop("graph_context"))
    outcome, script, _ = run_react(
        _graph(), reset=reset, run_script=run_script, certify=certify,
        exec_readonly=exec_readonly, run_tests=run_tests, planner=agent,
        history=History(), log=ReactLog(silent=True), max_steps=max_steps,
        _initial_script=_SEED, **hooks,
    )
    return outcome, script, sandbox, agent


def test_G3_repairs_the_build_in_ONE_turn_from_a_graph_that_never_knew_about_pg_config():
    """The whole thesis, measured.

    Nothing in the seed graph mentions pg_config. The container fails with the real setuptools
    wording ("Error: pg_config executable not found." — no colon, which is what used to classify
    as AMBIGUOUS). One turn later the agent has the right apt package.

    Everything in the chain has to work for this to pass: the classifier has to recognise the
    shape, enrich has to anchor the discovery at the OWNER package, the resolver has to turn the
    capability into `libpq-dev`, certify has to confirm it is really missing, and the renderer has
    to hand the agent a command it can actually run.
    """
    outcome, script, sandbox, agent = _run("G3")

    assert outcome == "DONE"
    assert "apt-get install -y libpq-dev" in script
    assert "pg_config" in sandbox.binaries
    assert sandbox.pip == {"psycopg2", "asyncpg"}
    # ONE planning turn. The loop exits the moment the gate passes, so a second call would
    # mean the graph had failed to localise the root on first sight.
    assert len(agent.contexts) == 1


def test_G3_hands_the_agent_a_RUNNABLE_command_never_an_internal_provider_id():
    _outcome, _script, _sandbox, agent = _run("G3")
    first = agent.contexts[0]
    assert "★ binary:pg_config" in first
    assert "fix      apt-get install -y libpq-dev" in first
    assert "apt:" not in first, "the apt: provider id is internal and must never reach the agent"


def test_G3_anchors_the_discovery_at_the_OWNER_package_not_the_test_node():
    # The edge is `pkg:psycopg2 --requires--> binary:pg_config`. Anchored at the Test node it
    # would be a flat star with no depth, and the subgraph would have nothing to say about WHY.
    _outcome, _script, _sandbox, agent = _run("G3")
    assert "pkg:psycopg2==2.9.12  --requires-->  binary:pg_config" in agent.contexts[0]
    assert "test:repo_tests_pass  --requires-->  binary:pg_config" not in agent.contexts[0]


def test_G1_the_control_never_sees_a_graph_and_cannot_repair_it():
    """The control. Same container, same failure, same agent — no graph.

    The agent has nothing to copy a fix from, so it gives up. This is what the G1->G2->G3 rungs
    are measuring, and it is why the seam being the ONLY delta between arms matters.
    """
    outcome, script, sandbox, agent = _run("G1")

    assert outcome != "DONE"
    assert "apt-get" not in script
    assert sandbox.pip == set()
    assert agent.contexts == [""] * len(agent.contexts), "G1 must never receive graph context"


def test_a_call_phase_AssertionError_never_becomes_a_graph_node():
    """The phase gate, end to end. Once the build is green the suite still has one failing test
    body (`assert 3 == 4`). No environment fix exists for that, and "the test's own code decided
    it was wrong" IS the line between no-env-fix and env-fix — so it must not put a node in the
    graph, however its message reads.
    """
    # threshold=1.0 so the one failing test body keeps the gate shut and the loop runs on —
    # otherwise the build goes green, the gate passes, and the agent never sees a pytest turn.
    _outcome, _script, sandbox, agent = _run("G3", threshold=1.0)
    final = agent.contexts[-1]
    assert "psycopg2" in sandbox.pip, "the build must be GREEN for pytest to have run at all"
    assert "NOT AN ENVIRONMENT FAILURE" in final
    assert "AssertionError" in final
    assert "★ " not in final.split("NOT AN ENVIRONMENT FAILURE")[1]
