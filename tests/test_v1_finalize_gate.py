# tests/test_v1_finalize_gate.py
"""Design §2: a v1 run succeeds ONLY when the `pytest --collect-only` gate
actually exits 0 — never on the Planner's say-so. The finalize path must not
fabricate a verified command; when the gate wasn't reached in-loop it actively
runs collect-only in the live container and only counts success if it passes.
"""
import types

import agent as agent_mod
from src.envstate.ledger import ActionLedger, ActionEvent
from src.envstate.orchestrator import COLLECT_ONLY_CMD


def _agent(sandbox_execute):
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    a.action_ledger = ActionLedger()
    a.env_container_id = "c"
    a.sandbox = types.SimpleNamespace(execute=sandbox_execute)
    return a


def test_uses_real_collect_only_from_ledger():
    # a genuine rc-0 collect-only ran in-loop -> use that exact command, no sandbox call
    a = _agent(lambda c: (_ for _ in ()).throw(AssertionError("sandbox must not run")))
    a.action_ledger.append(
        ActionEvent(step=1, cmd="pytest --collect-only -q tests/", rc=0, stdout="collected 5"))
    assert a._resolve_v1_verified_collect_only(done_flag=False) == "pytest --collect-only -q tests/"


def test_actively_runs_when_gate_not_reached_and_passes():
    # Planner declared done on deps alone; gate never ran -> actively verify, it passes
    calls = []
    def ex(cmd):
        calls.append(cmd)
        return (True, "collected 7 items / 7 selected")
    a = _agent(ex)
    res = a._resolve_v1_verified_collect_only(done_flag=False)
    assert res == COLLECT_ONLY_CMD
    assert any("--collect-only" in c for c in calls)                       # actively verified
    assert any(e.rc == 0 and "--collect-only" in e.cmd for e in a.action_ledger.events())  # recorded real evidence


def test_returns_none_when_active_collect_only_fails():
    # gate actively run and FAILS -> no success, and NOTHING fabricated/recorded
    a = _agent(lambda c: (False, "ERROR: ModuleNotFoundError: microsearch"))
    assert a._resolve_v1_verified_collect_only(done_flag=False) is None
    assert a.action_ledger.events() == ()


def test_honors_done_flag_without_re_running():
    # gate already fired structurally in-loop -> trust it, don't re-run the container
    calls = []
    a = _agent(lambda c: (calls.append(c), (False, "x"))[1])
    assert a._resolve_v1_verified_collect_only(done_flag=True) == COLLECT_ONLY_CMD
    assert calls == []
