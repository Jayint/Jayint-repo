import base64
import re
import unittest
import tempfile
from types import SimpleNamespace

from src.synthesizer import Synthesizer, build_dockerfile_apt_bootstrap_run_instructions


class FakeRecipeClient:
    def __init__(self, content):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    usage=SimpleNamespace(prompt_tokens=13, completion_tokens=5, total_tokens=18),
                )
            )
        )


class SynthesizerTests(unittest.TestCase):
    def test_extracts_setup_prefix_before_pytest(self):
        synthesizer = Synthesizer()
        commands = synthesizer._extract_recordable_setup_commands("pip install -e . && pytest tests")
        self.assertEqual(commands, ["pip install -e ."])

    def test_preserves_directory_change_when_setup_depends_on_it(self):
        synthesizer = Synthesizer()
        commands = synthesizer._extract_recordable_setup_commands("cd backend && npm install && npm test")
        self.assertEqual(commands, ["cd backend && npm install"])

    def test_discards_navigation_only_prefix_before_test(self):
        synthesizer = Synthesizer()
        commands = synthesizer._extract_recordable_setup_commands("cd build && ctest --output-on-failure")
        self.assertEqual(commands, [])

    def test_records_only_setup_portion_of_mixed_command(self):
        synthesizer = Synthesizer()
        synthesizer.record_success("pip install -e . && pytest tests")

        self.assertIn("RUN pip install -e .", synthesizer.instructions)
        self.assertNotIn("RUN pip install -e . && pytest tests", synthesizer.instructions)

    def test_discards_navigation_only_command(self):
        synthesizer = Synthesizer()
        commands = synthesizer._extract_recordable_setup_commands("cd build")
        self.assertEqual(commands, [])

    def test_drops_runtime_healthcheck_prefix_before_test(self):
        synthesizer = Synthesizer()
        commands = synthesizer._extract_recordable_setup_commands(
            "redis-cli ping && python3 -m pytest tests/"
        )
        self.assertEqual(commands, [])

    def test_strips_runtime_service_segments_from_setup_command(self):
        synthesizer = Synthesizer()
        commands = synthesizer._extract_recordable_setup_commands(
            "apt-get install -y redis-server && service redis-server start"
        )
        self.assertEqual(commands, ["apt-get install -y redis-server"])

    def test_preserves_file_edits_while_dropping_runtime_service_start(self):
        synthesizer = Synthesizer()
        commands = synthesizer._extract_recordable_setup_commands(
            'redis-server --daemonize yes && sed -i "s/foo/bar/" app.py'
        )
        self.assertEqual(commands, ['sed -i "s/foo/bar/" app.py'])

    def test_go_test_with_real_results_and_no_test_files_is_effective(self):
        synthesizer = Synthesizer()
        analysis = synthesizer.analyze_test_run(
            "go test -race ./...",
            "\n".join(
                [
                    "ok  \tgo.uber.org/atomic\t0.188s",
                    "?   \tgo.uber.org/atomic/internal/gen-atomicint\t[no test files]",
                    "?   \tgo.uber.org/atomic/internal/gen-atomicwrapper\t[no test files]",
                ]
            ),
        )

        self.assertTrue(analysis["is_test_command"])
        self.assertTrue(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "observed_test_execution_signal")

    def test_go_test_with_only_no_test_files_is_empty_run(self):
        synthesizer = Synthesizer()
        analysis = synthesizer.analyze_test_run(
            "go test ./internal/...",
            "\n".join(
                [
                    "?   \tgo.uber.org/atomic/internal/gen-atomicint\t[no test files]",
                    "?   \tgo.uber.org/atomic/internal/gen-atomicwrapper\t[no test files]",
                ]
            ),
        )

        self.assertTrue(analysis["is_test_command"])
        self.assertFalse(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "no_tests_executed")

    def test_dot_slash_vendor_phpunit_is_detected_and_effective(self):
        synthesizer = Synthesizer()
        analysis = synthesizer.analyze_test_run(
            "./vendor/bin/phpunit",
            "\n".join(
                [
                    "PHPUnit 9.6.34 by Sebastian Bergmann and contributors.",
                    "",
                    "Testing ",
                    "................................................................. 65 / 94 ( 69%)",
                    ".............................                                     94 / 94 (100%)",
                    "",
                    "Time: 00:00.012, Memory: 10.00 MB",
                    "",
                    "OK (94 tests, 185 assertions)",
                ]
            ),
        )

        self.assertTrue(analysis["is_test_command"])
        self.assertTrue(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "observed_test_execution_signal")

    def test_npm_tap_progress_output_counts_as_effective(self):
        synthesizer = Synthesizer()
        analysis = synthesizer.analyze_test_run(
            "npm test",
            "\n".join(
                [
                    "> pino-pretty@9.4.0 test",
                    "> tap --100 --color",
                    "",
                    "\u001b[1mSuites:\u001b[22m   0 of 5 completed",
                    "\u001b[1mAsserts:\u001b[22m  0 of 0",
                    "\u001b[43m RUNS \u001b[0m test/basic.test.js",
                ]
            ),
        )

        self.assertTrue(analysis["is_test_command"])
        self.assertTrue(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "observed_test_execution_signal")

    def test_maven_surefire_summary_counts_as_effective(self):
        synthesizer = Synthesizer()
        analysis = synthesizer.analyze_test_run(
            'mvn test -pl openbas-api -Dtest="EmailServiceTest,ResultUtilsTest,AtomicTestingUtilsTest"',
            "\n".join(
                [
                    "[INFO] -------------------------------------------------------",
                    "[INFO]  T E S T S",
                    "[INFO] -------------------------------------------------------",
                    "[INFO] Running io.openbas.service.EmailServiceTest",
                    "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 1.069 s -- in io.openbas.service.EmailServiceTest",
                    "[INFO] Running io.openbas.injects.atomic_testing.AtomicTestingUtilsTest",
                    "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.013 s -- in io.openbas.injects.atomic_testing.AtomicTestingUtilsTest",
                    "[INFO] Running io.openbas.injects.atomic_testing.ResultUtilsTest",
                    "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.271 s -- in io.openbas.injects.atomic_testing.ResultUtilsTest",
                    "[INFO] Results:",
                    "[INFO] Tests run: 3, Failures: 0, Errors: 0, Skipped: 0",
                    "[INFO] BUILD SUCCESS",
                ]
            ),
        )

        self.assertTrue(analysis["is_test_command"])
        self.assertTrue(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "observed_test_execution_signal")

    def test_pytest_nonzero_error_summary_is_not_effective_success(self):
        synthesizer = Synthesizer()
        observation = "\n".join(
            [
                "collected 32 items",
                "tests/test_database.py::test_dc_option ERROR",
                "==================== 32 passed, 1 error in 0.31 seconds ====================",
            ]
        )

        analysis = synthesizer.analyze_test_run("python -m pytest tests", observation)

        self.assertTrue(analysis["is_test_command"])
        self.assertFalse(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "test_failure_signal")
        self.assertTrue(synthesizer.observation_has_test_failure_signal(observation))

    def test_public_observation_signal_wrapper_detects_real_test_output(self):
        synthesizer = Synthesizer()

        self.assertTrue(
            synthesizer.observation_has_effective_test_signal("OK (94 tests, 185 assertions)")
        )
        self.assertTrue(
            synthesizer.observation_has_effective_test_signal(
                "[INFO] Tests run: 3, Failures: 0, Errors: 0, Skipped: 0"
            )
        )

    def test_safe_readonly_pipeline_is_not_recorded(self):
        synthesizer = Synthesizer()
        command = (
            'cd /app/openbas-api && grep -r "import org.junit.jupiter.api.Test;" '
            'src/test/java --include="*.java" | grep -v "IntegrationTest" | head -5'
        )

        self.assertTrue(synthesizer.is_readonly_command(command))
        self.assertEqual(synthesizer._extract_recordable_setup_commands(command), [])

    def test_safe_find_exec_command_with_escaped_semicolon_is_not_recorded(self):
        synthesizer = Synthesizer()
        command = (
            'cd /app/openbas-api && find src/test -name "*.java" -exec grep -L '
            '"@SpringBootTest\\|@DataJpaTest\\|@WebMvcTest" {} \\; | head -10'
        )

        self.assertTrue(synthesizer.is_readonly_command(command))
        self.assertEqual(synthesizer._extract_recordable_setup_commands(command), [])

    def test_safe_xargs_pipeline_is_not_recorded(self):
        synthesizer = Synthesizer()
        command = (
            'find openbas-api/src/test/java -name "*Test.java" | '
            'xargs grep -L "@SpringBootTest\\|@DataJpaTest\\|@IntegrationTest" | head -10'
        )

        self.assertTrue(synthesizer.is_readonly_command(command))
        self.assertEqual(synthesizer._extract_recordable_setup_commands(command), [])

    def test_ephemeral_numbered_archive_repair_is_not_recorded(self):
        synthesizer = Synthesizer()
        command = (
            "mv apache-maven-3.9.9-bin.tar.gz.1 apache-maven-3.9.9-bin.tar.gz && "
            "tar -xzf apache-maven-3.9.9-bin.tar.gz && "
            "mv apache-maven-3.9.9 /opt/maven && "
            "ln -s /opt/maven/bin/mvn /usr/local/bin/mvn"
        )

        self.assertEqual(synthesizer._extract_recordable_setup_commands(command), [])
        synthesizer.record_success(command)
        self.assertEqual(synthesizer.instructions, [])

    def test_download_and_extract_chain_remains_recordable(self):
        synthesizer = Synthesizer()
        command = (
            "wget https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/"
            "apache-maven-3.9.9-bin.tar.gz && "
            "tar -xzf apache-maven-3.9.9-bin.tar.gz && "
            "mv apache-maven-3.9.9 /opt/maven && "
            "ln -s /opt/maven/bin/mvn /usr/local/bin/mvn"
        )

        self.assertEqual(
            synthesizer._extract_recordable_setup_commands(command),
            [command],
        )

    def test_dockerfile_generation_includes_apt_bootstrap_before_setup_instructions(self):
        synthesizer = Synthesizer(base_image="ubuntu:24.04", workdir="/app")
        synthesizer.record_success("apt-get update && apt-get install -y git")

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile_path = f"{tmpdir}/Dockerfile"
            dockerfile = synthesizer.generate_dockerfile(file_path=dockerfile_path)

        self.assertIn("99jayint-retries", dockerfile)
        self.assertLess(
            dockerfile.index("99jayint-retries"),
            dockerfile.index("RUN apt-get update && apt-get install -y git"),
        )

    def test_build_recipe_normalization_preserves_llm_build_commands(self):
        synthesizer = Synthesizer()
        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "RUN pip install -e .",
                    "pytest tests",
                    "ls -la",
                    "redis-server --daemonize yes",
                    "pip install pytest",
                ],
                "post_test_patch_commands": ["RUN npm install"],
                "runtime_preparation_commands": ["redis-server --daemonize yes"],
                "test_commands": ["pytest tests"],
                "excluded_commands": [],
                "rationale": "Use editable install.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": ["redis-server --daemonize yes"],
                    "test_commands": ["pytest tests"],
                },
                "successful_actions": [
                    {"command": "pip install -e ."},
                    {"command": "pytest tests"},
                ],
                "failed_actions": [
                    {"command": "pip install pytest"},
                ],
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            [
                "pip install -e .",
                "pytest tests",
                "ls -la",
                "redis-server --daemonize yes",
                "pip install pytest",
            ],
        )
        self.assertEqual(recipe["post_test_patch_commands"], ["npm install"])
        self.assertEqual(recipe["runtime_preparation_commands"], ["redis-server --daemonize yes"])
        self.assertEqual(recipe["test_commands"], ["pytest tests"])
        self.assertEqual(recipe["confidence"], "high")

    def test_build_recipe_preserves_failed_command_if_llm_outputs_it(self):
        synthesizer = Synthesizer()
        pip_install = 'pip install -e ".[testing]" Django django-configurations pytest-xdist pytest'
        safe_directory = "git config --global --add safe.directory /app"
        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    safe_directory,
                    pip_install,
                    "sed -i 's/foo/bar/' tests/test_manage_py_scan.py",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["PYTHONPATH=/app pytest tests/"],
                "excluded_commands": [],
                "rationale": "Install dependencies after marking /app as safe.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["PYTHONPATH=/app pytest tests/"],
                },
                "successful_actions": [
                    {"command": f"{safe_directory} && {pip_install}"},
                    {"command": "PYTHONPATH=/app pytest tests/"},
                ],
                "failed_actions": [
                    {"command": pip_install},
                ],
            },
        )

        self.assertIn(safe_directory, recipe["build_commands"])
        self.assertIn(pip_install, recipe["build_commands"])

    def test_build_recipe_can_override_final_bundle_runtime_and_test_commands(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": ["pip install -e ."],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["cd /app && python -m pytest tests/test_django_settings_module.py"],
                "excluded_commands": [],
                "rationale": "Avoid global Django settings export for tests that unset the variable.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [
                        "export DJANGO_SETTINGS_MODULE=pytest_django_test.settings_sqlite_file",
                    ],
                    "test_commands": ["python -m pytest tests"],
                },
            },
        )

        self.assertEqual(recipe["runtime_preparation_commands"], [])
        self.assertEqual(
            recipe["test_commands"],
            ["cd /app && python -m pytest tests/test_django_settings_module.py"],
        )

    def test_moves_patch_sensitive_test_file_rewrites_to_post_patch_commands(self):
        synthesizer = Synthesizer()
        rewrite_command = "sed -i 's/assertRegexpMatches/assertRegex/g' test/summary.t"
        helper_rewrite_command = "sed -i 's/isAlive/is_alive/g' test/basetest/utils.py"

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "cmake --build build --parallel",
                    rewrite_command,
                    helper_rewrite_command,
                ],
                "post_test_patch_commands": ["python -m compileall test"],
                "runtime_preparation_commands": [],
                "test_commands": ["cd test && python summary.t"],
                "excluded_commands": [],
                "rationale": "Python compatibility fixes are required.",
                "confidence": "high",
            },
            recipe_input={
                "test_patch": (
                    "diff --git a/test/summary.t b/test/summary.t\n"
                    "--- a/test/summary.t\n"
                    "+++ b/test/summary.t\n"
                    "@@ -1,2 +1,3 @@\n"
                    "+self.assertRegexpMatches(out, pattern)\n"
                ),
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["cd test && python summary.t"],
                },
            },
        )

        self.assertNotIn(rewrite_command, recipe["build_commands"])
        self.assertIn(helper_rewrite_command, recipe["build_commands"])
        self.assertEqual(
            recipe["post_test_patch_commands"],
            [rewrite_command, "python -m compileall test"],
        )

    def test_apply_build_recipe_drives_generated_dockerfile(self):
        synthesizer = Synthesizer(base_image="python:3.11", workdir="/app")
        synthesizer.record_success("pip install pytest")
        synthesizer.apply_build_recipe(
            {
                "build_commands": ["pip install -e ."],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["python -m pytest tests"],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile = synthesizer.generate_dockerfile(file_path=f"{tmpdir}/Dockerfile")

        self.assertIn("RUN pip install -e .", dockerfile)
        self.assertNotIn("RUN pip install pytest", dockerfile)

    def test_recorded_pip_requirement_specs_are_shell_quoted(self):
        synthesizer = Synthesizer(base_image="python:3.11", workdir="/app")
        synthesizer.record_success(
            "pip install packaging>=24 setuptools>=65.6.3 filelock>=3.12.3"
        )

        self.assertEqual(
            synthesizer.instructions,
            ["RUN pip install 'packaging>=24' 'setuptools>=65.6.3' 'filelock>=3.12.3'"],
        )

    def test_recipe_synthesis_logs_input_and_output(self):
        synthesizer = Synthesizer()
        response = (
            '{"build_commands": ["pip install -e ."], '
            '"post_test_patch_commands": [], '
            '"runtime_preparation_commands": [], '
            '"test_commands": ["python -m pytest tests"], '
            '"excluded_commands": [], '
            '"rationale": "editable install was verified", '
            '"confidence": "high"}'
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = synthesizer.synthesize_build_recipe(
                FakeRecipeClient(response),
                "fake-model",
                {
                    "final_verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["python -m pytest tests"],
                    }
                },
                log_dir=tmpdir,
            )
            log_path = f"{tmpdir}/recipe_synthesis.md"
            with open(log_path, encoding="utf-8") as file_obj:
                log_text = file_obj.read()

        self.assertEqual(result.recipe["build_commands"], ["pip install -e ."])
        self.assertIn("LLM INPUT (build recipe synthesis)", log_text)
        self.assertIn("Parsed Build Recipe", log_text)
        self.assertIn("fake-model", log_text)

    def test_recipe_extraction_ignores_non_recipe_json_in_reasoning(self):
        synthesizer = Synthesizer()
        response = """
<think>
The code contains self.global_names = {}, but that is not the recipe.
</think>
{"build_commands": ["pip install -e ."],
 "post_test_patch_commands": [],
 "runtime_preparation_commands": [],
 "test_commands": ["python -m pytest tests"],
 "excluded_commands": [],
 "rationale": "editable install was verified",
 "confidence": "high"}
"""

        recipe = synthesizer.extract_build_recipe_json(response)

        self.assertEqual(recipe["build_commands"], ["pip install -e ."])

    def test_recipe_synthesis_error_does_not_apply_empty_fallback_recipe(self):
        synthesizer = Synthesizer()
        synthesizer.record_success("pip install pytest")

        result = synthesizer.synthesize_build_recipe(
            FakeRecipeClient("<think>self.global_names = {}</think>"),
            "fake-model",
            {"final_verification_bundle": {"test_commands": ["pytest tests"]}},
        )

        self.assertEqual(result.recipe, {})
        self.assertEqual(result.source, "llm_error")
        self.assertIsNotNone(result.error)
        self.assertIn("RUN pip install pytest", synthesizer.instructions)

    def test_multiline_build_command_is_written_as_encoded_script(self):
        synthesizer = Synthesizer(base_image="python:3.11", workdir="/app")
        synthesizer.apply_build_recipe(
            {
                "build_commands": [
                    'cd /app && python -c "\nimport pathlib\npathlib.Path(\"x\").write_text(\"ok\")\n"'
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest tests"],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile = synthesizer.generate_dockerfile(file_path=f"{tmpdir}/Dockerfile")

        self.assertIn("base64 -d > /tmp/jayint_run_1.sh", dockerfile)
        self.assertNotIn("\nimport pathlib\n", dockerfile)
        encoded = re.search(r"printf '%s' '([^']+)'", dockerfile).group(1)
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertIn('python -c "\nimport pathlib\n', decoded)

    def test_heredoc_build_command_is_written_as_encoded_script(self):
        synthesizer = Synthesizer(base_image="python:3.11", workdir="/app")
        synthesizer.apply_build_recipe(
            {
                "build_commands": [
                    "cat > /tmp/example.py << 'PY'\nprint('ok')\nPY\npython /tmp/example.py"
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest tests"],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile = synthesizer.generate_dockerfile(file_path=f"{tmpdir}/Dockerfile")

        self.assertIn("base64 -d > /tmp/jayint_run_1.sh", dockerfile)
        self.assertNotIn("RUN cat > /tmp/example.py", dockerfile)
        encoded = re.search(r"printf '%s' '([^']+)'", dockerfile).group(1)
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, "cat > /tmp/example.py << 'PY'\nprint('ok')\nPY\npython /tmp/example.py")

    def test_dockerfile_apt_bootstrap_helper_can_emit_mirror_rewrite(self):
        instructions = build_dockerfile_apt_bootstrap_run_instructions(
            apt_mirror_url="https://mirror.example.com/ubuntu"
        )

        self.assertEqual(len(instructions), 2)
        self.assertIn("APT_MIRROR_URL='https://mirror.example.com/ubuntu'", instructions[1])
        self.assertIn("apt-get update", instructions[1])

    def test_safe_command_with_output_redirection_is_not_treated_as_readonly(self):
        synthesizer = Synthesizer()
        command = 'echo "hello" > /tmp/example.txt'

        self.assertFalse(synthesizer.is_readonly_command(command))

    def test_version_probe_with_fallback_is_not_recorded(self):
        synthesizer = Synthesizer()
        command = (
            'php --version && composer --version 2>/dev/null || '
            'echo "Need to check available tools"'
        )

        self.assertTrue(synthesizer.is_readonly_command(command))
        self.assertEqual(synthesizer._extract_recordable_setup_commands(command), [])
        synthesizer.record_success(command)
        self.assertEqual(synthesizer.instructions, [])

    def test_installer_fallback_after_probe_remains_recordable(self):
        synthesizer = Synthesizer()
        command = (
            "which git && which composer || "
            "(curl -sS https://getcomposer.org/installer | "
            "php -- --install-dir=/usr/local/bin --filename=composer 2>/dev/null)"
        )

        self.assertEqual(
            synthesizer._extract_recordable_setup_commands(command),
            [
                "(curl -sS https://getcomposer.org/installer | "
                "php -- --install-dir=/usr/local/bin --filename=composer 2>/dev/null)"
            ],
        )

    def test_public_runtime_command_wrappers_distinguish_service_and_healthcheck(self):
        synthesizer = Synthesizer()

        self.assertTrue(synthesizer.is_runtime_service_command("redis-server --daemonize yes"))
        self.assertTrue(synthesizer.is_runtime_healthcheck_command("redis-cli ping"))
        self.assertFalse(synthesizer.is_runtime_healthcheck_command("redis-server --daemonize yes"))

    def test_truncated_test_output_command_is_not_effective_test_signal(self):
        synthesizer = Synthesizer()
        command = "cd /app && python -m pytest tests -v 2>&1 | head -100"
        observation = "collected 207 items\n======================== 207 passed in 47.25s ========================"

        analysis = synthesizer.analyze_test_run(command, observation)

        self.assertTrue(synthesizer.is_truncated_test_output_command(command))
        self.assertTrue(analysis["is_test_command"])
        self.assertFalse(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "truncated_test_output")

    def test_grep_filtered_test_output_command_is_not_effective_test_signal(self):
        synthesizer = Synthesizer()
        command = 'python -m pytest tests -v 2>&1 | grep -E "(passed|failed|error)"'

        analysis = synthesizer.analyze_test_run(
            command,
            "======================== 207 passed in 47.25s ========================",
        )

        self.assertTrue(synthesizer.is_truncated_test_output_command(command))
        self.assertTrue(analysis["is_test_command"])
        self.assertFalse(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "truncated_test_output")

    def test_non_test_head_pipeline_is_not_truncated_test_output_command(self):
        synthesizer = Synthesizer()

        self.assertFalse(
            synthesizer.is_truncated_test_output_command(
                'find src/test -name "*.java" | head -10'
            )
        )


if __name__ == "__main__":
    unittest.main()
