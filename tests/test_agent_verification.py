import os
import tempfile
import unittest
from types import SimpleNamespace

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
        agent.required_local_services = set()
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

    def test_accepts_agent_reported_bundle_without_system_level_test_validation(self):
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

        self.assertTrue(accepted)
        self.assertEqual(agent.verified_test_commands, ["make all"])
        self.assertEqual(agent.verification_source, "agent_report")

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

    def test_allows_safe_xargs_search_between_test_commands(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "pytest tests/unit", "collected 2 items\n2 passed")
        agent._record_successful_action(
            2,
            'find src/test -name "*Test.java" | xargs grep -L "@SpringBootTest" | head -10',
            "src/test/java/example/FooTest.java",
        )
        agent._record_successful_action(3, "pytest tests/integration", "collected 3 items\n3 passed")

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Final verification passed.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": [], "test_commands": ["pytest tests/unit", "pytest tests/integration"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertTrue(accepted)
        self.assertEqual(
            agent.verified_test_commands,
            ["pytest tests/unit", "pytest tests/integration"],
        )

    def test_preserves_reported_runtime_bundle_commands_without_system_filtering(self):
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
        self.assertEqual(
            agent.verified_runtime_preparation_commands,
            [
                "apt-get update && apt-get install -y git zip unzip",
                "curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer",
                "composer install --no-progress --prefer-dist --optimize-autoloader",
            ],
        )
        self.assertEqual(agent.verified_test_commands, ["make all"])

class AgentRepositoryHintTests(unittest.TestCase):
    def test_collect_maven_repository_hints_extracts_unique_repository_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root_pom = os.path.join(tmpdir, "pom.xml")
            nested_dir = os.path.join(tmpdir, "openbas-model")
            os.makedirs(nested_dir)
            nested_pom = os.path.join(nested_dir, "pom.xml")

            with open(root_pom, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    """
<project>
  <repositories>
    <repository>
      <id>shibboleth_repository</id>
      <url>https://build.shibboleth.net/nexus/content/repositories/releases/</url>
    </repository>
  </repositories>
</project>
""".strip()
                )

            with open(nested_pom, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    """
<project>
  <repositories>
    <repository>
      <id>shibboleth_repository</id>
      <url>https://build.shibboleth.net/nexus/content/repositories/releases/</url>
    </repository>
    <repository>
      <id>ossrh-snapshots</id>
      <url>https://s01.oss.sonatype.org/content/repositories/snapshots/</url>
    </repository>
  </repositories>
</project>
""".strip()
                )

            agent = DockerAgent.__new__(DockerAgent)
            agent.workplace = tmpdir

            hints = agent._collect_maven_repository_hints()

            self.assertIn("shibboleth_repository", hints)
            self.assertIn("ossrh-snapshots", hints)
            self.assertEqual(hints.count("shibboleth_repository"), 1)

    def test_collect_local_service_hints_detects_explicit_service_dependencies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resources_dir = os.path.join(tmpdir, "src", "test", "resources")
            os.makedirs(resources_dir)
            config_path = os.path.join(resources_dir, "application.properties")

            with open(config_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "\n".join(
                        [
                            "spring.datasource.url=jdbc:postgresql://localhost:5433/openbas",
                            "spring.elasticsearch.uris=http://localhost:9200",
                            "minio.url=http://localhost:10000",
                        ]
                    )
                )

            agent = DockerAgent.__new__(DockerAgent)
            agent.workplace = tmpdir

            hints = agent._collect_local_service_hints()

            self.assertEqual(hints, {"postgresql", "elasticsearch", "minio"})


class FakePlannerForRun:
    def __init__(self):
        self.calls = []
        self.responses = [
            (
                "Need to recover the last stable state.",
                "__ROLLBACK__",
                "Thought: Need to recover the last stable state.\nAction: __ROLLBACK__",
                False,
                {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
            (
                "Stopping here.",
                None,
                "Thought: Stopping here.\nFinal Answer: Failure",
                True,
                {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
            ),
        ]

    def plan(self, repo_url=None, last_observation=None, manage_history=True):
        self.calls.append(
            {
                "repo_url": repo_url,
                "last_observation": last_observation,
                "manage_history": manage_history,
            }
        )
        return self.responses.pop(0)

    def extract_final_answer(self, content):
        if "Final Answer: Failure" in content:
            return "Failure"
        if "Final Answer: Success" in content:
            return "Success"
        return None


class FakeSandboxForRun:
    def __init__(self):
        self.rollback_calls = []
        self.execute_calls = []
        self.close_calls = []

    def rollback(self, reason="agent_requested"):
        self.rollback_calls.append(reason)
        return True, "[SYSTEM] Restored the container to the last successful snapshot."

    def execute(self, action):
        self.execute_calls.append(action)
        return True, ""

    def close(self, keep_alive=False):
        self.close_calls.append(keep_alive)


class FakeLedger:
    def __init__(self):
        self.calls = []

    def add(self, bucket_name, input_tokens, output_tokens):
        self.calls.append((bucket_name, input_tokens, output_tokens))


class AgentRollbackActionTests(unittest.TestCase):
    def test_run_uses_explicit_rollback_action_without_shell_execution(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.repo_url = "https://github.com/example/repo.git"
        agent.enable_observation_compression = False
        agent.planner = FakePlannerForRun()
        agent.sandbox = FakeSandboxForRun()
        agent.synthesizer = SimpleNamespace(
            record_success=lambda command: None,
            command_mutates_environment=lambda command: False,
            generate_dockerfile=lambda file_path: None,
        )
        agent.run_token_ledger = FakeLedger()
        agent._prepare_observation_for_prompt = lambda observation: observation
        agent._record_successful_action = lambda step_id, action, observation: None
        agent._write_run_summary = lambda configuration_success, run_error: None
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.verified_runtime_preparation_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
        agent._environment_revision = 0
        agent._current_verification_group = []
        agent.agent_steps = []
        agent.compression_stats = {"candidate_steps": 0, "compressed_steps": 0, "saved_tokens_est": 0}
        agent.observation_compressor = None
        agent.platform_override = None
        agent.workplace = tempfile.mkdtemp()

        agent.run(max_steps=2, keep_container=False)

        self.assertEqual(agent.sandbox.rollback_calls, ["agent_requested"])
        self.assertEqual(agent.sandbox.execute_calls, [])
        self.assertEqual(
            agent.planner.calls[1]["last_observation"],
            "[SYSTEM] Restored the container to the last successful snapshot.",
        )
        self.assertEqual(agent.sandbox.close_calls, [False])


if __name__ == "__main__":
    unittest.main()
