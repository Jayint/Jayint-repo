"""Tests for graph_context edge semantics (pure; no Docker, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.graph_context import (
    ACTIONABLE, BLOCKED, SATISFIED_OK, WAITING, blocks, in_conflict, verdict,
)
from python_deps.depgraph.ids import capability_id, package_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)


def _pkg(name, version="1.0", state=State.MISSING) -> Node:
    return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                version=version, state=state)


def _tool(name, state=State.MISSING) -> Node:
    return Node(id=f"binary:{name}", type=NodeType.TOOL, name=name,
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=state)


def test_actionable_when_nothing_beneath_is_missing():
    g = DepGraph().with_node(_tool("pg_config"))
    assert verdict(g, g.get("binary:pg_config")) == ACTIONABLE


def test_waiting_when_a_hard_prerequisite_is_missing():
    g = (DepGraph()
         .with_node(_pkg("psycopg2"))
         .with_node(_tool("pg_config"))
         .with_edge(Edge(src="pkg:psycopg2==1.0", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver")))
    assert verdict(g, g.get("pkg:psycopg2==1.0")) == WAITING
    assert verdict(g, g.get("binary:pg_config")) == ACTIONABLE


def test_satisfied_prerequisite_does_not_make_the_owner_wait():
    g = (DepGraph()
         .with_node(_pkg("psycopg2"))
         .with_node(_tool("pg_config", state=State.SATISFIED))
         .with_edge(Edge(src="pkg:psycopg2==1.0", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver")))
    assert verdict(g, g.get("pkg:psycopg2==1.0")) == ACTIONABLE


# ── THE BUG THIS TASK FIXES ──────────────────────────────────────────────────

def test_conflicted_node_is_BLOCKED_even_with_zero_missing_prerequisites():
    """The exact shape that fooled the old root definition.

    `pkg:pydantic` is MISSING and has NO missing prerequisite, so "MISSING with no
    MISSING prerequisite" calls it a root and tells the agent `pip install pydantic`.
    It CANNOT be installed at any version — emit._is_emittable already refuses to emit
    a conflicted node. It must be BLOCKED, never ACTIONABLE.
    """
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11"))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    node = g.get("pkg:pydantic==2.11")
    assert in_conflict(g, node) is True
    assert verdict(g, node) == BLOCKED
    assert verdict(g, node) != ACTIONABLE


def test_conflict_blocks_BOTH_endpoints():
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11"))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    assert verdict(g, g.get("pkg:fastapi==0.115")) == BLOCKED


def test_a_conflicts_edge_is_not_a_prerequisite():
    # CONFLICTS_WITH must never be traversed as a requires edge.
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.CONFLICTS_WITH,
             origin="resolver")
    assert blocks(e) is False


# ── soft edges ───────────────────────────────────────────────────────────────

def test_soft_edge_does_not_block():
    # emit.py:69-70 — "soft requires edges never block (invariant #10)".
    e = Edge(src="pkg:a==1.0", dst="config:DATABASE_URL", relation=EdgeType.REQUIRES,
             origin="llm", data={"hard": False})
    assert blocks(e) is False


def test_soft_missing_prerequisite_leaves_the_owner_actionable():
    # NOTE: DiscoveredBy has NO `LLM` member. The codebase's name for an LLM-admitted node is
    # CLASSIFIER (schema.py:64-74; see patch_gate.py:250).
    g = (DepGraph()
         .with_node(_pkg("app"))
         .with_node(Node(id="config:DATABASE_URL", type=NodeType.CONFIG,
                         name="DATABASE_URL", layer=Layer.CONFIG,
                         discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING))
         .with_edge(Edge(src="pkg:app==1.0", dst="config:DATABASE_URL",
                         relation=EdgeType.REQUIRES, origin="llm",
                         data={"hard": False})))
    assert verdict(g, g.get("pkg:app==1.0")) == ACTIONABLE


# ── a SATISFIED node is not "actionable" ─────────────────────────────────────

def test_a_satisfied_node_is_never_actionable():
    """A SATISFIED leaf has no missing prerequisites, so a verdict() that only looked at
    PREREQUISITES would call it ACTIONABLE and hand it a record — telling the agent to
    'fix' something that is already fine. verdict() must check the node's OWN state first."""
    g = DepGraph().with_node(_tool("pkg-config", state=State.SATISFIED))
    assert verdict(g, g.get("binary:pkg-config")) == SATISFIED_OK
    assert verdict(g, g.get("binary:pkg-config")) != ACTIONABLE


def test_hard_edge_is_the_default_when_the_key_is_absent():
    e = Edge(src="pkg:a==1.0", dst="binary:x", relation=EdgeType.REQUIRES, origin="resolver")
    assert blocks(e) is True


# ── markers ──────────────────────────────────────────────────────────────────

def test_marker_that_does_not_hold_is_skipped():
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.REQUIRES,
             origin="resolver", marker='python_version < "3.9"')
    assert blocks(e, target_env={"python_version": "3.12"}) is False


def test_marker_that_holds_is_traversed():
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.REQUIRES,
             origin="resolver", marker='python_version >= "3.9"')
    assert blocks(e, target_env={"python_version": "3.12"}) is True


def test_marker_is_conservatively_traversed_when_no_target_env_is_known():
    # Without a target we cannot evaluate — do NOT silently drop a real prerequisite.
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.REQUIRES,
             origin="resolver", marker='python_version < "3.9"')
    assert blocks(e, target_env=None) is True


def test_unparseable_marker_is_conservatively_traversed():
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.REQUIRES,
             origin="resolver", marker="this is not a marker")
    assert blocks(e, target_env={"python_version": "3.12"}) is True
