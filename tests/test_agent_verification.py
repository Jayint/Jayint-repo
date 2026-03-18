import unittest

from agent import DockerAgent
from src.synthesizer import Synthesizer


class AgentVerificationAggregationTests(unittest.TestCase):
    def _make_agent(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.synthesizer = Synthesizer()
        agent.successful_test_commands = []
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.test_run_attempts = []
        agent._environment_revision = 0
        agent._current_verification_group = []
        return agent

    def test_aggregates_final_contiguous_verification_block(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "pip install -e .", "Successfully installed package")
        agent._record_successful_action(2, "pytest tests/unit", "collected 2 items\n2 passed")
        agent._record_successful_action(3, "cat README.md", "project docs")
        agent._record_successful_action(4, "pytest tests/integration", "collected 3 items\n3 passed")

        self.assertEqual(
            agent.verified_test_commands,
            ["pytest tests/unit", "pytest tests/integration"],
        )
        self.assertEqual(agent.verified_test_command, "pytest tests/integration")

    def test_environment_mutation_invalidates_previous_verification_block(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "pytest tests/unit", "collected 2 items\n2 passed")
        agent._record_successful_action(2, "pip install extra-package", "Successfully installed extra-package")

        self.assertEqual(agent.verified_test_commands, [])
        self.assertIsNone(agent.verified_test_command)

    def test_non_mutating_smoke_check_preserves_verification_block(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "pytest tests/unit", "collected 2 items\n2 passed")
        agent._record_successful_action(2, 'python -c "print(\\"ok\\")"', "ok")

        self.assertEqual(agent.verified_test_commands, ["pytest tests/unit"])
        self.assertEqual(agent.verified_test_command, "pytest tests/unit")


if __name__ == "__main__":
    unittest.main()
