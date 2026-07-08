"""Mocked-depgraph scenarios for the arm-C mechanics eval. Each returns
``(graph, FakeWorld, ScriptedSolver)``."""
from __future__ import annotations

from python_deps.depgraph.schema import (
    DepGraph, Node, Edge, NodeType, Layer, State, DiscoveredBy, EdgeType,
)
from src.eval.repair_arm_eval.mock_world import FakeWorld, RealNode
from src.eval.repair_arm_eval.scripted_agent import ScriptedSolver, Fix, syslib_patch, tool_patch


def _base_graph(pkg_id, pkg_name):
    g = DepGraph().with_node(Node(id="project:demo", type=NodeType.PROJECT, name="demo",
                                  layer=Layer.PIP, discovered_by=DiscoveredBy.GOAL))
    g = g.with_node(Node(id=pkg_id, type=NodeType.PACKAGE, name=pkg_name, layer=Layer.PIP,
                         discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING, version="1.0",
                         check_command=f"python -c 'import {pkg_name}'"))
    return g.with_edge(Edge(src="project:demo", dst=pkg_id, relation=EdgeType.REQUIRES,
                            data={"hard": True}))


def scenario_simple():
    """A package needs one syslib. One patch, done."""
    reality = {
        "pkg:cryptography": RealNode("cryptography", frozenset({"ffi"}),
                                     "python -c 'import cryptography'"),
        "syslib:ffi": RealNode("ffi", frozenset(), "ldconfig -p | grep -q libffi"),
    }
    fixes = {"ffi": Fix("ldconfig -p | grep -q libffi",
                        syslib_patch("ffi", "syslib:ffi", "libffi-dev",
                                     "ldconfig -p | grep -q libffi", "pkg:cryptography"))}
    return _base_graph("pkg:cryptography", "cryptography"), FakeWorld(reality), ScriptedSolver(fixes)


def scenario_chain():
    """psycopg2 needs libpq THEN pg_config — one session must follow the failure forward."""
    reality = {
        "pkg:psycopg2": RealNode("psycopg2", frozenset({"libpq", "pg_config"}),
                                 "python -c 'import psycopg2'"),
        "syslib:libpq": RealNode("libpq", frozenset(), "ldconfig -p | grep -q libpq"),
        "tool:pg_config": RealNode("pg_config", frozenset(), "command -v pg_config"),
    }
    fixes = {
        "libpq": Fix("ldconfig -p | grep -q libpq",
                     syslib_patch("libpq", "syslib:libpq", "libpq-dev",
                                  "ldconfig -p | grep -q libpq", "pkg:psycopg2")),
        "pg_config": Fix("command -v pg_config",
                         tool_patch("pg_config", "tool:pg_config", "libpq-dev",
                                    "command -v pg_config", "pkg:psycopg2")),
    }
    return _base_graph("pkg:psycopg2", "psycopg2"), FakeWorld(reality), ScriptedSolver(fixes)


def scenario_stall():
    """A package needs a capability the agent has no fix for — must stall + give up honestly."""
    reality = {
        "pkg:mystery": RealNode("mystery", frozenset({"libunobtainium"}),
                                "python -c 'import mystery'"),
    }
    return _base_graph("pkg:mystery", "mystery"), FakeWorld(reality), ScriptedSolver({})


def scenario_hidden_gap():
    """The KNOWN-BUG shape: a required SystemLib node already exists in the graph
    (check_command set) but has no chosen_fix yet, so render_build_script emits
    NOTHING for it (emit._is_reciped is False) — the script trivially exits 0 on
    the FIRST cycle even though the obligation was never installed. FakeWorld's own
    replay is blind to this too (a node with no chosen_fix is skipped from its
    install-order walk), so only the top-level unmet-required-node check (not the
    replay result) can catch it. The agent DOES have a fix, so the loop must
    localize + repair it and reach a REAL DONE on the next cycle."""
    reality = {
        "pkg:widget": RealNode("widget", frozenset(), "python -c 'import widget'"),
        "syslib:ghost": RealNode("ghost", frozenset(), "ldconfig -p | grep -q libghost"),
    }
    g = _base_graph("pkg:widget", "widget")
    g = g.with_node(Node(id="syslib:ghost", type=NodeType.SYSTEM_LIB, name="ghost",
                         layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                         state=State.MISSING, check_command="ldconfig -p | grep -q libghost"))
    fixes = {"syslib:ghost": Fix("ldconfig -p | grep -q libghost",
                                 syslib_patch("ghost", "syslib:ghost", "libghost-dev",
                                              "ldconfig -p | grep -q libghost", "pkg:widget"))}
    return g, FakeWorld(reality), ScriptedSolver(fixes)
