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
        agent.verified_runtime_preparation_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
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

    def test_accepts_agent_reported_wrapper_test_bundle(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "apt-get install -y redis-server", "Setting up redis-server")
        agent._record_successful_action(2, "redis-server --daemonize yes", "")
        agent._record_successful_action(3, "redis-cli ping", "PONG")
        agent._record_successful_action(
            4,
            "make all",
            "\n".join(
                [
                    "PHPUnit 9.6.34 by Sebastian Bergmann and contributors.",
                    "",
                    "OK (94 tests, 185 assertions)",
                ]
            ),
        )

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: The environment is fully configured.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": ["redis-server --daemonize yes"], "test_commands": ["make all"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertTrue(accepted)
        self.assertEqual(agent.verified_runtime_preparation_commands, ["redis-server --daemonize yes"])
        self.assertEqual(agent.verified_test_commands, ["make all"])
        self.assertEqual(agent.verified_test_command, "make all")
        self.assertEqual(agent.verification_source, "agent_report")

    def test_rejects_agent_reported_test_bundle_without_real_test_signal(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "make all", "Build complete.")

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Setup seems done.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": [], "test_commands": ["make all"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertFalse(accepted)
        self.assertEqual(agent.verified_test_commands, [])

    def test_allows_healthcheck_between_runtime_prep_and_test_bundle(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "redis-server --daemonize yes", "")
        agent._record_successful_action(2, "redis-cli ping", "PONG")
        agent._record_successful_action(3, "pytest tests", "collected 2 items\n2 passed")

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Redis is up and tests passed.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": ["redis-server --daemonize yes"], "test_commands": ["pytest tests"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertTrue(accepted)
        self.assertEqual(agent.verified_runtime_preparation_commands, ["redis-server --daemonize yes"])
        self.assertEqual(agent.verified_test_commands, ["pytest tests"])

    def test_drops_persistent_setup_from_runtime_bundle_but_keeps_valid_test_command(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "apt-get update && apt-get install -y git zip unzip", "installed")
        agent._record_successful_action(
            2,
            "curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer",
            "Composer successfully installed",
        )
        agent._record_successful_action(
            3,
            "composer install --no-progress --prefer-dist --optimize-autoloader",
            "Generating optimized autoload files",
        )
        agent._record_successful_action(4, "make --version", "GNU Make 4.4.1")
        agent._record_successful_action(
            5,
            "make all",
            "\n".join(
                [
                    "PHPUnit 9.6.34 by Sebastian Bergmann and contributors.",
                    "",
                    "OK (94 tests, 185 assertions)",
                ]
            ),
        )

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: The final tests passed.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": ["apt-get update && apt-get install -y git zip unzip", "curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer", "composer install --no-progress --prefer-dist --optimize-autoloader"], "test_commands": ["make all"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertTrue(accepted)
        self.assertEqual(agent.verified_runtime_preparation_commands, [])
        self.assertEqual(agent.verified_test_commands, ["make all"])


if __name__ == "__main__":
    unittest.main()
