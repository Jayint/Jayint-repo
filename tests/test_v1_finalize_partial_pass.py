"""Fix 3 Tier B — v1 finalize (_resolve_v1_verified_test_run) majority-pass acceptance.

Path 3 (active re-run in the still-live container) accepts a run when the MAJORITY of
tests passed (pass-ratio >= MIN_PASS_RATIO) and rejects 0-passed / collect-only /
sub-majority runs. A partial pass is recorded in the ledger with the REAL rc (1), so it
can never later masquerade as an rc==0 clean pass (Fix 3 m5).

NOTE: failure-CAUSE diagnosis (env-defect vs source bug) is intentionally NOT gated here
yet. A broken-env run at a high pass-ratio (numpy ABI break, DB down) is currently
ACCEPTED. The honest-diagnosis upgrade and its 19 audit cases are specified in
docs/superpowers/plans/FUTURE-tier-b-honest-failure-diagnosis.md -- the
`*_accepted_until_diagnosis_gate` tests below pin the current (deferred) behaviour so the
future change is a visible, intentional flip.
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


class V1FinalizeMajorityPassTests(unittest.TestCase):
    # -- accepted: genuine majority pass -------------------------------------
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

    def test_high_majority_accepted(self):
        a = _make_v1_agent(ok=False, out="==== 686 passed, 3 failed in 12.0s ====")
        self.assertEqual(a._resolve_v1_verified_test_run(done_flag=False), VERIFY_TEST_CMD)

    # -- rejected: real floors (NOT a majority pass) -------------------------
    def test_zero_passed_rejected(self):
        a = _make_v1_agent(ok=False, out="==== 5 failed, 0 passed in 1.0s ====")
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))
        self.assertEqual(a.action_ledger.events(), [])

    def test_collect_only_rejected(self):
        a = _make_v1_agent(ok=True, out="150 tests collected in 1.3s")
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))

    def test_sub_majority_rejected(self):
        a = _make_v1_agent(ok=False, out="==== 26 passed, 33 failed in 5.0s ====")
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))

    # -- DEFERRED: env-broken runs currently accepted at a high pass-ratio ----
    # These pin the intentional gap. When the honest-diagnosis gate lands (see the
    # FUTURE-* doc) they must flip to assertIsNone.
    def test_import_error_accepted_until_diagnosis_gate(self):
        a = _make_v1_agent(
            ok=False,
            out="100 passed, 2 failed\nModuleNotFoundError: No module named 'fastapi'",
        )
        self.assertEqual(a._resolve_v1_verified_test_run(done_flag=False), VERIFY_TEST_CMD)

    def test_db_down_accepted_until_diagnosis_gate(self):
        a = _make_v1_agent(
            ok=False,
            out="200 passed, 5 failed\nsqlalchemy.exc.OperationalError: could not connect to server",
        )
        self.assertEqual(a._resolve_v1_verified_test_run(done_flag=False), VERIFY_TEST_CMD)


if __name__ == "__main__":
    unittest.main()
