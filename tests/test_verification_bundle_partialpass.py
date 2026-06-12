"""Fix 3 Tier B — verification bundle Filter 2 partial-pass acceptance.

A partial-pass run (forced rc==0 in-agent, e.g. `pytest || true`, so it is a
recorded successful_action) finalises ONLY when the majority of tests passed and
the remaining failures are non-environment. Env-defects, ambiguous 'N error',
0-passed, truncation, and sub-majority pass-rates are all dropped.
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


class VerificationBundlePartialPassTests(unittest.TestCase):
    def setUp(self):
        self.synth = Synthesizer()

    def _bundle(self, command, observation):
        return derive_supported_verification_bundle(
            _summary(command, observation), synthesizer=self.synth
        )["test_commands"]

    # -- accepted --------------------------------------------------------------
    def test_majority_pass_with_assertion_failures_accepted(self):
        out = "==== 1601 passed, 2 failed in 30s ====\nAssertionError: assert 1 == 2"
        self.assertEqual(self._bundle("python -m pytest -q", out), ["python -m pytest -q"])

    def test_near_perfect_ratio_accepted(self):
        out = "==== 686 passed, 3 failed in 12s ===="
        self.assertEqual(self._bundle("python -m pytest -q", out), ["python -m pytest -q"])

    # -- env-defect: rejected --------------------------------------------------
    def test_module_not_found_rejected(self):
        out = "100 passed, 2 failed\nModuleNotFoundError: No module named 'fastapi'"
        self.assertEqual(self._bundle("python -m pytest -q", out), [])

    def test_error_collecting_rejected(self):
        out = "50 passed\nERROR collecting tests/foo.py"
        self.assertEqual(self._bundle("python -m pytest -q", out), [])

    def test_connection_refused_rejected(self):
        out = "200 passed, 5 failed\nConnectionRefusedError: [Errno 111]"
        self.assertEqual(self._bundle("python -m pytest -q", out), [])

    # -- hollow / ambiguous / sub-majority: rejected ---------------------------
    def test_zero_passed_only_failures_rejected(self):
        out = "==== 5 failed in 2s ===="
        self.assertEqual(self._bundle("python -m pytest -q", out), [])

    def test_bare_n_error_rejected(self):
        # §5.7: 'N error' (pytest collection/setup category) is treated conservatively.
        out = "collected 32 items\n==== 32 passed, 1 error in 0.31s ===="
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


if __name__ == "__main__":
    unittest.main()
