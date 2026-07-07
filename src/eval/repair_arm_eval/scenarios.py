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
