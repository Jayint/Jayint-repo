#!/usr/bin/env python3
"""Watch the graph arm work, without Docker.

    python3 scripts/react_graph_demo.py            # G3: render + grow  (the full arm)
    python3 scripts/react_graph_demo.py --rung G2  # render only, frozen topology
    python3 scripts/react_graph_demo.py --rung G1  # the control: no graph at all

Drives the REAL `run_react` loop -- real classifier, real enrich, real certify, real renderer,
real planner seam -- against `FakeSandbox`. The only thing mocked is Docker and the LLM.

The scripted "agent" is deliberately DUMB: it reads the ★ line out of the graph context and
installs whatever apt package the graph names. That is the point. If the graph is right, a dumb
agent succeeds; if the graph is wrong, no amount of cleverness saves it. On G1 the same agent has
no graph to read, so it must guess -- and you can watch it fail.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests" / "react_repair"))  # FakeSandbox test double

from graph.ids import TEST_NODE_ID, package_id
from graph.model import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)
from src.agent.entry import build_graph_hooks, docker_adapters, rung_flags
from fake_sandbox import FakeSandbox
from src.agent.history import History
from src.agent.log import ReactLog
from src.agent.loop import run_react
from src.agent.actions import Action

BAR = "━" * 78


def demo_graph() -> DepGraph:
    """The graph as CONSTRUCTION leaves it: two declared packages, and NOTHING about pg_config.

    That absence is deliberate. The build tool is the thing the arm has to DISCOVER at runtime —
    if we seeded it here, the demo would be testing the seed, not the arm.
    """
    def pkg(name, version):
        return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                    layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                    version=version, state=State.MISSING, build_from_source=True)

    def imp(name):
        return Node(id=f"import:{name}", type=NodeType.IMPORT, name=name, layer=Layer.PIP,
                    discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING)

    g = (DepGraph()
         .with_node(Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                         layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL))
         .with_node(pkg("psycopg2", "2.9.12"))
         .with_node(pkg("asyncpg", "0.30.0"))
         .with_node(imp("psycopg2"))
         .with_node(imp("asyncpg")))
    for name, node_id in (("psycopg2", "pkg:psycopg2==2.9.12"), ("asyncpg", "pkg:asyncpg==0.30.0")):
        g = (g.with_edge(Edge(src=f"import:{name}", dst=node_id,
                              relation=EdgeType.REQUIRES, origin="static"))
              .with_edge(Edge(src=TEST_NODE_ID, dst=f"import:{name}",
                              relation=EdgeType.REQUIRES, origin="static")))
    return g


class WatchingPlanner:
    """Prints the prompt the arm actually produced, then acts on the ★ the graph named.

    Mirrors ReactPlanner's seam exactly: `graph_context` is a CONSTRUCTOR callable, invoked with
    (graph, result, causes, prev_states). Those last three are what the loop hands the planner
    each turn — the same four arguments the real planner passes.
    """

    def __init__(self, graph_context=None) -> None:
        self.graph_context = graph_context
        self.turn = 0
        self.saw_graph = False

    def plan(self, history, script, observation, graph, *, result=None, causes=None,
             prev_states=None, **kw):
        self.turn += 1
        print(f"\n{BAR}\nTURN {self.turn} — what the agent is looking at\n{BAR}")
        print((observation or "").strip()[:600] or "(no observation)")

        ctx = ""
        if self.graph_context is not None:
            ctx = self.graph_context(graph, result, causes or [], prev_states or {}) or ""
        if ctx:
            self.saw_graph = True
            print(f"\n{'─' * 78}\nGRAPH CONTEXT (the ONLY delta between arms)\n{'─' * 78}")
            print(ctx.strip())

        return "t", self._act(ctx, script), {}

    def _act(self, ctx: str, script: str) -> Action:
        # Read the fix straight off the ★ record. A graph that localises correctly makes the
        # agent's job trivial; that is the entire thesis.
        for line in ctx.splitlines():
            if line.strip().startswith("fix ") and "apt-get" in line:
                cmd = line.split("fix", 1)[1].strip()
                if cmd not in script:
                    print(f"\n>>> agent: the graph names a root. Prepending: {cmd}")
                    return Action("patch", new_script=f"{cmd}\n{script}")
        if "apt-get" not in script:
            # No graph (G0/G1): nothing localises the failure, so the agent is reduced to guessing.
            print("\n>>> agent: no graph. Guessing `apt-get install -y postgresql` from the error text.")
            return Action("patch", new_script=f"apt-get install -y postgresql\n{script}")
        print("\n>>> agent: out of ideas.")
        return Action("done")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rung", default="G3", choices=("G1", "G2", "G3"))
    ap.add_argument("--max-steps", type=int, default=4)
    args = ap.parse_args()

    os.environ.pop("REACT_GRAPH_CONTEXT", None)
    os.environ.pop("REACT_GRAPH_UPDATE", None)
    if args.rung == "G2":
        os.environ["REACT_GRAPH_CONTEXT"] = "1"
    elif args.rung == "G3":
        os.environ["REACT_GRAPH_UPDATE"] = "1"

    want_ctx, want_update = rung_flags()
    print(f"{BAR}\n{args.rung}: render={want_ctx}  grow={want_update}\n{BAR}")

    sandbox = FakeSandbox()
    graph = demo_graph()

    # The script construction hands over. It is WRONG -- it never installs the build tool --
    # which is exactly the situation the repair arm exists for.
    script = "pip install psycopg2==2.9.12\npip install asyncpg==0.30.0\n"

    reset, run_script, certify, exec_readonly, run_tests = docker_adapters(sandbox, 0.8)
    # The SAME hook builder the real arm uses — not a replica that could drift from it.
    hooks = build_graph_hooks(want_ctx, want_update, sandbox.exec_readonly, repo_path=None)
    planner = WatchingPlanner(graph_context=hooks.pop("graph_context"))

    outcome, final_script, _ = run_react(
        graph, reset=reset, run_script=run_script, certify=certify,
        exec_readonly=exec_readonly, run_tests=run_tests, planner=planner,
        history=History(), log=ReactLog(silent=True), max_steps=args.max_steps,
        _initial_script=script, **hooks,
    )

    print(f"\n{BAR}\nRESULT: {outcome}\n{BAR}")
    print("final script:\n  " + final_script.strip().replace("\n", "\n  "))
    print(f"\ncontainer now has: binaries={sorted(sandbox.binaries - sandbox.base_binaries)} "
          f"pip={sorted(sandbox.pip)}")
    print(f"agent ever saw a graph: {planner.saw_graph}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
