# tests/test_v1_finalize_gate.py
"""Design §2: a v1 run succeeds ONLY when a genuine test execution gate actually
passes — never on the Planner's say-so. The finalize path must not fabricate a
verified command; when the gate wasn't reached in-loop it actively runs
`python -m pytest -q` in the live container and only counts success if the
output shows a real execution pass (N passed, no failures).

Phase 1: changed from collect-only semantics to execution semantics.
"""
import types

import agent as agent_mod
from src.envstate.ledger import ActionLedger, ActionEvent
from src.envstate.orchestrator import COLLECT_ONLY_CMD, VERIFY_TEST_CMD
from src.synthesizer import Synthesizer


def _agent(sandbox_execute):
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    a.action_ledger = ActionLedger()
    a.env_container_id = "c"
    a.sandbox = types.SimpleNamespace(execute=sandbox_execute)
    a.synthesizer = Synthesizer()  # Tier B rc!=0 branch uses self.synthesizer
    return a


def test_uses_real_verified_run_from_ledger():
    """A genuine rc-0 execution already in the ActionLedger -> use that exact
    command, no sandbox call needed."""
    a = _agent(lambda c: (_ for _ in ()).throw(AssertionError("sandbox must not run")))
    a.action_ledger.append(
        ActionEvent(step=1, cmd="python -m pytest -q", rc=0, stdout="8 passed in 0.5s"))
    result = a._resolve_v1_verified_test_run(done_flag=False)
    assert result == "python -m pytest -q"


def test_actively_runs_when_gate_not_reached_and_passes():
    """Planner declared done on deps alone; gate never ran -> actively verify, it passes."""
    calls = []
    def ex(cmd):
        calls.append(cmd)
        return (True, "7 passed in 0.3s")
    a = _agent(ex)
    res = a._resolve_v1_verified_test_run(done_flag=False)
    assert res == VERIFY_TEST_CMD
    assert len(calls) >= 1                                                  # actively verified
    assert any(e.rc == 0 and "pytest" in e.cmd for e in a.action_ledger.events())  # recorded


def test_returns_none_when_active_run_fails():
    """Active run FAILS -> no success, and NOTHING fabricated/recorded."""
    a = _agent(lambda c: (False, "ERROR: ModuleNotFoundError: microsearch"))
    assert a._resolve_v1_verified_test_run(done_flag=False) is None
    assert a.action_ledger.events() == ()


def test_returns_none_when_active_run_is_collect_only_output():
    """Active run returns only collected output (no 'passed') -> not a valid execution."""
    a = _agent(lambda c: (True, "collected 7 items"))
    assert a._resolve_v1_verified_test_run(done_flag=False) is None


def test_honors_done_flag_without_re_running():
    """Gate already fired structurally in-loop -> trust it, don't re-run the container."""
    calls = []
    a = _agent(lambda c: (calls.append(c), (False, "x"))[1])
    result = a._resolve_v1_verified_test_run(done_flag=True)
    assert result == VERIFY_TEST_CMD
    assert calls == []
