"""Test the FakeWorld reality model: replay fails on a missing real dep; certify flips
only present nodes."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from src.eval.repair_arm_eval.mock_world import FakeWorld, RealNode  # noqa: E402


def _pkg(nid, name):
    return Node(id=nid, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING, version="1.0",
                check_command=f"python -c 'import {name}'")


def test_replay_fails_when_real_dep_missing():
    reality = {"pkg:foo": RealNode("foo", frozenset({"libx"}), "python -c 'import foo'")}
    world = FakeWorld(reality)
    g = DepGraph().with_node(_pkg("pkg:foo", "foo"))
    r = world.replay_from_base(g)
    assert not r.ok and r.failing_node == "pkg:foo" and r.failing_cap == "libx"


def test_certify_flips_only_present_nodes():
    reality = {"pkg:foo": RealNode("foo", frozenset(), "python -c 'import foo'")}
    world = FakeWorld(reality)
    g = DepGraph().with_node(_pkg("pkg:foo", "foo"))
    world.replay_from_base(g)                 # installs foo (no reqs) -> present
    g = world.certify(g)
    assert g.get("pkg:foo").state is State.SATISFIED
