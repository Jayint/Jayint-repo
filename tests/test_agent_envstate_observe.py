import unittest

from agent import DockerAgent
from src.synthesizer import Synthesizer
from src.envstate.ledger import ActionLedger


class AgentEnvStateObserveTests(unittest.TestCase):
    def _make_agent(self, enable_envstate):
        agent = DockerAgent.__new__(DockerAgent)
        agent.synthesizer = Synthesizer()
        agent.successful_test_commands = []
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.verified_runtime_preparation_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.failed_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
        agent._environment_revision = 0
        agent._current_verification_group = []
        agent.required_local_services = set()
        agent.enable_envstate = enable_envstate
        agent.action_ledger = ActionLedger() if enable_envstate else None
        agent.env_container_id = "abc123"
        return agent

    def test_envstate_off_does_not_record_ledger(self):
        # Defense-in-depth: even with a real ledger attached, the OFF flag gates writes.
        agent = self._make_agent(enable_envstate=False)
        agent.action_ledger = ActionLedger()
        agent._record_successful_action(1, "pip install -e .", "Successfully installed pkg")
        agent._record_failed_action(2, "pip install badpkg", "ERROR: not found")
        self.assertEqual(agent.action_ledger.events(), ())

    def test_failed_action_records_rc1_event_when_on(self):
        agent = self._make_agent(enable_envstate=True)
        agent._record_failed_action(3, "pip install badpkg", "ERROR: could not find badpkg")
        event = agent.action_ledger.events()[-1]
        self.assertEqual(event.cmd, "pip install badpkg")
        self.assertEqual(event.rc, 1)
        self.assertEqual(event.env_revision_before, event.env_revision_after)
        self.assertIsNone(event.mutation_class)

    def test_envstate_on_appends_ordered_events(self):
        agent = self._make_agent(enable_envstate=True)
        agent._record_successful_action(1, "pip install -e .", "Successfully installed pkg")
        agent._record_successful_action(2, "pytest -q", "collected 2 items\n2 passed")
        events = agent.action_ledger.events()
        self.assertEqual([e.cmd for e in events], ["pip install -e .", "pytest -q"])
        # The mutating install advanced the revision; the test did not.
        self.assertEqual(events[0].mutation_class is not None, True)
        self.assertEqual(events[1].env_revision_after, events[1].env_revision_before)
