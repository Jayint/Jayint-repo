"""Fix 3 Tier B — verification bundle Filter 2 majority-pass acceptance.

A partial-pass run (forced rc==0 in-agent, e.g. `pytest || true`, so it is a recorded
successful_action) finalises when the MAJORITY of tests passed (pass-ratio >=
MIN_PASS_RATIO). 0-passed, collect-only, truncation, and sub-majority pass-rates are
dropped.

NOTE: failure-CAUSE diagnosis (env-defect vs source bug) is intentionally NOT gated yet;
an env-broken run at a high pass-ratio is currently ACCEPTED. See
docs/superpowers/plans/FUTURE-tier-b-honest-failure-diagnosis.md.
"""
from __future__ import annotations
import unittest

from src.synthesizer import Synthesizer
from src.verification_bundle import derive_supported_verification_bundle


def _summary(command, observation):
    """A single recorded successful action with no env-mutation following it."""
    return {
        "verification_bundle": {"runtime_preparation_commands": [], "test_commands": [command]},
        "successful_actions": [
            {
                "step_index": 1,
                "command": command,
                "observation": observation,
                "environment_revision": 1,
                "mutates_environment": False,
            }
        ],
    }


class VerificationBundleMajorityPassTests(unittest.TestCase):
    def setUp(self):
        self.synth = Synthesizer()

    def _bundle(self, command, observation):
        return derive_supported_verification_bundle(
            _summary(command, observation), synthesizer=self.synth
        )["test_commands"]

    # -- accepted: genuine majority pass --------------------------------------
    def test_majority_pass_with_assertion_failures_accepted(self):
        out = "==== 1601 passed, 2 failed in 30s ====\nAssertionError: assert 1 == 2"
        self.assertEqual(self._bundle("python -m pytest -q", out), ["python -m pytest -q"])

    def test_near_perfect_ratio_accepted(self):
        out = "==== 686 passed, 3 failed in 12s ===="
        self.assertEqual(self._bundle("python -m pytest -q", out), ["python -m pytest -q"])

    # -- rejected: real floors (NOT a majority pass) --------------------------
    def test_zero_passed_only_failures_rejected(self):
        out = "==== 5 failed in 2s ===="
        self.assertEqual(self._bundle("python -m pytest -q", out), [])

    def test_sub_majority_rejected(self):
        out = "==== 26 passed, 33 failed in 5s ===="
        self.assertEqual(self._bundle("python -m pytest -q", out), [])

    def test_tiny_pass_fraction_rejected(self):
        out = "==== 3 passed, 997 failed in 40s ===="
        self.assertEqual(self._bundle("python -m pytest -q", out), [])

    def test_truncated_output_rejected_even_with_passes(self):
        out = "==== 1601 passed, 2 failed in 30s ===="
        self.assertEqual(self._bundle("python -m pytest -q 2>&1 | head -100", out), [])

    # -- DEFERRED: env-broken runs currently accepted at a high pass-ratio -----
    # Pins the intentional gap; flips to [] when the honest-diagnosis gate lands.
    def test_module_not_found_accepted_until_diagnosis_gate(self):
        out = "100 passed, 2 failed\nModuleNotFoundError: No module named 'fastapi'"
        self.assertEqual(self._bundle("python -m pytest -q", out), ["python -m pytest -q"])

    def test_connection_refused_accepted_until_diagnosis_gate(self):
        out = "200 passed, 5 failed\nConnectionRefusedError: [Errno 111]"
        self.assertEqual(self._bundle("python -m pytest -q", out), ["python -m pytest -q"])


if __name__ == "__main__":
    unittest.main()
