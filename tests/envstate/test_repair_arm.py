"""Test run_repair_arm global control flow: an unfixable error gives up honestly."""
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
from python_deps.depgraph.patch import PatchProposal, NodeSpec, ProviderSpec  # noqa: E402
from src.envstate.repair_arm import run_repair_arm  # noqa: E402
from src.eval.repair_arm_eval.mock_world import FakeWorld, RealNode  # noqa: E402


class _NullLog:
    def d(self, *a, **k):
        pass


class _NoFixAgent:
    """Proposes a valid-but-useless patch each turn — never resolves -> stall -> giveup."""
    def next_action(self, session, failure, log):
        nid = f"syslib:dummy-{failure.failing_cap}"
        p = PatchProposal(
            add_requirements=(NodeSpec(id=nid, type="SystemLib", name="d", layer="system",
                                       check_command=f"ldconfig -p | grep -q d{failure.failing_cap}",
                                       evidence_ref="ev.1"),),
            add_providers=(ProviderSpec(id="apt:dummy", kind="apt",
                                        command="apt-get install -y dummy", provides=(nid,)),))
        return ("patch", p, failure.failing_cap)


def test_unfixable_gives_up():
    reality = {"pkg:m": RealNode("m", frozenset({"libz"}), "python -c 'import m'")}
    world = FakeWorld(reality)
    g = DepGraph().with_node(Node(id="pkg:m", type=NodeType.PACKAGE, name="m", layer=Layer.PIP,
                                  discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
                                  version="1"))
    outcome, _ = run_repair_arm(g, replay=lambda gr, mb=(): world.replay_from_base(gr),
                                certify=world.certify, agent=_NoFixAgent(), log=_NullLog())
    assert outcome == "GIVEUP"
