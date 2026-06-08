import unittest

from src.synthesizer import Synthesizer
from src.verification_bundle import derive_supported_verification_bundle


def _action(step, command, observation, revision, mutates):
    return {
        "step_index": step,
        "command": command,
        "observation": observation,
        "environment_revision": revision,
        "mutates_environment": mutates,
    }


class VerificationBundleRevisionTests(unittest.TestCase):
    def setUp(self):
        self.synth = Synthesizer()

    def test_rejects_test_command_made_stale_by_later_mutation(self):
        # pytest passed at revision 1, then a mutating install advanced env to revision 2.
        run_summary = {
            "environment_revision": 2,
            "verification_bundle": {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest -q"],
            },
            "successful_actions": [
                _action(1, "pytest -q", "collected 3 items\n3 passed", revision=1, mutates=False),
                _action(2, "pip install extra-pkg", "Successfully installed extra-pkg", revision=2, mutates=True),
            ],
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=self.synth)
        self.assertEqual(bundle["test_commands"], [])

    def test_accepts_test_command_at_final_revision(self):
        run_summary = {
            "environment_revision": 1,
            "verification_bundle": {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest -q"],
            },
            "successful_actions": [
                _action(1, "pip install -e .", "Successfully installed pkg", revision=1, mutates=True),
                _action(2, "pytest -q", "collected 3 items\n3 passed", revision=1, mutates=False),
            ],
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=self.synth)
        self.assertEqual(bundle["test_commands"], ["pytest -q"])

    def test_fallback_does_not_promote_stale_last_test(self):
        # No reported test matches; the only observed test is stale -> must NOT be promoted.
        run_summary = {
            "environment_revision": 2,
            "verification_bundle": {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest tests/does_not_match"],
            },
            "successful_actions": [
                _action(1, "pytest -q", "collected 3 items\n3 passed", revision=1, mutates=False),
                _action(2, "pip install extra-pkg", "Successfully installed extra-pkg", revision=2, mutates=True),
            ],
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=self.synth)
        self.assertEqual(bundle["test_commands"], [])


class VerificationBundleLiveStampingTests(unittest.TestCase):
    """Drive the REAL agent stamping (_record_successful_action) instead of hand-
    stamping `environment_revision`, so the gate is tested against how the agent
    actually records evidence (closes review finding: unit fixtures could pass even
    if live stamping were wrong)."""

    def _make_agent(self):
        from agent import DockerAgent
        agent = DockerAgent.__new__(DockerAgent)
        agent.synthesizer = Synthesizer()
        agent.successful_test_commands = []
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.verified_runtime_preparation_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
        agent._environment_revision = 0
        agent._current_verification_group = []
        agent.required_local_services = set()
        agent.enable_envstate = False
        agent.action_ledger = None
        return agent

    def test_live_stamped_stale_test_is_rejected_by_gate(self):
        agent = self._make_agent()
        agent._record_successful_action(1, "pip install -e .", "Successfully installed pkg")
        agent._record_successful_action(2, "pytest -q", "collected 3 items\n3 passed")
        agent._record_successful_action(3, "pip install extra-pkg", "Successfully installed extra-pkg")
        run_summary = {
            "environment_revision": agent._environment_revision,
            "verification_bundle": {"runtime_preparation_commands": [], "test_commands": ["pytest -q"]},
            "successful_actions": agent.successful_actions,
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=Synthesizer())
        self.assertEqual(bundle["test_commands"], [])  # stale: a mutation ran after the test

    def test_live_stamped_current_test_is_accepted_by_gate(self):
        agent = self._make_agent()
        agent._record_successful_action(1, "pip install -e .", "Successfully installed pkg")
        agent._record_successful_action(2, "pytest -q", "collected 3 items\n3 passed")
        run_summary = {
            "environment_revision": agent._environment_revision,
            "verification_bundle": {"runtime_preparation_commands": [], "test_commands": ["pytest -q"]},
            "successful_actions": agent.successful_actions,
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=Synthesizer())
        self.assertEqual(bundle["test_commands"], ["pytest -q"])
