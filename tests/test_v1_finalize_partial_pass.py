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
    # __new__ bypasses __init__, so the A2 in-build signal attrs don't exist yet.
    # Initialise them to the same honest defaults __init__ uses.
    a.in_build_pass_rate = None
    a.in_build_passed_ge1 = False
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

    def test_n_error_collection_setup_rejected(self):
        # The narrow 'N error' (pytest collection/setup) guard stays on the v1 Tier B path.
        a = _make_v1_agent(ok=False, out="==== 100 passed, 2 failed, 1 error in 5s ====")
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


class V1InBuildPassSignalTests(unittest.TestCase):
    """A2: the REAL in-sandbox pass signal (in_build_pass_rate / in_build_passed_ge1)
    is recorded ONLY from a genuine test-run output that fired the gate, never
    fabricated. A rejected run leaves both fields at their honest defaults."""

    def test_clean_pass_records_ratio_1_and_ge1(self):
        a = _make_v1_agent(ok=True, out="==== 50 passed in 3.2s ====")
        a._resolve_v1_verified_test_run(done_flag=False)
        self.assertEqual(a.in_build_pass_rate, 1.0)
        self.assertIs(a.in_build_passed_ge1, True)

    def test_majority_pass_records_real_ratio(self):
        a = _make_v1_agent(ok=False, out="==== 1601 passed, 2 failed in 30.1s ====")
        a._resolve_v1_verified_test_run(done_flag=False)
        self.assertIs(a.in_build_passed_ge1, True)
        self.assertIsNotNone(a.in_build_pass_rate)
        self.assertLess(abs(a.in_build_pass_rate - 1601 / 1603), 1e-3)

    def test_rejected_run_leaves_signal_unset(self):
        # honesty: a 0-passed run is rejected and NOTHING is recorded.
        a = _make_v1_agent(ok=False, out="==== 5 failed, 0 passed in 1.0s ====")
        self.assertIsNone(a._resolve_v1_verified_test_run(done_flag=False))
        self.assertIsNone(a.in_build_pass_rate)
        self.assertIs(a.in_build_passed_ge1, False)

    def test_all_skipped_path3_rejected_and_no_fake_signal(self):
        # rc==0 all-skipped run reaching Path 3 must be REJECTED and must NOT
        # stamp a pass signal (honesty invariant; zero tests passed).
        a = _make_v1_agent(ok=True, out="tests/x.py sssss [100%]\n\n===== 5 skipped in 0.10s =====\n")
        assert a._resolve_v1_verified_test_run(done_flag=False) is None
        assert a.in_build_pass_rate is None
        assert a.in_build_passed_ge1 is False

    def test_done_flag_with_passing_ledger_event_records_signal_no_rerun(self):
        # done_flag fired in-loop: recover the real pass signal from the ledger
        # (no sandbox re-run). A genuine rc-0 passing event is caught by the
        # ledger scan and its signal recorded.
        from src.envstate.ledger import ActionEvent

        a = _make_v1_agent(ok=False, out="SANDBOX MUST NOT RUN")
        a.action_ledger.append(
            ActionEvent(step=1, cmd="python -m pytest -q", rc=0,
                        stdout="==== 50 passed in 3.2s ===="))
        result = a._resolve_v1_verified_test_run(done_flag=True)
        self.assertIn(result, ("python -m pytest -q", VERIFY_TEST_CMD))
        self.assertEqual(a.sandbox.calls, [])  # never re-ran the container
        self.assertEqual(a.in_build_pass_rate, 1.0)
        self.assertIs(a.in_build_passed_ge1, True)

    def test_done_flag_without_passing_event_leaves_signal_unset_no_rerun(self):
        # done_flag set but no genuine passing run in the ledger -> honest:
        # the signal stays unset and we do NOT re-run the sandbox to fabricate one.
        a = _make_v1_agent(ok=True, out="SANDBOX MUST NOT RUN")
        result = a._resolve_v1_verified_test_run(done_flag=True)
        self.assertEqual(result, VERIFY_TEST_CMD)
        self.assertEqual(a.sandbox.calls, [])
        self.assertIsNone(a.in_build_pass_rate)
        self.assertIs(a.in_build_passed_ge1, False)


if __name__ == "__main__":
    unittest.main()
