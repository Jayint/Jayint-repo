"""Unit tests for src.artifact_verify — clean-room render/classify + repair orchestration.

Docker is never invoked: the build/test wrappers are patched so the loop logic is
tested deterministically.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import artifact_verify as av


class ConfigurableDetector:
    """Stand-in for the Synthesizer signal API used by classify_test_execution."""

    def __init__(self, effective=False, empty=False, help_=False, failure=False):
        self._effective, self._empty, self._help, self._failure = effective, empty, help_, failure

    def observation_has_effective_test_signal(self, o):
        return self._effective

    def observation_has_empty_test_run_signal(self, o):
        return self._empty

    def observation_looks_like_help_text(self, o):
        return self._help

    def observation_has_test_failure_signal(self, o):
        return self._failure


class TestTestExecutionScript(unittest.TestCase):
    def test_script_structure(self):
        script = av.build_test_execution_script("/app", ["redis-server &"], "pytest -q")
        self.assertIn("cd /app", script)
        self.assertIn("redis-server &", script)
        self.assertIn("pytest -q", script)
        self.assertIn("__SELFVERIFY_TEST_EXIT_CODE__", script)


class TestSelfContainedDockerfile(unittest.TestCase):
    def test_inserts_clone_after_workdir_with_checkout(self):
        text = "FROM python:3.11\nWORKDIR /app\nRUN pip install -e .\n"
        out = av.make_self_contained_dockerfile(text, "https://x/y.git", "abc123", "/app")
        lines = out.splitlines()
        workdir_idx = next(i for i, l in enumerate(lines) if l.startswith("WORKDIR"))
        self.assertIn("git clone", lines[workdir_idx + 1])
        self.assertIn("git checkout abc123", lines[workdir_idx + 1])
        # clone must precede the build command
        clone_idx = next(i for i, l in enumerate(lines) if "git clone" in l)
        install_idx = next(i for i, l in enumerate(lines) if "pip install -e ." in l)
        self.assertLess(clone_idx, install_idx)

    def test_no_checkout_when_no_base_commit(self):
        out = av.make_self_contained_dockerfile("FROM x\nWORKDIR /app\n", "https://x/y", None, "/app")
        self.assertNotIn("git checkout", out)

    def test_falls_back_to_after_from_without_workdir(self):
        out = av.make_self_contained_dockerfile("FROM python:3.11\nRUN echo hi\n", "https://x/y", None, "/app")
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("FROM"))
        self.assertIn("git clone", lines[1])


class TestClassify(unittest.TestCase):
    def test_returncode_zero_is_effective(self):
        r = av.classify_test_execution({"returncode": 0, "stdout": "5 passed"}, ConfigurableDetector())
        self.assertTrue(r["effective"])
        self.assertEqual(r["reason"], "tests_passed")

    def test_returncode_five_no_tests_not_effective(self):
        # pytest exit 5 = zero tests collected/ran. Collecting nothing is not execution.
        r = av.classify_test_execution({"returncode": 5}, ConfigurableDetector())
        self.assertFalse(r["effective"])
        self.assertEqual(r["reason"], "no_tests_collected")

    def test_collect_only_success_not_effective(self):
        # `pytest --collect-only` exits 0 but only COLLECTS tests — it never runs them.
        # This is the bug that disarmed self-verify on 33/50 repos: collection "succeeds",
        # so the repair loop declared victory and never fired. rc 0 alone is not execution.
        r = av.classify_test_execution(
            {"returncode": 0, "stdout": "collected 15 items"},
            ConfigurableDetector(),
        )
        self.assertFalse(r["effective"])
        self.assertEqual(r["reason"], "ran_without_test_execution")

    def test_returncode_zero_with_real_run_is_effective(self):
        # rc 0 WITH a real pytest summary line is a genuine pass.
        r = av.classify_test_execution(
            {"returncode": 0, "stdout": "===== 15 passed in 2.3s ====="},
            ConfigurableDetector(),
        )
        self.assertTrue(r["effective"])
        self.assertEqual(r["reason"], "tests_passed")

    def test_internal_repo_import_error_not_effective(self):
        out = "ModuleNotFoundError: No module named 'src'"
        r = av.classify_test_execution(
            {"returncode": 1, "stderr": out}, ConfigurableDetector(), internal_import_prefixes={"src"},
        )
        self.assertFalse(r["effective"])
        self.assertEqual(r["reason"], "internal_repo_import_error")

    def test_ran_with_failures_is_effective(self):
        r = av.classify_test_execution(
            {"returncode": 1, "stdout": "1 failed, 3 passed"},
            ConfigurableDetector(effective=True, failure=True),
        )
        self.assertTrue(r["effective"])
        self.assertEqual(r["reason"], "tests_ran_with_failures")

    def test_missing_third_party_module_not_effective(self):
        r = av.classify_test_execution(
            {"returncode": 1, "stderr": "ModuleNotFoundError: No module named 'redis'"},
            ConfigurableDetector(),
        )
        self.assertFalse(r["effective"])


class TestOrchestrator(unittest.TestCase):
    def test_skips_without_test_command(self):
        result = av.verify_and_repair_recipe(
            recipe={"build_commands": []}, synthesizer=ConfigurableDetector(),
            repo_url="https://x/y", base_commit=None, workdir="/app",
            verified_test_command=None, runtime_preparation_commands=[],
            workspace_root=None, client=None, model="m", image_tag="t",
        )
        self.assertEqual(result["status"], "skipped_no_test_command")
        self.assertFalse(result["changed"])

    def test_hollow_then_resolved_via_deterministic_repair(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "requirements.txt").write_text("narwhals==1.0.0\n")
            test_results = [
                {"returncode": 1, "stdout": "", "stderr": "ModuleNotFoundError: No module named 'narwhals'", "timed_out": False},
                {"returncode": 0, "stdout": "5 passed", "stderr": "", "timed_out": False},
            ]
            with mock.patch.object(av, "render_verification_dockerfile", return_value="FROM x\nWORKDIR /app\n"), \
                 mock.patch.object(av, "build_image", return_value={"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}), \
                 mock.patch.object(av, "run_test_command_in_image", side_effect=test_results), \
                 mock.patch.object(av, "remove_image"):
                result = av.verify_and_repair_recipe(
                    recipe={"build_commands": [], "test_commands": ["pytest"]},
                    synthesizer=ConfigurableDetector(),
                    repo_url="https://x/y", base_commit=None, workdir="/app",
                    verified_test_command="pytest", runtime_preparation_commands=[],
                    workspace_root=d, client=None, model="m", image_tag="t",
                    logger=lambda *_: None,
                )
        self.assertEqual(result["status"], "resolved")
        self.assertTrue(result["changed"])
        self.assertIn("pip install narwhals==1.0.0", result["recipe"]["build_commands"])
        # round 0 attempted a deterministic repair, round 1 resolved
        self.assertEqual(result["rounds"][0]["repair"]["type"], "deterministic")
        self.assertTrue(result["rounds"][1]["resolved"])

    def test_unresolved_when_no_repair_available(self):
        # missing third-party module but no workspace + no client → no repair possible
        with mock.patch.object(av, "render_verification_dockerfile", return_value="FROM x\nWORKDIR /app\n"), \
             mock.patch.object(av, "build_image", return_value={"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}), \
             mock.patch.object(av, "run_test_command_in_image",
                               return_value={"returncode": 1, "stdout": "", "stderr": "boom", "timed_out": False}), \
             mock.patch.object(av, "remove_image"):
            result = av.verify_and_repair_recipe(
                recipe={"build_commands": [], "test_commands": ["pytest"]},
                synthesizer=ConfigurableDetector(),
                repo_url="https://x/y", base_commit=None, workdir="/app",
                verified_test_command="pytest", runtime_preparation_commands=[],
                workspace_root=None, client=None, model="m", image_tag="t",
                logger=lambda *_: None,
            )
        # no missing-module signal and no LLM client → cannot repair
        self.assertEqual(result["status"], "unresolved")

    def test_build_failure_path(self):
        with mock.patch.object(av, "render_verification_dockerfile", return_value="FROM x\nWORKDIR /app\n"), \
             mock.patch.object(av, "build_image",
                               return_value={"returncode": 1, "stdout": "", "stderr": "build broke", "timed_out": False}), \
             mock.patch.object(av, "remove_image"):
            result = av.verify_and_repair_recipe(
                recipe={"build_commands": [], "test_commands": ["pytest"]},
                synthesizer=ConfigurableDetector(),
                repo_url="https://x/y", base_commit=None, workdir="/app",
                verified_test_command="pytest", runtime_preparation_commands=[],
                workspace_root=None, client=None, model="m", image_tag="t",
                max_rounds=0, logger=lambda *_: None,
            )
        self.assertEqual(result["status"], "build_failed")


if __name__ == "__main__":
    unittest.main()
