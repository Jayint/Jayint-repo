"""Tests for discovery_expand (no network — seed_build_deps_for is stubbed)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import python_deps.depgraph.discovery_expand as dx
from python_deps.depgraph.ids import package_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)


class _Exec:
    """CommandResult fields are (command, returncode, stdout, stderr) — there is NO `rc`."""
    def run(self, command, **_kw):
        from python_deps.depgraph.executor import CommandResult
        return CommandResult(command, 0, "", "")


def _pkg(name, version, discovered_by=DiscoveredBy.RUNTIME) -> Node:
    return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=discovered_by, version=version,
                state=State.MISSING)


def test_expands_a_versioned_discovery_through_the_real_oracle(monkeypatch):
    """The oracle (build_dep_prior, via seed_build_deps_for) is stubbed — we assert that
    expand_discovery CALLS it with the right node and grafts what it returns."""
    calls = []

    def _fake_seed_for(graph, pkg, executor):
        calls.append(pkg.name)
        g = (graph
             .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                             layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                             state=State.MISSING))
             .with_edge(Edge(src=pkg.id, dst="binary:pg_config",
                             relation=EdgeType.REQUIRES, origin="resolver")))
        return g, 1, 1, 0                       # (graph, pkgs, cap_nodes, aptdep_nodes)

    monkeypatch.setattr(dx, "seed_build_deps_for", _fake_seed_for)
    g = DepGraph().with_node(_pkg("psycopg2", "2.9.12"))
    new, expanded = dx.expand_discovery(g, ["pkg:psycopg2==2.9.12"], _Exec())
    assert calls == ["psycopg2"]
    assert expanded == {"pkg:psycopg2==2.9.12"}
    assert new.get("binary:pg_config") is not None
    assert any(e.src == "pkg:psycopg2==2.9.12" and e.dst == "binary:pg_config"
               for e in new.edges)


def test_a_versionless_discovery_is_NOT_expanded(monkeypatch):
    """build_dep_prior needs a version (build_deps.py skips versionless packages).
    Without one we mark it unresolved and expand NOTHING — expansion propagates a bad
    anchor's wrongness through a whole fabricated subtree (the 6->0 property)."""
    called = []
    monkeypatch.setattr(dx, "seed_build_deps_for",
                        lambda g, p, e: (called.append(p.name), (g, 1, 0, 0))[1])
    g = DepGraph().with_node(_pkg("patchright", None))
    new, expanded = dx.expand_discovery(g, ["pkg:patchright"], _Exec())
    assert called == []
    assert expanded == set()


def test_a_non_package_node_is_not_expanded(monkeypatch):
    called = []
    monkeypatch.setattr(dx, "seed_build_deps_for",
                        lambda g, p, e: (called.append(p.name), (g, 1, 0, 0))[1])
    g = DepGraph().with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RUNTIME,
                                  state=State.MISSING))
    _new, expanded = dx.expand_discovery(g, ["binary:pg_config"], _Exec())
    assert called == []
    assert expanded == set()


def test_a_node_is_expanded_at_most_ONCE_across_turns(monkeypatch):
    """The script re-runs from base every turn, so the same failure recurs. Without the
    `expanded` set we would re-hit the network with build_dep_prior every single turn."""
    called = []
    monkeypatch.setattr(dx, "seed_build_deps_for",
                        lambda g, p, e: (called.append(p.name), (g, 1, 0, 0))[1])
    g = DepGraph().with_node(_pkg("psycopg2", "2.9.12"))
    _g1, exp1 = dx.expand_discovery(g, ["pkg:psycopg2==2.9.12"], _Exec())
    _g2, exp2 = dx.expand_discovery(g, ["pkg:psycopg2==2.9.12"], _Exec(), expanded=exp1)
    assert called == ["psycopg2"]                     # ONCE, not twice
    assert exp2 == exp1


def test_expansion_never_raises(monkeypatch):
    def _boom(graph, pkg, executor):
        raise RuntimeError("network down")
    monkeypatch.setattr(dx, "seed_build_deps_for", _boom)
    g = DepGraph().with_node(_pkg("psycopg2", "2.9.12"))
    new, expanded = dx.expand_discovery(g, ["pkg:psycopg2==2.9.12"], _Exec())
    assert new is not None                            # the run must never break (spec §11)


def test_a_FAILING_expansion_is_not_retried_every_turn(monkeypatch):
    """The prior is best-effort; re-paying for it every turn is not.

    The id was only marked expanded on SUCCESS, so a node whose expansion threw was retried on
    every subsequent turn — and the react loop re-runs the script from base each turn, so the
    same failure recurs forever. Each retry is fresh network/container work inside a loop where
    one turn already costs a full container rebuild.
    """
    import python_deps.depgraph.discovery_expand as dx

    calls = []

    def boom(graph, pkg, executor):
        calls.append(pkg.id)
        raise RuntimeError("resolver down")

    monkeypatch.setattr(dx, "seed_build_deps_for", boom)

    pkg = Node(id=package_id("psycopg2", "2.9.12"), type=NodeType.PACKAGE, name="psycopg2",
               layer=Layer.PIP, discovered_by=DiscoveredBy.RUNTIME, version="2.9.12",
               state=State.MISSING)
    graph = DepGraph().with_node(pkg)

    _g1, done1 = dx.expand_discovery(graph, [pkg.id], None)
    _g2, done2 = dx.expand_discovery(graph, [pkg.id], None, done1)

    assert calls == [pkg.id]          # attempted exactly ONCE, not once per turn
    assert pkg.id in done1
    assert done2 == done1
