import os
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

import agent as agent_module
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

    def test_create_sandbox_forwards_command_timeout(self):
        captured = {}

        class FakeSandbox:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        original_sandbox = agent_module.Sandbox
        agent_module.Sandbox = FakeSandbox
        try:
            agent = DockerAgent.__new__(DockerAgent)
            agent.workplace = "/tmp/demo-workplace"
            agent.command_timeout_seconds = 1234
            sandbox = agent._create_sandbox("python:3.11", "linux/amd64")
        finally:
            agent_module.Sandbox = original_sandbox

        self.assertIsInstance(sandbox, FakeSandbox)
        self.assertEqual(captured["base_image"], "python:3.11")
        self.assertEqual(captured["platform"], "linux/amd64")
        self.assertEqual(captured["seed_dir"], "/tmp/demo-workplace")
        self.assertEqual(captured["command_timeout_seconds"], 1234)

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

    def test_rejects_agent_reported_bundle_without_successful_test_observation(self):
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
        self.assertIsNone(agent.verification_source)

    def test_accepts_reported_command_when_successful_action_wrapped_it_with_echoes(self):
        agent = self._make_agent()

        agent._record_successful_action(
            1,
            (
                'cd /app/build && make test 2>&1 && echo "---" && '
                'echo "Verification Bundle:" && echo "{}"'
            ),
            "\n".join(
                [
                    "Passed:                           660",
                    "Failed:                             0",
                    "Unexpected successes:               0",
                    "[100%] Built target test",
                ]
            ),
        )

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Final verification passed.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": [], "test_commands": ["cd /app/build && make test"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertTrue(accepted)
        self.assertEqual(agent.verified_test_commands, ["cd /app/build && make test"])

    def test_rejects_agent_reported_bundle_when_test_command_was_not_observed(self):
        agent = self._make_agent()

        agent._record_successful_action(1, "python -m pytest tests/test_ok.py", "collected 1 item\n1 passed")

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Setup seems done.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": [], "test_commands": ["python -m pytest tests/test_broken.py"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertFalse(accepted)
        self.assertEqual(agent.verified_test_commands, ["python -m pytest tests/test_ok.py"])
        self.assertIsNone(agent.verification_source)

    def test_rejects_agent_reported_bundle_when_prior_output_had_errors(self):
        agent = self._make_agent()

        agent._record_successful_action(
            1,
            "python -m pytest tests",
            "collected 32 items\n==================== 32 passed, 1 error in 0.31s ====================",
        )

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Setup seems done.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": [], "test_commands": ["python -m pytest tests"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertFalse(accepted)
        self.assertEqual(agent.verified_test_commands, [])
        self.assertIsNone(agent.verification_source)

    def test_rejects_agent_reported_truncated_test_output_command(self):
        agent = self._make_agent()

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Setup seems done.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": [], '
                    '"test_commands": ["python -m pytest tests -v 2>&1 | head -100"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertFalse(accepted)
        self.assertEqual(agent.verified_test_commands, [])
        self.assertIsNone(agent.verification_source)

    def test_accepts_agent_reported_collect_only_bundle_when_observation_reports_tests_collected(self):
        agent = self._make_agent()

        agent._record_successful_action(
            1,
            "pytest --collect-only -q --disable-warnings",
            "22 tests collected in 1.39s\n",
        )

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Final verification passed.",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": [], "test_commands": ["pytest --collect-only -q --disable-warnings"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertTrue(accepted)
        self.assertEqual(agent.verified_test_commands, ["pytest --collect-only -q --disable-warnings"])
        self.assertEqual(agent.verification_source, "agent_report")

    def test_accepts_verification_bundle_from_successful_observation(self):
        agent = self._make_agent()

        agent._record_successful_action(
            1,
            "pytest --collect-only -q --disable-warnings 2>&1",
            "12 tests collected in 2.41s\n",
        )

        observation = "\n".join(
            [
                "Verification Bundle:",
                '{"runtime_preparation_commands": [], '
                '"test_commands": ["pytest --collect-only -q --disable-warnings"]}',
                "Final Answer: Success",
            ]
        )
        accepted = agent._finalize_verification_from_agent_report(
            observation,
            source="agent_observation",
        )

        self.assertTrue(agent._observation_contains_final_success_bundle(observation))
        self.assertTrue(accepted)
        self.assertEqual(agent.verified_test_commands, ["pytest --collect-only -q --disable-warnings"])
        self.assertEqual(agent.verification_source, "agent_observation")

    def test_auto_finalizes_from_recorded_verified_tests(self):
        agent = self._make_agent()

        agent._record_successful_action(
            1,
            "pytest --collect-only -q --disable-warnings",
            "18 tests collected in 0.31s\n",
        )

        accepted = agent._auto_finalize_from_verified_tests("auto_after_transient_error")

        self.assertTrue(accepted)
        self.assertEqual(agent.verification_source, "auto_after_transient_error")
        self.assertEqual(
            agent.verification_bundle,
            {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
            },
        )

    def test_invalid_final_report_auto_finalizes_when_verified_tests_exist(self):
        agent = self._make_agent()

        agent._record_successful_action(
            1,
            "pytest --collect-only -q --disable-warnings",
            "18 tests collected in 0.31s\n",
        )

        count, auto_finalized, stop_reason = agent._handle_invalid_final_success_report(0)

        self.assertEqual(count, 0)
        self.assertTrue(auto_finalized)
        self.assertIsNone(stop_reason)
        self.assertEqual(agent.verification_source, "auto_finalized_after_invalid_agent_report")
        self.assertEqual(
            agent.verification_bundle,
            {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
            },
        )

    def test_invalid_final_report_stops_after_repeated_invalid_reports_without_verified_tests(self):
        agent = self._make_agent()

        count, auto_finalized, stop_reason = agent._handle_invalid_final_success_report(2)

        self.assertEqual(count, 3)
        self.assertFalse(auto_finalized)
        self.assertIn("invalid final Verification Bundles", stop_reason)

    def test_observation_compression_error_does_not_abort_step_recording(self):
        agent = self._make_agent()
        agent.enable_observation_compression = True
        agent.compression_delay = 0
        agent.compression_threshold_chars = 1
        agent.compression_benefit_tokens = 1
        agent.compression_context_before = 0
        agent.compression_stats = {
            "candidate_steps": 0,
            "compressed_steps": 0,
            "saved_tokens_est": 0,
        }
        agent.agent_steps = []
        agent.memory_stats = {"errors": []}
        agent.run_token_ledger = SimpleNamespace(add=lambda *args, **kwargs: None)

        class FakePlanner:
            def append_step(self, **kwargs):
                return None

            def replace_observation(self, step_id, observation):
                return True

        class FailingCompressor:
            def compress(self, **kwargs):
                raise TimeoutError("Request timed out.")

        agent.planner = FakePlanner()
        agent.observation_compressor = FailingCompressor()

        agent._record_agent_step(
            step_id=1,
            thought="verify",
            action="pytest --collect-only -q --disable-warnings",
            assistant_content="Action: pytest --collect-only -q --disable-warnings",
            success=True,
            observation="tests collected",
            prompt_observation="tests collected",
            mutates_environment=False,
            env_revision_before=0,
            env_revision_after=0,
            planner_usage={"input_tokens": 1, "output_tokens": 1},
        )

        self.assertEqual(len(agent.agent_steps), 1)
        self.assertIn("Observation compression failed", agent.memory_stats["errors"][0])

    def test_accepts_json_bundle_before_final_answer_without_marker(self):
        agent = self._make_agent()

        agent._record_successful_action(
            1,
            "pytest --collect-only -q --disable-warnings",
            "22 tests collected in 1.39s\n",
        )

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: Final verification passed.",
                    '{"runtime_preparation_commands": [], "test_commands": ["pytest --collect-only -q --disable-warnings"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertTrue(accepted)
        self.assertEqual(agent.verified_test_commands, ["pytest --collect-only -q --disable-warnings"])

    def test_accepts_last_valid_bundle_when_output_contains_format_examples(self):
        agent = self._make_agent()

        agent._record_successful_action(
            1,
            "pytest --collect-only -q --disable-warnings",
            "24 tests collected in 7.33s\n",
        )

        accepted = agent._finalize_verification_from_agent_report(
            "\n".join(
                [
                    "Thought: The expected format is:",
                    "Verification Bundle:",
                    '{"runtime_preparation_commands": [], "test_commands": []}',
                    "Final Answer: Success",
                    "That was only an example. The real verified bundle is below.",
                    "Verification Bundle: "
                    '{"runtime_preparation_commands": [], '
                    '"test_commands": ["pytest --collect-only -q --disable-warnings"]}',
                    "Final Answer: Success",
                ]
            )
        )

        self.assertTrue(accepted)
        self.assertEqual(
            agent.verification_bundle,
            {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
            },
        )

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


class AgentPrepareWorkplaceTests(unittest.TestCase):
    def test_prepare_workplace_resets_partial_clone_before_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workplace = os.path.join(tmpdir, "workplace")
            agent = DockerAgent.__new__(DockerAgent)
            agent.workplace = workplace
            agent.repo_url = "https://github.com/example/repo.git"

            attempts = {"count": 0}

            def fake_run(command, cwd=None, check=None, capture_output=None):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    with open(os.path.join(cwd, "partial.txt"), "w", encoding="utf-8") as file_obj:
                        file_obj.write("partial clone state")
                    raise agent_module.subprocess.CalledProcessError(
                        128,
                        command,
                        output=b"",
                        stderr=b"fatal: remote hung up unexpectedly",
                    )

                self.assertFalse(os.path.exists(os.path.join(cwd, "partial.txt")))
                return SimpleNamespace(returncode=0)

            with mock.patch.object(agent_module.subprocess, "run", side_effect=fake_run):
                with mock.patch.object(agent_module.time, "sleep", return_value=None) as sleep_mock:
                    agent._prepare_workplace()

            self.assertEqual(attempts["count"], 2)
            sleep_mock.assert_not_called()
            self.assertTrue(os.path.isdir(workplace))
            self.assertFalse(os.path.exists(os.path.join(workplace, "partial.txt")))

    def test_prepare_workplace_raises_clear_error_when_git_lfs_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workplace = os.path.join(tmpdir, "workplace")
            agent = DockerAgent.__new__(DockerAgent)
            agent.workplace = workplace
            agent.repo_url = "https://github.com/example/repo.git"

            commands = []

            def fake_run(command, cwd=None, check=None, capture_output=None):
                commands.append(command)
                if "clone" in command and "filter.lfs.process=" not in command:
                    with open(os.path.join(cwd, "partial.txt"), "w", encoding="utf-8") as file_obj:
                        file_obj.write("partial clone state")
                    raise agent_module.subprocess.CalledProcessError(
                        128,
                        command,
                        output=b"Cloning into '.'...\ngit-lfs filter-process: git-lfs: command not found\n",
                        stderr=b"fatal: remote hung up unexpectedly\n",
                    )
                return SimpleNamespace(returncode=0)

            with mock.patch.object(agent_module.subprocess, "run", side_effect=fake_run) as run_mock:
                with mock.patch.object(agent_module.time, "sleep", return_value=None) as sleep_mock:
                    agent._prepare_workplace()

            self.assertEqual(run_mock.call_count, 2)
            sleep_mock.assert_not_called()
            self.assertTrue(os.path.isdir(workplace))
            self.assertFalse(os.path.exists(os.path.join(workplace, "partial.txt")))
            self.assertIn("filter.lfs.process=", commands[1])
            self.assertIn("clone", commands[1])


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
        ledger_calls = []
        agent.run_token_ledger = SimpleNamespace(
            image_selector=SimpleNamespace(),
            planner=SimpleNamespace(),
            supervisor=SimpleNamespace(),
            worker=SimpleNamespace(),
            reflection=SimpleNamespace(),
            memory=SimpleNamespace(),
            recipe=SimpleNamespace(),
            total=SimpleNamespace(),
            add=lambda bucket_name, input_tokens, output_tokens: ledger_calls.append(
                (bucket_name, input_tokens, output_tokens)
            ),
        )
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


class AgentSetupLogSelectionTests(unittest.TestCase):
    def test_latest_setup_log_ignores_non_numeric_auxiliary_logs(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.memory_stats = {"errors": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            agent.setup_log_dir = tmpdir
            with open(os.path.join(tmpdir, "1.md"), "w", encoding="utf-8") as file_obj:
                file_obj.write("planner setup log")
            with open(os.path.join(tmpdir, "recipe_synthesis.md"), "w", encoding="utf-8") as file_obj:
                file_obj.write("auxiliary recipe log")

            self.assertEqual(agent._read_latest_setup_log(), "planner setup log")

    def test_extract_setup_log_trajectory_drops_planner_prompt_and_repo_tree(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.agent_steps = []

        setup_log_text = "\n".join(
            [
                "##### LLM INPUT (setup call #3) #####",
                "================================ Human Message =================================",
                "",
                "[SYSTEM]",
                "You are an expert environment configuration agent.",
                "",
                "Repository Structure:",
                "```",
                "repo/",
                "  pyproject.toml",
                "```",
                "",
                "[ASSISTANT]",
                "Thought: Inspect dependencies first.",
                "Action: cat /app/pyproject.toml",
                "",
                "Observation: [project]",
                "dependencies = [\"pytest\"]",
                "",
                "[ASSISTANT]",
                "Thought: Run the tests to see what is missing.",
                "Action: python -m pytest tests -v",
                "",
                "Observation: /usr/local/bin/python: No module named pytest",
                "",
                "================================ Raw AI Message =================================",
                "",
                "Thought: Install pytest next.",
            ]
        )

        trajectory = agent._extract_setup_log_trajectory(setup_log_text)

        self.assertNotIn("[SYSTEM]", trajectory)
        self.assertNotIn("Repository Structure", trajectory)
        self.assertNotIn("pyproject.toml\n```", trajectory)
        self.assertIn("Step 1", trajectory)
        self.assertIn("Thought: Inspect dependencies first.", trajectory)
        self.assertIn("Action: cat /app/pyproject.toml", trajectory)
        self.assertIn("Observation:\n[project]\ndependencies = [\"pytest\"]", trajectory)
        self.assertIn("Step 2", trajectory)
        self.assertIn("Action: python -m pytest tests -v", trajectory)
        self.assertIn("Observation:\n/usr/local/bin/python: No module named pytest", trajectory)

    def test_build_recipe_input_uses_setup_log_summary_and_run_summary(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.repo_url = "https://github.com/example/repo.git"
        agent.base_commit = "abc1234"
        agent.client = object()
        agent.model = "fake-model"
        agent.language = "python"
        agent.required_local_services = {"redis"}
        agent.verification_bundle = {
            "runtime_preparation_commands": ["export APP_ENV=test"],
            "test_commands": ["python -m pytest tests -v"],
        }
        agent.verified_runtime_preparation_commands = ["export APP_ENV=test"]
        agent.verified_test_commands = ["python -m pytest tests -v"]
        agent.verified_test_command = "python -m pytest tests -v"
        agent.successful_test_commands = ["python -m pytest tests -v"]
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.failed_actions = []
        agent.verification_source = "agent_report"
        agent.build_recipe = None
        agent.build_recipe_source = None
        agent.build_recipe_error = None
        agent.enable_observation_compression = False
        agent.compression_stats = {}
        agent.enable_long_term_memory = False
        agent.memory_manager = None
        agent.memory_path = None
        agent.memory_embedding_model = None
        agent.memory_stats = {"errors": []}
        agent.agent_steps = []
        agent.benchmark_evaluation_target = {}
        ledger_calls = []
        agent.run_token_ledger = SimpleNamespace(
            image_selector=SimpleNamespace(),
            planner=SimpleNamespace(),
            supervisor=SimpleNamespace(),
            worker=SimpleNamespace(),
            reflection=SimpleNamespace(),
            memory=SimpleNamespace(),
            recipe=SimpleNamespace(),
            total=SimpleNamespace(),
            add=lambda bucket_name, input_tokens, output_tokens: ledger_calls.append(
                (bucket_name, input_tokens, output_tokens)
            ),
        )
        agent.synthesizer = SimpleNamespace(
            base_image="python:3.10",
            workdir="/app",
            summarize_setup_log_for_recipe=lambda client, model, setup_log_trajectory_text, log_dir=None: SimpleNamespace(
                summary_text=(
                    "Step 1-2\n"
                    "Type: read_only_inspection\n"
                    "Goal: inspect dependency files\n"
                    "Attempts:\n"
                    "- Step 1: cat requirements.txt -> found pytest\n"
                    "- Step 2: cat pyproject.toml -> no extra test deps\n"
                    "Outcome: requirements.txt is sufficient\n\n"
                    "Step 3\n"
                    "Type: successful_state_change\n"
                    "Thought: Install deps\n"
                    "Action: pip install -r requirements.txt\n"
                    "Observation: Installed pytest"
                ),
                usage={"input_tokens": 17, "output_tokens": 9, "total_tokens": 26},
                error=None,
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            agent.setup_log_dir = tmpdir
            with open(os.path.join(tmpdir, "2.md"), "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "\n".join(
                        [
                            "##### LLM INPUT (setup call #2) #####",
                            "================================ Human Message =================================",
                            "",
                            "[SYSTEM]",
                            "planner prompt",
                            "",
                            "Repository Structure:",
                            "repo/",
                            "",
                            "[ASSISTANT]",
                            "Thought: Install dependencies.",
                            "Action: pip install -r requirements.txt",
                            "",
                            "Observation: Successfully installed pytest",
                            "",
                            "================================ Raw AI Message =================================",
                        ]
                    )
                )

            recipe_input = agent._build_recipe_synthesis_input()

        self.assertEqual(
            recipe_input["setup_log_summary_text"],
            (
                "Step 1-2\n"
                "Type: read_only_inspection\n"
                "Goal: inspect dependency files\n"
                "Attempts:\n"
                "- Step 1: cat requirements.txt -> found pytest\n"
                "- Step 2: cat pyproject.toml -> no extra test deps\n"
                "Outcome: requirements.txt is sufficient\n\n"
                "Step 3\n"
                "Type: successful_state_change\n"
                "Thought: Install deps\n"
                "Action: pip install -r requirements.txt\n"
                "Observation: Installed pytest"
            ),
        )
        self.assertIn("agent_run_summary", recipe_input)
        self.assertEqual(
            recipe_input["agent_run_summary"]["verification_bundle"]["test_commands"],
            ["python -m pytest tests -v"],
        )
        self.assertEqual(
            recipe_input["task"]["required_local_services"],
            ["redis"],
        )
        self.assertEqual(ledger_calls, [("recipe", 17, 9)])


class AgentLongTermMemoryHintTests(unittest.TestCase):
    def test_memory_hint_is_not_added_when_disabled(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_long_term_memory = False
        agent.failed_actions = [{"command": "pip install deps"}]

        self.assertEqual(
            agent._append_long_term_memory_hint("pip failed"),
            "pip failed",
        )

    def test_memory_hint_points_agent_to_retrieve_after_failures(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_long_term_memory = True
        agent.failed_actions = [
            {"command": "pip install deps"},
            {"command": "pip install deps --no-cache-dir"},
        ]

        observation = agent._append_long_term_memory_hint("pip failed again")

        self.assertIn("[Long-Term Memory Hint]", observation)
        self.assertIn("2 commands have failed during this setup run", observation)
        self.assertIn("__RETRIEVE_MEMORY__", observation)


if __name__ == "__main__":
    unittest.main()
