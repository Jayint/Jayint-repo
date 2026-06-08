import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from src.planner import Planner


class PlannerManagedHistoryTests(unittest.TestCase):
    def test_replace_observation_updates_only_target_step(self):
        planner = Planner(client=None)
        planner.init_managed_history("https://github.com/example/repo.git")

        planner.append_step(1, "Thought: t1\nAction: a1", "obs1")
        planner.append_step(2, "Thought: t2\nAction: a2", "obs2")

        replaced = planner.replace_observation(1, "obs1-compressed")

        self.assertTrue(replaced)
        obs1_index = planner.managed_step_to_history_index[1]["observation"]
        obs2_index = planner.managed_step_to_history_index[2]["observation"]
        self.assertEqual(
            planner.managed_history[obs1_index]["content"],
            "Observation: obs1-compressed",
        )
        self.assertEqual(planner.managed_history[obs2_index]["content"], "Observation: obs2")

    def test_default_managed_history_is_not_trimmed_by_small_step_count(self):
        planner = Planner(client=None)
        planner.init_managed_history("https://github.com/example/repo.git")

        for step_id in range(1, 15):
            planner.append_step(
                step_id,
                f"Thought: t{step_id}\nAction: a{step_id}",
                f"obs{step_id}",
            )

        self.assertIn(1, planner.managed_step_to_history_index)
        self.assertIn(14, planner.managed_step_to_history_index)

    def test_trim_rebuilds_index_when_history_budget_is_small(self):
        planner = Planner(client=None, history_token_budget=120)
        planner.init_managed_history("https://github.com/example/repo.git")

        for step_id in range(1, 8):
            planner.append_step(
                step_id,
                f"Thought: {'t' * 40}\nAction: action-{step_id}-{'a' * 20}",
                f"observation-{step_id}-{'o' * 60}",
            )

        self.assertNotIn(1, planner.managed_step_to_history_index)
        self.assertIn(7, planner.managed_step_to_history_index)

        replaced = planner.replace_observation(7, "obs7-compressed")
        self.assertTrue(replaced)
        obs_index = planner.managed_step_to_history_index[7]["observation"]
        self.assertEqual(
            planner.managed_history[obs_index]["content"],
            "Observation: obs7-compressed",
        )

    def test_append_step_sanitizes_overgenerated_future_trajectory(self):
        planner = Planner(client=None)
        planner.init_managed_history("https://github.com/example/repo.git")

        overgenerated = (
            "<think>Planning the whole setup.</think>\n\n"
            "Thought: I will check Python first.\n"
            "Action: python --version\n"
            "Observation: Python 3.11.0\n"
            "Action: pip install pytest\n"
            "Observation: Successfully installed pytest\n"
            "Verification Bundle:\n"
            '{"runtime_preparation_commands": [], "test_commands": ["pytest"]}\n'
            "Final Answer: Success"
        )

        planner.append_step(1, overgenerated, "Python 3.6.15")

        assistant_index = planner.managed_step_to_history_index[1]["assistant"]
        self.assertEqual(
            planner.managed_history[assistant_index]["content"],
            "Thought: I will check Python first.\nAction: python --version",
        )

    def test_append_step_normalizes_think_block_when_thought_tag_is_missing(self):
        planner = Planner(client=None)
        planner.init_managed_history("https://github.com/example/repo.git")

        raw_output = (
            "<think>\n"
            "I should inspect the dependency file first.\n"
            "</think>\n\n"
            "Action: cat requirements.txt\n"
            "Observation: pyside6-fluent-widgets>=1.5.6"
        )

        planner.append_step(1, raw_output, "pyside6-fluent-widgets>=1.5.6")

        assistant_index = planner.managed_step_to_history_index[1]["assistant"]
        self.assertEqual(
            planner.managed_history[assistant_index]["content"],
            "Thought: I should inspect the dependency file first.\nAction: cat requirements.txt",
        )

    def test_extract_action_stops_before_verification_bundle(self):
        planner = Planner(client=None)

        content = (
            "Thought: Tests passed.\n"
            "Action: pytest tests -q\n"
            "Verification Bundle:\n"
            '{"runtime_preparation_commands": [], "test_commands": ["pytest tests -q"]}\n'
            "Final Answer: Success"
        )

        self.assertEqual(planner._extract_tag(content, "Action"), "pytest tests -q")

    def test_extract_action_ignores_action_text_inside_thought(self):
        planner = Planner(client=None)

        content = (
            'Thought: The system keeps asking for "Action: <command>", but this is just '
            "quoted protocol text, not an executable command."
        )

        self.assertIsNone(planner._extract_tag(content, "Action"))

    def test_log_output_marks_raw_overgeneration_and_executable_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            planner = Planner(client=None, log_dir=tmpdir)
            raw_output = (
                "Thought: inspect config\n"
                "Action: cat pyproject.toml\n"
                "Observation: fake file content\n"
                "Action: pytest"
            )
            sanitized = planner.sanitize_assistant_content(raw_output)

            planner._log_llm_call(
                "output",
                {
                    "content": raw_output,
                    "sanitized_content": sanitized,
                    "overgenerated": planner._assistant_output_was_sanitized(
                        raw_output,
                        sanitized,
                    ),
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 2,
                        "total_tokens": 3,
                    },
                },
            )

            log_text = (Path(tmpdir) / "0.md").read_text(encoding="utf-8")
            self.assertIn("Raw AI Message", log_text)
            self.assertIn("Executable Message Used By Agent", log_text)
            self.assertIn("Thought: inspect config\nAction: cat pyproject.toml", log_text)
            self.assertIn("raw model output contained generated Observation", log_text)


class PlannerFinalAnswerParsingTests(unittest.TestCase):
    def test_plan_treats_final_answer_as_finished_even_when_action_is_present(self):
        class FakeCompletions:
            def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=(
                                    "Thought: The environment is ready.\n"
                                    "Action: pytest --collect-only -q --disable-warnings\n"
                                    "Verification Bundle:\n"
                                    '{"runtime_preparation_commands": [], '
                                    '"test_commands": ["pytest --collect-only -q --disable-warnings"]}\n'
                                    "Final Answer: Success"
                                )
                            )
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=1,
                        completion_tokens=2,
                        total_tokens=3,
                    ),
                )

        planner = Planner(
            client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        )

        _, action, _, is_finished, _ = planner.plan(
            "https://github.com/example/repo.git",
            "previous observation",
        )

        self.assertEqual(action, "pytest --collect-only -q --disable-warnings")
        self.assertTrue(is_finished)
        self.assertNotIn("Action:", planner.history[-1]["content"])

    def test_extract_final_answer_ignores_quoted_phrase(self):
        planner = Planner(client=None)

        content = (
            'Thought: I cannot output "Final Answer: Success" while tests are failing.\n'
            "Action: mvn test -Dtest=JsonUtilsTest -pl openbas-api"
        )

        self.assertIsNone(planner.extract_final_answer(content))

    def test_extract_final_answer_accepts_real_final_success_marker(self):
        planner = Planner(client=None)

        content = (
            "Thought: The environment is ready.\n"
            "Final Answer: Success"
        )

        self.assertEqual(planner.extract_final_answer(content), "Success")

    def test_extract_final_answer_ignores_inline_thought_marker(self):
        planner = Planner(client=None)

        content = (
            "Thought: The instructions mention Final Answer: Success, but I still need an action.\n"
            "Action: pytest --collect-only -q --disable-warnings"
        )

        self.assertIsNone(planner.extract_final_answer(content))

    def test_extract_final_answer_ignores_format_example_followed_by_more_text(self):
        planner = Planner(client=None)

        content = (
            "The format should be:\n\n"
            "Verification Bundle: {\"runtime_preparation_commands\": [], \"test_commands\": [\"pytest\"]}\n"
            "Final Answer: Success\n\n"
            "Wait, I still need to output the final response correctly."
        )

        self.assertIsNone(planner.extract_final_answer(content))

    def test_extract_final_answer_rejects_later_incomplete_final_marker(self):
        planner = Planner(client=None)

        content = (
            "Verification Bundle: {\"runtime_preparation_commands\": [], \"test_commands\": [\"pytest\"]}\n"
            "Final Answer: Success\n\n"
            "Trying again:\n"
            "Verification Bundle: {\"runtime_preparation_commands\": [], \"test_commands\": [\"pytest\"]}\n"
            "Final Answer:"
        )

        self.assertIsNone(planner.extract_final_answer(content))


class PlannerPromptTests(unittest.TestCase):
    def test_system_prompt_tells_agent_to_use_apt_without_sudo(self):
        planner = Planner(client=None)

        self.assertIn("Do not use `sudo`.", planner.system_prompt)
        self.assertIn(
            "first try installing it directly with commands like `apt-get update && apt-get install -y <package>`",
            planner.system_prompt,
        )

    def test_system_prompt_exposes_explicit_rollback_action(self):
        planner = Planner(client=None)

        self.assertIn("Action: <bash command to execute, or __ROLLBACK__>", planner.system_prompt)
        self.assertIn("Ordinary command failures do NOT automatically roll back the container", planner.system_prompt)
        self.assertIn("Action: __ROLLBACK__", planner.system_prompt)
        self.assertIn("Split Mutation From Verification", planner.system_prompt)
        self.assertIn("probe/test/read-only command", planner.system_prompt)
        self.assertIn("do not rely on that implicit partial state", planner.system_prompt)
        self.assertIn("rerun it as its own separate Action so it is confirmed successful", planner.system_prompt)

    def test_system_prompt_discourages_hard_to_replay_compound_commands(self):
        planner = Planner(client=None)

        self.assertIn("Keep Setup Commands Atomic", planner.system_prompt)
        self.assertIn("Prefer one state-changing operation per Action", planner.system_prompt)
        self.assertIn("do not put environment-changing commands and read-only checks/tests in the same Action", planner.system_prompt)
        self.assertIn("No Output Truncation Filters", planner.system_prompt)
        self.assertIn("Do not append `| tail`, `| head`, `| grep`", planner.system_prompt)

    def test_system_prompt_exposes_long_term_memory_only_when_enabled(self):
        default_planner = Planner(client=None)
        memory_planner = Planner(client=None, enable_long_term_memory=True)

        self.assertNotIn("__RETRIEVE_MEMORY__", default_planner.system_prompt)
        self.assertIn("__RETRIEVE_MEMORY__", memory_planner.system_prompt)
        self.assertIn("LONG-TERM MEMORY TOOL", memory_planner.system_prompt)
        self.assertIn("[Long-Term Memory Hint]", memory_planner.system_prompt)
        self.assertIn("Prefer this before trying more speculative fixes", memory_planner.system_prompt)
        self.assertIn(
            "Action: <bash command to execute, __ROLLBACK__, or __RETRIEVE_MEMORY__>",
            memory_planner.system_prompt,
        )

    def test_system_prompt_forbids_generated_observations_and_future_steps(self):
        planner = Planner(client=None)

        self.assertIn("The Observation is produced ONLY by the host system", planner.system_prompt)
        self.assertIn("You are the planner, not the executor", planner.system_prompt)
        self.assertIn("Your response must end immediately after the Action line", planner.system_prompt)
        self.assertIn("Never generate command results, `Observation:`", planner.system_prompt)
        self.assertIn("a second `Action:`", planner.system_prompt)
        self.assertIn("Do not simulate command execution results", planner.system_prompt)

    def test_system_prompt_requires_matching_real_local_services(self):
        planner = Planner(client=None)

        self.assertIn("Do NOT replace it with a different backend", planner.system_prompt)
        self.assertIn("A Client Is Not A Service", planner.system_prompt)
        self.assertIn("The actual server/daemon must be running and reachable", planner.system_prompt)
        self.assertIn("host/port expected by the tests", planner.system_prompt)

    def test_system_prompt_moves_detailed_recovery_strategy_to_memory_trigger(self):
        default_planner = Planner(client=None)
        memory_planner = Planner(client=None, enable_long_term_memory=True)

        self.assertNotIn("Prefer Small Package Batches On Flaky Networks", default_planner.system_prompt)
        self.assertNotIn("Protect Existing Maven Repositories", default_planner.system_prompt)
        self.assertNotIn("`<mirrorOf>*</mirrorOf>`", default_planner.system_prompt)
        self.assertNotIn("apt broken state", memory_planner.system_prompt)
        self.assertNotIn("Maven mirrors", memory_planner.system_prompt)
        self.assertNotIn("local services/daemons", memory_planner.system_prompt)
        self.assertIn("resisted several real attempts", memory_planner.system_prompt)
        self.assertIn("repeated troubleshooting", memory_planner.system_prompt)

    def test_system_prompt_requires_representative_tests_for_service_dependent_projects(self):
        planner = Planner(client=None)

        self.assertIn("Service-Dependent Projects Still Need Real Environment Fixes", planner.system_prompt)
        self.assertIn("If pytest collection fails because repository imports/configuration require local services", planner.system_prompt)

    def test_system_prompt_uses_repo2run_collect_only_success_definition(self):
        planner = Planner(client=None)

        self.assertIn("pytest --collect-only -q --disable-warnings", planner.system_prompt)
        self.assertIn("poetry run pytest --collect-only -q --disable-warnings", planner.system_prompt)
        self.assertIn("You do NOT need to run the full test suite or make all tests pass", planner.system_prompt)
        self.assertIn("The target is successful pytest collection, not test execution or test passing", planner.system_prompt)
        self.assertIn("Repo2Run-style collection command", planner.system_prompt)
        self.assertNotIn("representative project-native tests should execute for real", planner.system_prompt)
        self.assertNotIn("native tests pass", planner.system_prompt)
        self.assertNotIn("tests that pass", planner.system_prompt)

    def test_system_prompt_forbids_truncated_final_test_output(self):
        planner = Planner(client=None)

        self.assertIn("Do Not Truncate Verification Output", planner.system_prompt)
        self.assertIn("Do NOT pipe the collection command through `head`, `tail`", planner.system_prompt)
        self.assertIn("long output will be handled by observation compression", planner.system_prompt)

    def test_system_prompt_includes_project_maven_repository_hints(self):
        planner = Planner(
            client=None,
            maven_repository_hints="- shibboleth_repository: https://build.shibboleth.net/nexus/content/repositories/releases/ (declared in pom.xml)",
        )

        self.assertIn("Project Maven Repository Hints:", planner.system_prompt)
        self.assertIn("shibboleth_repository", planner.system_prompt)

    def test_benchmark_evaluation_target_is_seed_context_not_system_prompt(self):
        planner = Planner(
            client=None,
            benchmark_evaluation_target={
                "changed_test_files": ["test/resize.t", "test/summary.t"],
                "test_framework_clues": ["TAP/simpletap", "python unittest"],
            },
        )

        planner.init_managed_history("https://github.com/example/repo.git")
        seed_content = planner.managed_history[0]["content"]

        self.assertIn("Benchmark Evaluation Target:", seed_content)
        self.assertIn("test/resize.t", seed_content)
        self.assertIn("TAP/simpletap", seed_content)
        self.assertIn("Do NOT apply the benchmark test patch", seed_content)
        self.assertIn("successful Repo2Run-style pytest collection", seed_content)
        self.assertIn("you do not need to execute or pass those tests", seed_content)
        self.assertNotIn("test/resize.t", planner.system_prompt)


class PlannerNoneContentTests(unittest.TestCase):
    def _make_planner(self):
        class FakeCompletionsNone:
            def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1),
                )

        return Planner(
            client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletionsNone()))
        )

    def test_plan_tolerates_none_completion_content(self):
        planner = self._make_planner()
        thought, action, content, is_finished, usage = planner.plan(
            "https://github.com/example/repo.git",
            "previous observation",
        )
        self.assertIsNone(thought)
        self.assertIsNone(action)
        self.assertFalse(is_finished)
        self.assertEqual(content, "")


if __name__ == "__main__":
    unittest.main()
