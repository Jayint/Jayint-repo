"""Test fix_one_error: a sustained session resolves an error and persists to the attempts axis."""
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
from python_deps.depgraph.patch import PatchProposal, NodeSpec, ProviderSpec, EdgeSpec  # noqa: E402
from src.envstate.repair_fix import fix_one_error  # noqa: E402
from src.eval.repair_arm_eval.mock_world import FakeWorld, RealNode  # noqa: E402


class _NullLog:
    def d(self, *a, **k):
        pass


class _OneShotAgent:
    """Emits one patch that adds the missing syslib, then (never asked again)."""
    def next_action(self, session, failure, log):
        p = PatchProposal(
            add_requirements=(NodeSpec(id="syslib:ffi", type="SystemLib", name="ffi",
                                       layer="system", check_command="ldconfig -p | grep -q libffi",
                                       evidence_ref="ev.1"),),
            add_providers=(ProviderSpec(id="apt:libffi-dev", kind="apt",
                                        command="apt-get install -y libffi-dev",
                                        provides=("syslib:ffi",)),),
            add_edges=(EdgeSpec(source="pkg:cryptography", target="syslib:ffi", hard=True),))
        return ("patch", p, "ffi")


def test_fix_one_error_resolves_and_persists():
    reality = {
        "pkg:cryptography": RealNode("cryptography", frozenset({"ffi"}),
                                     "python -c 'import cryptography'"),
        "syslib:ffi": RealNode("ffi", frozenset(), "ldconfig -p | grep -q libffi"),
    }
    world = FakeWorld(reality)
    g = DepGraph().with_node(Node(id="pkg:cryptography", type=NodeType.PACKAGE, name="cryptography",
                                  layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                                  state=State.MISSING, version="1"))
    err = world.replay_from_base(g)
    assert not err.ok and err.failing_cap == "ffi"
    g2, outcome = fix_one_error(g, err, agent=_OneShotAgent(),
                                replay=lambda gr, mb=(): world.replay_from_base(gr),
                                certify=world.certify, log=_NullLog())
    assert outcome == "resolved"
    assert g2.get("syslib:ffi") is not None
    assert g2.get("pkg:cryptography").attempts               # transcript persisted
