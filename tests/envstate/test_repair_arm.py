"""Test run_repair_arm global control flow: an unfixable error gives up honestly."""
from __future__ import annotations

import sys
from dataclasses import replace
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
from src.envstate.repair_types import ReplayResult  # noqa: E402
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


def test_green_replay_with_unmet_required_node_is_not_falsely_done():
    """KNOWN BUG regression: replay.ok=True only means the rendered SCRIPT exited 0.
    A SystemLib node with no chosen_fix/version renders NOTHING (emit._is_reciped is
    False for it), so a trivially-green replay must NOT be mistaken for DONE while
    that check-bearing node is still MISSING — it must be localized and repaired
    (here: repair fails, so the loop must give up honestly, never claim DONE)."""
    g = DepGraph().with_node(Node(id="syslib:mystery", type=NodeType.SYSTEM_LIB, name="mystery",
                                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                                  state=State.MISSING,
                                  check_command="ldconfig -p | grep -q libmystery"))

    def always_green_replay(gr, mb=()):
        return ReplayResult(True)          # the (empty) script trivially exits 0

    def identity_certify(gr):
        return gr                          # nothing installed it -> check still fails -> MISSING

    outcome, graph = run_repair_arm(g, replay=always_green_replay, certify=identity_certify,
                                    agent=_NoFixAgent(), log=_NullLog())
    assert outcome != "DONE"
    assert graph.get("syslib:mystery").state is State.MISSING


def test_unmet_required_node_gets_localized_and_fixed_then_done():
    """Positive-path companion: once the previously-unrendered node is given a
    provider, DONE must still fire — the new check does not block a real success."""
    reality = {"syslib:mystery": RealNode("mystery", frozenset(), "ldconfig -p | grep -q libmystery")}
    world = FakeWorld(reality)
    g = DepGraph().with_node(Node(id="syslib:mystery", type=NodeType.SYSTEM_LIB, name="mystery",
                                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                                  state=State.MISSING,
                                  check_command="ldconfig -p | grep -q libmystery"))

    class _FixesMysteryAgent:
        def next_action(self, session, failure, log):
            p = PatchProposal(
                add_providers=(ProviderSpec(id="apt:libmystery-dev", kind="apt",
                                            command="apt-get install -y libmystery-dev",
                                            provides=("syslib:mystery",)),))
            return ("patch", p, failure.failing_cap)

    outcome, graph = run_repair_arm(
        g, replay=lambda gr, mb=(): world.replay_from_base(gr), certify=world.certify,
        agent=_FixesMysteryAgent(), log=_NullLog())
    assert outcome == "DONE"
    assert graph.get("syslib:mystery").state is State.SATISFIED


def test_previously_satisfied_node_that_gets_revoked_is_relocalized():
    """§9 certification revocation: 'a later patch breaks an earlier node -> certify_all
    flips it back to MISSING -> it becomes the next localized error.' Even a node that
    STARTS SATISFIED must be re-verified by host certify every cycle — the loop must
    never trust a stale/prior SATISFIED state over what the CURRENT certify reports."""
    g = DepGraph().with_node(Node(id="syslib:a", type=NodeType.SYSTEM_LIB, name="a",
                                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                                  state=State.SATISFIED,             # starts SATISFIED
                                  check_command="test -f /a"))

    def revoking_certify(gr):
        node = gr.get("syslib:a")
        return gr.with_node(replace(node, state=State.MISSING))     # host re-check demotes it

    localized = []

    class _RecordingNoFixAgent:
        def next_action(self, session, failure, log):
            localized.append(failure.failing_node)
            return ("patch", PatchProposal(), failure.failing_cap)

    outcome, _ = run_repair_arm(g, replay=lambda gr, mb=(): ReplayResult(True),
                                certify=revoking_certify, agent=_RecordingNoFixAgent(),
                                log=_NullLog(), max_errors=1)
    assert outcome != "DONE"
    assert localized == ["syslib:a"]
