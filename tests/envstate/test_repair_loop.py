import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.patch import PatchProposal, ProviderSpec
from python_deps.depgraph.block import Block
from src.envstate.repair_loop import (
    CandidateTransactionOutcome,
    run_structured_repair,
)
from python_deps.depgraph.evidence_log import Evidence, EvidenceBundle
from src.envstate.repair_scope import RepairScope
from src.envstate.agent_action import AbstainAction

def _scope_builder(graph, *, target_node_id, failed_block, bundle, known_invalid, constraints):
    # deterministic fake scope; carries the avoid-list so the memory test can observe it
    return RepairScope(target_node_id, "apt-get install -y libplacebodev", "not found",
                       (), tuple(known_invalid), (), frozenset({"ev.1.0"}))

_GOOD = PatchProposal(add_providers=(ProviderSpec(
    id="apt:libplacebo-dev", kind="apt", command="apt-get install -y libplacebo-dev",
    provides=("syslib:libplacebo",), override=True),))

class _Graph:  # minimal stand-in: admit_proposal is monkeypatched in these tests
    pass

def test_recovers_when_emit_passes_after_patch(monkeypatch):
    import src.envstate.repair_loop as rl
    monkeypatch.setattr(rl, "admit_proposal", lambda g, p, **k:
        type("R", (), {"accepted": True, "errors": (), "graph": g, "manual_blocks": ()})())
    monkeypatch.setattr(rl, "compose_script", lambda g, mb: ())
    emitted = {"n": 0}
    def emit(g, mb):
        emitted["n"] += 1
        return g, object(), (None if emitted["n"] >= 1 else "system.libplacebo")
    out = run_structured_repair(_Graph(), "system.libplacebo", object(), 1,
                                propose=lambda s, **k: _GOOD, emit=emit,
                                scope_builder=_scope_builder, max_repairs=5, repair_budget=10)
    assert out.still_failing_id is None and out.turns_spent == 1

def test_budget_exhaustion(monkeypatch):
    import src.envstate.repair_loop as rl
    monkeypatch.setattr(rl, "compose_script", lambda g, mb: ())
    out = run_structured_repair(_Graph(), "system.x", object(), 1,
                                propose=lambda s, **k: None, emit=lambda g, mb: (g, object(), "system.x"),
                                scope_builder=_scope_builder, max_repairs=5, repair_budget=0)
    assert out.budget_exhausted is True and out.still_failing_id == "system.x"

def test_known_invalid_grows_and_convergence_guard(monkeypatch):
    import src.envstate.repair_loop as rl
    monkeypatch.setattr(rl, "admit_proposal", lambda g, p, **k:
        type("R", (), {"accepted": True, "errors": (), "graph": g, "manual_blocks": ()})())
    monkeypatch.setattr(rl, "compose_script", lambda g, mb: ())
    out = run_structured_repair(_Graph(), "system.x", object(), 1,
        propose=lambda s, **k: _GOOD,
        emit=lambda g, mb: (g, object(), "system.x"),   # same command keeps failing
        scope_builder=_scope_builder, max_repairs=5, repair_budget=10)
    # convergence guard stops re-attempting the identical failing command (does not burn all 5)
    assert out.still_failing_id == "system.x"
    assert "apt-get install -y libplacebodev" in out.known_invalid
    assert out.turns_spent <= 2

def test_gate_reject_then_reprompt_then_skip(monkeypatch):
    import src.envstate.repair_loop as rl
    calls = {"n": 0}
    def admit(g, p, **k):
        calls["n"] += 1
        return type("R", (), {"accepted": False, "errors": ("bad id",),
                              "graph": g, "manual_blocks": ()})()
    monkeypatch.setattr(rl, "admit_proposal", admit)
    monkeypatch.setattr(rl, "compose_script", lambda g, mb: ())
    seen = []
    def propose(s, **k):
        seen.append(k.get("rejection_errors"))
        return _GOOD
    out = run_structured_repair(_Graph(), "system.x", object(), 1, propose=propose,
        emit=lambda g, mb: (g, object(), "system.x"), scope_builder=_scope_builder,
        max_repairs=5, repair_budget=10)
    assert calls["n"] == 2                      # admit called twice (initial + re-prompt)
    assert seen[1] == ("bad id",)               # second propose got the gate errors
    assert out.still_failing_id == "system.x"


def test_gate_reject_then_abstain_is_host_reviewed_and_accepted(monkeypatch):
    import src.envstate.repair_loop as rl

    admitted = []
    monkeypatch.setattr(rl, "compose_script", lambda g, mb: ())

    def admit(graph, proposal, **kwargs):
        admitted.append(proposal)
        return type("R", (), {
            "accepted": False,
            "errors": ("provider lacks evidence",),
            "graph": graph,
            "manual_blocks": (),
        })()

    monkeypatch.setattr(rl, "admit_proposal", admit)
    responses = iter((
        _GOOD,
        AbstainAction("non_environment", "source failure", ("ev.1.0",)),
    ))
    reviewed = []

    out = run_structured_repair(
        _Graph(), "system.x", object(), 1,
        propose=lambda scope, **kwargs: next(responses),
        emit=lambda graph, blocks: (graph, object(), None),
        scope_builder=_scope_builder,
        review_abstain=lambda action, scope: (
            reviewed.append(action.reason) or True,
            "host confirmed source failure",
        ),
    )

    assert admitted == [_GOOD]
    assert reviewed == ["source failure"]
    assert out.turns_spent == 2
    assert out.still_failing_id == "system.x"
    assert not out.known_invalid


def test_gate_reject_then_abstain_host_rejection_stops_without_crash(monkeypatch):
    import src.envstate.repair_loop as rl

    admitted = []
    monkeypatch.setattr(rl, "compose_script", lambda g, mb: ())

    def admit(graph, proposal, **kwargs):
        admitted.append(proposal)
        return type("R", (), {
            "accepted": False,
            "errors": ("bad patch",),
            "graph": graph,
            "manual_blocks": (),
        })()

    monkeypatch.setattr(rl, "admit_proposal", admit)
    responses = iter((
        _GOOD,
        AbstainAction("non_environment", "not actually source", ("ev.1.0",)),
    ))

    out = run_structured_repair(
        _Graph(), "system.x", object(), 1,
        propose=lambda scope, **kwargs: next(responses),
        emit=lambda graph, blocks: (graph, object(), None),
        scope_builder=_scope_builder,
        review_abstain=lambda action, scope: (False, "still environment-shaped"),
    )

    assert admitted == [_GOOD]
    assert out.turns_spent == 2
    assert out.still_failing_id == "system.x"
    assert "apt-get install -y libplacebodev" in out.known_invalid


def test_injected_execution_plan_supplies_the_exact_failed_block():
    block = Block(
        block_id="pip.demo",
        wave="pip",
        commands=("install-demo",),
        target_node_ids=("pkg:demo",),
        check_commands=("check demo",),
    )
    seen = {}

    def scope_builder(graph, *, target_node_id, failed_block, **kwargs):
        seen["target"] = target_node_id
        seen["block"] = failed_block
        return RepairScope(
            target_node_id, "install-demo", "failed", (), (), (), frozenset()
        )

    run_structured_repair(
        _Graph(),
        "pkg:demo",
        object(),
        1,
        propose=lambda scope, **kwargs: None,
        emit=lambda graph, blocks: (graph, None, None),
        scope_builder=scope_builder,
        plan_builder=lambda graph, blocks: (block,),
        target_hint="pkg:demo",
    )

    assert seen == {"target": "pkg:demo", "block": block}


def test_agent_abstain_cannot_bypass_host_review(monkeypatch):
    import src.envstate.repair_loop as rl

    monkeypatch.setattr(rl, "admit_proposal", lambda g, p, **k:
        type("R", (), {"accepted": True, "errors": (), "graph": g,
                       "manual_blocks": ()})())
    monkeypatch.setattr(rl, "compose_script", lambda g, mb: ())
    responses = iter((
        AbstainAction("non_environment", "source bug", ("ev.1.0",)),
        _GOOD,
    ))
    reviews = []

    out = run_structured_repair(
        _Graph(),
        "system.libplacebo",
        object(),
        1,
        propose=lambda scope, **kwargs: next(responses),
        emit=lambda graph, blocks: (graph, None, None),
        scope_builder=_scope_builder,
        review_abstain=lambda action, scope: (
            reviews.append(action.reason) or False,
            "Host diagnosis remains environment",
        ),
    )

    assert reviews == ["source bug"]
    assert out.still_failing_id is None
    assert out.turns_spent == 2


def test_aborted_candidate_keeps_official_graph_and_returns_failure_evidence(monkeypatch):
    import src.envstate.repair_loop as rl

    official = _Graph()
    candidate = _Graph()
    monkeypatch.setattr(rl, "admit_proposal", lambda g, p, **k:
        type("R", (), {"accepted": True, "errors": (), "graph": candidate,
                       "manual_blocks": ("candidate-block",)})())
    monkeypatch.setattr(rl, "compose_script", lambda g, mb: ())
    seen_outputs = []

    def scope_builder(graph, *, bundle, **kwargs):
        assert graph is official
        items = getattr(bundle, "items", ())
        seen_outputs.append(items[-1].output_excerpt if items else "")
        return RepairScope(
            "syslib:libplacebo", "bad-provider", seen_outputs[-1], (), (), (),
            frozenset(item.evidence_id for item in items),
        )

    candidate_bundle = EvidenceBundle().with_item(Evidence(
        evidence_id="candidate.txn.fail",
        container_kind="candidate_transaction",
        command="bad-provider",
        rc=1,
        output_excerpt="candidate failed without touching official state",
        cycle=1,
        node_id="syslib:libplacebo",
        block_id="system.libplacebo",
    ))

    out = run_structured_repair(
        official,
        "system.libplacebo",
        EvidenceBundle(),
        1,
        propose=lambda scope, **kwargs: _GOOD,
        emit=lambda graph, blocks: (_ for _ in ()).throw(
            AssertionError("aborted candidates must never reach official emit")
        ),
        scope_builder=scope_builder,
        max_repairs=2,
        validate_candidate=lambda *args, transaction_id, **kwargs:
            CandidateTransactionOutcome(
                False,
                official,
                (),
                candidate_bundle,
                "system.libplacebo",
                transaction_id,
            ),
    )

    assert out.graph is official
    assert out.manual_blocks == ()
    assert seen_outputs[-1] == "candidate failed without touching official state"
