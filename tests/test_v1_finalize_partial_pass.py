"""Fix 3 Tier B — v1 finalize (_resolve_v1_verified_test_run) partial-pass acceptance.

Path 3 (active re-run in the still-live container) must accept a RepoLaunch
majority-pass run (>=1 passed, majority pass-ratio, non-env failures) and reject
0-passed / collect-only / env-defect / ambiguous-error / sub-majority runs. A
partial pass is recorded in the ledger with the REAL rc (1), so it can never later
masquerade as an rc==0 clean pass (Fix 3 m5).
"""
from __future__ import annotations
import unittest

from src.synthesizer import Synthesizer
from src.envstate.orchestrator import VERIFY_TEST_CMD


class _FakeLedger:
    def __init__(self):
        self._events = []

    def events(self):
        return list(self._events)

    def append(self, ev):
        self._events.append(ev)


class _FakeSandbox:
    def __init__(self, ok, out):
        self._ok, self._out = ok, out
        self.calls = []

    def execute(self, cmd):
        self.calls.append(cmd)
        return (self._ok, self._out)


def _make_v1_agent(ok, out):
    from agent import DockerAgent
    a = DockerAgent.__new__(DockerAgent)
    a.synthesizer = Synthesizer()
    a.sandbox = _FakeSandbox(ok, out)
    a.action_ledger = _FakeLedger()
    a.env_container_id = ""
    return a


class V1FinalizePartialPassTests(unittest.TestCase):
    def test_majority_pass_accepted_and_ledger_rc_is_one(self):
        a = _make_v1_agent(ok=False, out="==== 1601 passed, 2 failed in 30.1s ====")
        result = a._resolve_v1_verified_test_run(done_flag=False)
        self.assertEqual(result, VERIFY_TEST_CMD)
        events = a.action_ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].rc, 1)  # m5: partial pass records the REAL rc

    def test_clean_pass_accepted_and_ledger_rc_is_zero(self):
        a = _make_v1_agent(ok=True, out="==== 50 passed in 3.2s ====")
        result = a._resolve_v1_verified_test_run(done_flag=False)
        self.assertEqual(result, VERIFY_TEST_CMD)
        self.assertEqual(a.action_ledger.events()[0].rc, 0)

    def test_zero_passed_rejected(self):
        a = _make_v1_agent(ok=False, out="==== 5 failed, 0 passed in 1.0s ====")
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))
        self.assertEqual(a.action_ledger.events(), [])

    def test_collect_only_rejected(self):
        a = _make_v1_agent(ok=True, out="150 tests collected in 1.3s")
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))

    def test_import_error_rejected(self):
        a = _make_v1_agent(
            ok=False,
            out="100 passed, 2 failed\nModuleNotFoundError: No module named 'fastapi'",
        )
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))

    def test_connection_refused_rejected(self):
        a = _make_v1_agent(
            ok=False,
            out="200 passed, 5 failed\nConnectionRefusedError: [Errno 111] Connection refused",
        )
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))

    def test_ambiguous_error_rejected(self):
        a = _make_v1_agent(ok=False, out="==== 32 passed, 1 error in 0.31s ====")
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))

    def test_sub_majority_rejected(self):
        a = _make_v1_agent(ok=False, out="==== 26 passed, 33 failed in 5.0s ====")
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))

    def test_high_majority_accepted(self):
        a = _make_v1_agent(ok=False, out="==== 686 passed, 3 failed in 12.0s ====")
        self.assertEqual(a._resolve_v1_verified_test_run(done_flag=False), VERIFY_TEST_CMD)


if __name__ == "__main__":
    unittest.main()
