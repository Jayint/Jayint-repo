import unittest

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


class PlannerFinalAnswerParsingTests(unittest.TestCase):
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
            "Thought: The environment is ready. Therefore, Final Answer: Success"
        )

        self.assertEqual(planner.extract_final_answer(content), "Success")


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

    def test_system_prompt_exposes_long_term_memory_only_when_enabled(self):
        default_planner = Planner(client=None)
        memory_planner = Planner(client=None, enable_long_term_memory=True)

        self.assertNotIn("__RETRIEVE_MEMORY__", default_planner.system_prompt)
        self.assertIn("__RETRIEVE_MEMORY__", memory_planner.system_prompt)
        self.assertIn("LONG-TERM MEMORY TOOL", memory_planner.system_prompt)
        self.assertIn(
            "Action: <bash command to execute, __ROLLBACK__, or __RETRIEVE_MEMORY__>",
            memory_planner.system_prompt,
        )

    def test_system_prompt_forbids_generated_observations_and_future_steps(self):
        planner = Planner(client=None)

        self.assertIn("Do NOT write `Observation:`", planner.system_prompt)
        self.assertIn("Never generate `Observation:`", planner.system_prompt)
        self.assertIn("a second `Action:`", planner.system_prompt)
        self.assertIn("Do not simulate command execution results", planner.system_prompt)

    def test_system_prompt_requires_matching_real_local_services(self):
        planner = Planner(client=None)

        self.assertIn("Do NOT replace it with a different backend", planner.system_prompt)
        self.assertIn("client package such as `postgresql-client`", planner.system_prompt)
        self.assertIn("The actual server/daemon must be installed, started, and reachable", planner.system_prompt)
        self.assertIn("Running PostgreSQL on 5432 does NOT satisfy tests that explicitly expect 5433", planner.system_prompt)
        self.assertIn("If a service refuses to start as root", planner.system_prompt)
        self.assertIn("Prefer `service <name> start`", planner.system_prompt)
        self.assertIn("verify that its log/data directories are writable by that service user", planner.system_prompt)

    def test_system_prompt_requires_small_batched_installs_on_flaky_networks(self):
        planner = Planner(client=None)

        self.assertIn("Prefer Small Package Batches On Flaky Networks", planner.system_prompt)
        self.assertIn("Do NOT start with one huge `apt-get install`", planner.system_prompt)
        self.assertIn("do not install `nodejs`/`npm` early unless they are immediately required", planner.system_prompt)
        self.assertIn("Fix Broken Package State Before Expanding Scope", planner.system_prompt)

    def test_system_prompt_warns_against_global_maven_mirror_override(self):
        planner = Planner(client=None)

        self.assertIn("Protect Existing Maven Repositories", planner.system_prompt)
        self.assertIn("`<mirrorOf>*</mirrorOf>`", planner.system_prompt)
        self.assertIn("temporary per-command settings file", planner.system_prompt)

    def test_system_prompt_requires_representative_tests_for_service_dependent_projects(self):
        planner = Planner(client=None)

        self.assertIn("Service-Dependent Projects Need Representative Final Tests", planner.system_prompt)
        self.assertIn("do NOT end with only narrow single-test or unit-test commands", planner.system_prompt)

    def test_system_prompt_includes_project_maven_repository_hints(self):
        planner = Planner(
            client=None,
            maven_repository_hints="- shibboleth_repository: https://build.shibboleth.net/nexus/content/repositories/releases/ (declared in pom.xml)",
        )

        self.assertIn("Project Maven Repository Hints:", planner.system_prompt)
        self.assertIn("shibboleth_repository", planner.system_prompt)


if __name__ == "__main__":
    unittest.main()
