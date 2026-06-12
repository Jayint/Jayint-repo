import base64
import re
import unittest
import tempfile
from types import SimpleNamespace

from src.synthesizer import (
    RECIPE_SYNTHESIS_SYSTEM_PROMPT,
    SETUP_LOG_SUMMARY_SYSTEM_PROMPT,
    Synthesizer,
    build_dockerfile_apt_bootstrap_run_instructions,
    build_dockerfile_pip_bootstrap_env_instructions,
    build_resilient_apt_install_run_instruction,
    build_resilient_pip_install_run_instruction,
)


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
    def test_recipe_prompt_forbids_reordering_or_merging_setup_trajectory(self):
        self.assertIn("Strictly follow the agent's setup trajectory order", RECIPE_SYNTHESIS_SYSTEM_PROMPT)
        self.assertIn("same relative order as the successful state-changing commands", RECIPE_SYNTHESIS_SYSTEM_PROMPT)
        self.assertIn("Do not sort, group, hoist, delay, or reorder commands", RECIPE_SYNTHESIS_SYSTEM_PROMPT)
        self.assertIn("Do not merge independent install/setup commands", RECIPE_SYNTHESIS_SYSTEM_PROMPT)
        self.assertIn("setup side effects are not algebraically mergeable", RECIPE_SYNTHESIS_SYSTEM_PROMPT)
        self.assertIn("do not merge it into a later replacement command", RECIPE_SYNTHESIS_SYSTEM_PROMPT)

    def test_setup_summary_prompt_preserves_successful_state_change_order(self):
        self.assertIn("successful state-changing commands must stay in the same relative order", SETUP_LOG_SUMMARY_SYSTEM_PROMPT)
        self.assertIn("exact relative order of successful state-changing commands", SETUP_LOG_SUMMARY_SYSTEM_PROMPT)

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

    def test_poetry_run_pytest_is_test_command_not_build_command(self):
        synthesizer = Synthesizer()

        self.assertTrue(
            synthesizer.is_test_command("poetry run pytest --collect-only -q --disable-warnings")
        )
        self.assertEqual(
            synthesizer._extract_recordable_setup_commands(
                "poetry run pytest --collect-only -q --disable-warnings"
            ),
            [],
        )

    def test_normalize_build_recipe_keeps_verified_runtime_and_coalesces_postgres_setup(self):
        synthesizer = Synthesizer()
        recipe = {
            "build_commands": [
                "pip install poetry",
                "poetry install --with test",
                "pg_ctlcluster 17 main start",
                'su - postgres -c "psql -c \\"CREATE USER raglite_user;\\""',
                'su - postgres -c "psql -c \\"ALTER USER raglite_user CREATEDB;\\""',
                "poetry run pytest --collect-only -q --disable-warnings",
            ],
            "runtime_preparation_commands": [],
            "test_commands": ["poetry run pytest --collect-only -q --disable-warnings"],
        }
        recipe_input = {
            "verification_bundle": {
                "runtime_preparation_commands": ["pg_ctlcluster 17 main start"],
                "test_commands": ["poetry run pytest --collect-only -q --disable-warnings"],
            },
            "successful_actions": [
                {"step_index": 1, "command": command}
                for command in recipe["build_commands"]
            ],
        }

        normalized = synthesizer.normalize_build_recipe(recipe, recipe_input=recipe_input)

        self.assertEqual(
            normalized["runtime_preparation_commands"],
            ["pg_ctlcluster 17 main start"],
        )
        self.assertTrue(
            any(
                "pg_ctlcluster 17 main start && su - postgres" in command
                for command in normalized["build_commands"]
            )
        )
        self.assertFalse(
            any("poetry run pytest" in command for command in normalized["build_commands"])
        )

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

    def test_extract_recordable_setup_commands_preserves_heredoc_file_write(self):
        synthesizer = Synthesizer()
        command = (
            "cat /app/src/common/processors/opencv_processors/bilateral_denoise_processor.py | head -20 "
            "&& echo \"---Creating workaround module---\" && "
            "cat > /app/src/common/processors/opencv_processors/means_doising_processor.py << 'EOF'\n"
            "# This file exists as a workaround for a bug in the test file\n"
            "# The test imports BilateralDenoiseProcessor from this misspelled module name\n"
            "# It should import from bilateral_denoise_processor instead\n"
            "from src.common.processors.opencv_processors.bilateral_denoise_processor import BilateralDenoiseProcessor\n"
            "EOF\n"
            "cat /app/src/common/processors/opencv_processors/means_doising_processor.py"
        )

        self.assertEqual(
            synthesizer._extract_recordable_setup_commands(command),
            [
                "cat > /app/src/common/processors/opencv_processors/means_doising_processor.py << 'EOF'\n"
                "# This file exists as a workaround for a bug in the test file\n"
                "# The test imports BilateralDenoiseProcessor from this misspelled module name\n"
                "# It should import from bilateral_denoise_processor instead\n"
                "from src.common.processors.opencv_processors.bilateral_denoise_processor import BilateralDenoiseProcessor\n"
                "EOF"
            ],
        )

    def test_extract_recordable_setup_commands_preserves_sed_rewrite_whitespace(self):
        synthesizer = Synthesizer()
        command = (
            "sed -i 's/^from flash_attn.bert_padding import index_first_axis, "
            "pad_input, unpad_input  # noqa$/    from flash_attn.bert_padding import "
            "index_first_axis, pad_input, unpad_input  # noqa\\n    FLASH_ATTN_AVAILABLE = True/' "
            "/app/MoA/kernels/mixture_of_attention.py 2>&1"
        )

        [recordable] = synthesizer._extract_recordable_setup_commands(command)

        self.assertIn("unpad_input  # noqa$/    from flash_attn", recordable)
        self.assertIn("# noqa\\n    FLASH_ATTN_AVAILABLE", recordable)

    def test_normalize_build_recipe_replays_moa_patch_and_pytest_ini_from_trajectory(self):
        synthesizer = Synthesizer()
        sed_import = (
            "sed -i 's/^from flash_attn import flash_attn_func, flash_attn_varlen_func$/try:\\n"
            "    from flash_attn import flash_attn_func, flash_attn_varlen_func/' "
            "/app/MoA/kernels/mixture_of_attention.py 2>&1"
        )
        sed_padding = (
            "sed -i 's/^from flash_attn.bert_padding import index_first_axis, pad_input, "
            "unpad_input  # noqa$/    from flash_attn.bert_padding import index_first_axis, "
            "pad_input, unpad_input  # noqa\\n    FLASH_ATTN_AVAILABLE = True\\nexcept ImportError:\\n"
            "    flash_attn_func = None/' /app/MoA/kernels/mixture_of_attention.py 2>&1"
        )
        pytest_ini = (
            'echo -e "[pytest]\\ntestpaths = tests\\n'
            'python_files = *_test.py test_*.py *_tests.py *_attention.py\\n'
            'python_classes = Test* *Test\\npython_functions = test_*" > /app/pytest.ini 2>&1'
        )
        recipe = {
            "build_commands": [
                "sed -i 's/^from flash_attn.bert_padding import index_first_axis, pad_input, "
                "unpad_input # noqa$/ from flash_attn.bert_padding import index_first_axis, "
                "pad_input, unpad_input # noqa\\n FLASH_ATTN_AVAILABLE = True/' "
                "/app/MoA/kernels/mixture_of_attention.py",
                "cd /app && pytest --collect-only -q --disable-warnings",
            ],
            "runtime_preparation_commands": [],
            "test_commands": ["cd /app && pytest --collect-only -q --disable-warnings"],
        }
        recipe_input = {
            "verification_bundle": {
                "runtime_preparation_commands": [],
                "test_commands": ["cd /app && pytest --collect-only -q --disable-warnings"],
            },
            "successful_actions": [
                {"step_index": 31, "command": sed_import},
                {"step_index": 32, "command": sed_padding},
                {"step_index": 50, "command": pytest_ini},
                {
                    "step_index": 52,
                    "command": "cd /app && pytest --collect-only -q --disable-warnings",
                    "observation_summary": "7 tests collected in 5.39s\n",
                },
            ],
        }

        normalized = synthesizer.normalize_build_recipe(recipe, recipe_input=recipe_input)

        self.assertIn(sed_import[:-5], normalized["build_commands"])
        self.assertIn(sed_padding[:-5], normalized["build_commands"])
        self.assertTrue(
            any(
                command.startswith("printf '%b'")
                and "python_files = *_test.py test_*.py *_tests.py *_attention.py" in command
                and command.endswith("> /app/pytest.ini")
                for command in normalized["build_commands"]
            )
        )
        self.assertFalse(
            any(command.startswith("cd /app && pytest") for command in normalized["build_commands"])
        )

    def test_normalize_echo_e_file_write_uses_printf_for_docker_shell_portability(self):
        synthesizer = Synthesizer()

        normalized = synthesizer._sanitize_build_commands_for_replay(
            ['echo -e "[pytest]\\ntestpaths = tests" > /app/pytest.ini']
        )

        self.assertEqual(
            normalized,
            ["printf '%b' '[pytest]\\ntestpaths = tests\\n' > /app/pytest.ini"],
        )

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
            dockerfile.index("RUN JAYINT_APT_ATTEMPT=1;"),
        )
        self.assertIn("apt-get update && apt-get install -y git", dockerfile)

    def test_build_recipe_normalization_drops_pure_test_and_failed_only_build_commands(self):
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
                "npm install",
            ],
        )
        self.assertEqual(recipe["post_test_patch_commands"], [])
        self.assertEqual(recipe["runtime_preparation_commands"], ["redis-server --daemonize yes"])
        self.assertEqual(recipe["test_commands"], ["pytest tests"])
        self.assertEqual(recipe["confidence"], "high")

    def test_build_recipe_normalization_drops_readonly_recipe_commands(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip list",
                    "cat React/model/roi_align/__init__.py",
                    "ls -la React/model/roi_align/src/",
                    "head -15 React/model/roi_align/roi_align.py",
                    "pip install -e .",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Keep only persistent setup commands.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["pytest --collect-only -q --disable-warnings"],
                },
            },
        )

        self.assertTrue(synthesizer.is_readonly_command("pip list"))
        self.assertEqual(recipe["build_commands"], ["pip install -e ."])

    def test_build_recipe_normalization_drops_python_import_and_pip_index_probes(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Trajectory-first replay should keep setup only.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["pytest --collect-only -q --disable-warnings"],
                },
                "successful_actions": [
                    {"command": 'python3 -c "import xr; print(dir(xr))"'},
                    {"command": "pip index versions pyopenxr"},
                    {"command": "pip install pyopenxr==1.0.3401"},
                ],
            },
        )

        self.assertTrue(synthesizer.is_readonly_command('python3 -c "import xr; print(dir(xr))"'))
        self.assertTrue(synthesizer.is_readonly_command("pip index versions pyopenxr"))
        self.assertFalse(
            synthesizer.is_readonly_command(
                'python3 -c "from pathlib import Path; Path(\\"x\\").write_text(\\"ok\\")"'
            )
        )
        self.assertEqual(recipe["build_commands"], ["pip install pyopenxr==1.0.3401"])

    def test_build_recipe_keeps_successful_pip_installs_with_resolver_conflict_warning(self):
        synthesizer = Synthesizer()
        resolver_warning = "\n".join(
            [
                "ERROR: pip's dependency resolver does not currently take into account all the packages that are installed.",
                "dbally 0.7.1 requires pandas~=2.0.3, but you have pandas 3.0.2 which is incompatible.",
                "WARNING: Running pip as the 'root' user can result in broken permissions.",
            ]
        )
        litellm_install = 'pip install "litellm>=1.37.9" "chromadb~=0.4.24" "tenacity~=8.3.0" --quiet'
        numpy_pin = 'pip install "numpy<2" --quiet'

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Replay trajectory order.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["pytest --collect-only -q --disable-warnings"],
                },
                "successful_actions": [
                    {"command": "pip install pytest", "observation_summary": ""},
                    {"command": litellm_install, "success": True, "observation_summary": resolver_warning},
                    {"command": numpy_pin, "success": True, "observation_summary": resolver_warning},
                ],
                "failed_actions": [
                    {"command": 'pip install -e ".[litellm,chromadb]" --quiet'},
                ],
            },
        )

        self.assertIn(litellm_install, recipe["build_commands"])
        self.assertIn(numpy_pin, recipe["build_commands"])

    def test_trajectory_replay_preserves_repeated_file_patch_after_reinstall(self):
        synthesizer = Synthesizer()
        patch_command = (
            "printf '\\nKnowledgeBase = KnowledgeBaseProcessor\\n' "
            ">> /usr/local/lib/python3.10/site-packages/bddl/knowledge_base/__init__.py"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Replay trajectory order.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["pytest --collect-only -q --disable-warnings"],
                },
                "successful_actions": [
                    {"command": patch_command},
                    {"command": "pip uninstall bddl -y"},
                    {"command": "pip install bddl==3.6.0"},
                    {"command": patch_command},
                ],
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            [
                patch_command,
                "pip uninstall bddl -y",
                "pip install bddl==3.6.0",
                patch_command,
            ],
        )

    def test_post_test_patch_commands_merge_into_build_without_test_patch(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": ["pip install -e ."],
                "post_test_patch_commands": [
                    "sed -i 's/old/new/g' src/module.py",
                    "python3 -c \"print('create import stubs')\"",
                ],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Post commands are replay-critical when no test patch exists.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["pytest --collect-only -q --disable-warnings"],
                }
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            [
                "pip install -e .",
                "sed -i 's/old/new/g' src/module.py",
                "python3 -c \"print('create import stubs')\"",
            ],
        )
        self.assertEqual(recipe["post_test_patch_commands"], [])

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

    def test_build_recipe_normalization_can_fall_back_to_agent_run_summary_bundle(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": ["pip install pytest"],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["python -m pytest tests"],
                "excluded_commands": [],
                "rationale": "pytest must exist in the image",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "verification_bundle": {
                        "runtime_preparation_commands": ["export APP_ENV=test"],
                        "test_commands": ["timeout 120 python -m pytest tests -v"],
                    }
                }
            },
        )

        self.assertEqual(recipe["runtime_preparation_commands"], [])
        self.assertEqual(recipe["test_commands"], ["python -m pytest tests"])

    def test_build_recipe_canonicalizes_llm_file_patch_command_to_observed_multiline_setup(self):
        synthesizer = Synthesizer()
        observed_command = (
            "python3 -c \"\n"
            "with open('/app/tests/utils_test/image_utils_test.py', 'r') as f:\n"
            "    content = f.read()\n"
            "with open('/app/tests/utils_test/image_utils_test.py', 'w') as f:\n"
            "    f.write(content.replace('E:/old', '/app/tests/test_data'))\n"
            "print('Fixed image_utils_test.py')\n"
            "\"\n"
            "LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 python3 -m pytest /app/tests/utils_test -v"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    (
                        "python3 -c \"with open('/app/tests/utils_test/image_utils_test.py', 'r') as f: "
                        "content = f.read(); with open('/app/tests/utils_test/image_utils_test.py', 'w') as f: "
                        "f.write(content); print('Fixed image_utils_test.py')\""
                    )
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["python -m pytest tests"],
                "excluded_commands": [],
                "rationale": "Reuse the file patch",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"command": observed_command},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["python -m pytest tests"],
                    },
                },
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            [
                "python3 -c \"\n"
                "with open('/app/tests/utils_test/image_utils_test.py', 'r') as f:\n"
                "    content = f.read()\n"
                "with open('/app/tests/utils_test/image_utils_test.py', 'w') as f:\n"
                "    f.write(content.replace('E:/old', '/app/tests/test_data'))\n"
                "print('Fixed image_utils_test.py')\n"
                "\""
            ],
        )

    def test_build_recipe_adds_omitted_observed_file_rewrite_before_later_observed_command(self):
        synthesizer = Synthesizer()
        missing_rewrite = (
            "sed -i 's/means_doising_processor/means_denoise_processor/g' "
            "/app/tests/test_processors/test_opencv_processors/bilateral_denoise_test.py"
        )
        later_rewrite = (
            "sed -i 's/BilateralDenoiseProcessor/MeansDenoiseProcessor/g' "
            "/app/tests/test_processors/test_opencv_processors/bilateral_denoise_test.py && "
            "sed -i 's/bilateral/means/g' "
            "/app/tests/test_processors/test_opencv_processors/bilateral_denoise_test.py 2>/dev/null"
        )
        first_later_rewrite = (
            "sed -i 's/BilateralDenoiseProcessor/MeansDenoiseProcessor/g' "
            "/app/tests/test_processors/test_opencv_processors/bilateral_denoise_test.py"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install pytest -q && pip install -e /app -q",
                    later_rewrite,
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "The final collection succeeded after test rewrites.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"command": "pip install pytest -q && pip install -e /app -q"},
                        {"command": missing_rewrite},
                        {"command": later_rewrite},
                        {"command": "pytest --collect-only -q --disable-warnings 2>&1"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertIn(missing_rewrite, recipe["build_commands"])
        self.assertLess(
            recipe["build_commands"].index(missing_rewrite),
            recipe["build_commands"].index(first_later_rewrite),
        )

    def test_build_recipe_does_not_promote_file_rewrite_prefix_from_failed_test_command(self):
        synthesizer = Synthesizer()
        rewrite_command = (
            "sed -i 's/registration.registry.env_specs/registration.registry/g' "
            "diffusion_policy/env/block_pushing/block_pushing.py"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": ["pip install -e ."],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Install project deps.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 5, "command": "pip install -e ."},
                    ],
                    "failed_actions": [
                        {
                            "step_index": 6,
                            "command": (
                                f"{rewrite_command} && "
                                "pytest --collect-only -q --disable-warnings 2>&1"
                            ),
                            "observation_summary": (
                                "tests/test_cv2_util.py::test\n"
                                "ERROR tests/test_robomimic_image_runner.py\n"
                                "3 errors during collection\n"
                            ),
                        },
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(recipe["build_commands"], ["pip install -e ."])
        self.assertNotIn(rewrite_command, recipe["build_commands"])

    def test_build_recipe_does_not_auto_add_failed_conftest_prefix(self):
        synthesizer = Synthesizer()
        conftest_command = (
            "cat > tests/conftest.py << 'EOF'\n"
            "import pytest\n"
            "EOF"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": ["pip install -e ."],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Install project deps.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 5, "command": "pip install -e ."},
                    ],
                    "failed_actions": [
                        {
                            "step_index": 6,
                            "command": (
                                f"{conftest_command}\n"
                                "pytest --collect-only -q --disable-warnings 2>&1"
                            ),
                            "observation_summary": (
                                "ERROR tests/test_robomimic_image_runner.py\n"
                                "2 errors during collection\n"
                            ),
                        },
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertNotIn(conftest_command, recipe["build_commands"])

    def test_build_recipe_does_not_promote_file_rewrite_prefix_from_failed_setup_chain(self):
        synthesizer = Synthesizer()
        rewrite_command = (
            "sed -i 's/registration.registry.env_specs/registration.registry/g' "
            "diffusion_policy/env/block_pushing/block_pushing_multimodal.py"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install 'git+https://github.com/facebookresearch/pytorch3d.git@stable'",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Install project deps.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {
                            "step_index": 8,
                            "command": "pip install 'git+https://github.com/facebookresearch/pytorch3d.git@stable'",
                        },
                        {
                            "step_index": 6,
                            "command": (
                                f"{rewrite_command} && "
                                "pip install pytorch3d 2>&1 | tail -10"
                            ),
                            "observation_summary": (
                                "ERROR: Could not find a version that satisfies the requirement pytorch3d\n"
                                "ERROR: No matching distribution found for pytorch3d\n"
                            ),
                        },
                    ],
                    "failed_actions": [],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            ["pip install 'git+https://github.com/facebookresearch/pytorch3d.git@stable'"],
        )
        self.assertNotIn(rewrite_command, recipe["build_commands"])

    def test_build_recipe_adds_omitted_root_relative_file_rewrite_before_install(self):
        synthesizer = Synthesizer()
        observed_command = (
            "cat >> pyproject.toml << 'EOF'\n\n"
            "[tool.setuptools.packages.find]\n"
            "include = [\"agentstack*\"]\n"
            "exclude = [\"logs*\", \"tests*\"]\n"
            "EOF\n"
            "cat pyproject.toml | tail -15"
        )
        expected_rewrite = (
            "cat >> pyproject.toml << 'EOF'\n\n"
            "[tool.setuptools.packages.find]\n"
            "include = [\"agentstack*\"]\n"
            "exclude = [\"logs*\", \"tests*\"]\n"
            "EOF"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install -e /app --no-build-isolation",
                    "pip install pytest parameterized",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Keep the successful package install.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"command": observed_command},
                        {"command": "pip install -e /app --no-build-isolation"},
                        {"command": "pytest --collect-only -q --disable-warnings 2>&1"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertIn(expected_rewrite, recipe["build_commands"])
        self.assertLess(
            recipe["build_commands"].index(expected_rewrite),
            recipe["build_commands"].index("pip install -e /app --no-build-isolation"),
        )

    def test_build_recipe_adds_observed_setuptools_wheel_bootstrap_before_no_build_isolation(self):
        synthesizer = Synthesizer()
        bootstrap_command = "pip install setuptools wheel --upgrade"

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install -e /app --no-build-isolation",
                    "pip install pytest parameterized",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Editable install is sufficient.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {
                            "command": (
                                "pip install setuptools wheel --upgrade && "
                                "pip install -e /app --no-build-isolation --no-deps 2>&1 | tail -20"
                            )
                        },
                        {"command": "pip install -e /app --no-build-isolation 2>&1 | tail -20"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertIn(bootstrap_command, recipe["build_commands"])
        self.assertLess(
            recipe["build_commands"].index(bootstrap_command),
            recipe["build_commands"].index("pip install -e /app --no-build-isolation"),
        )

    def test_build_recipe_adds_tox_declared_pytest_dependencies_when_missing(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install setuptools wheel --upgrade",
                    "pip install -e . --no-build-isolation",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Editable install should be enough.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {
                            "command": "cat tox.ini",
                            "observation_summary": (
                                "[tox]\n"
                                "envlist = py310\n\n"
                                "[testenv]\n"
                                "deps =\n"
                                "    pytest\n"
                                "    parameterized\n"
                                "    mypy: mypy\n"
                                "commands =\n"
                                "    pytest -v {posargs}\n"
                            ),
                        },
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertIn("pip install pytest parameterized", recipe["build_commands"])

    def test_build_recipe_replays_successful_exact_version_overrides_in_order(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip3 install -r /app/requirements.txt",
                    "pip3 install torchao==0.5.0",
                    "pip3 install transformers==4.45.2",
                    "pip3 install pytest",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["cd /app && pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Requirements plus final pins should be enough.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 7, "command": "pip3 install -r /app/requirements.txt 2>&1 | tail -20"},
                        {"step_index": 12, "command": "pip3 install torchao==0.5.0 2>&1 | tail -5"},
                        {"step_index": 14, "command": "pip3 install 'diffusers==0.30.3' 2>&1 | tail -5"},
                        {"step_index": 16, "command": "pip3 install 'diffusers==0.31.0' 2>&1 | tail -5"},
                        {"step_index": 18, "command": "pip3 install 'transformers==4.45.2' 2>&1 | tail -5"},
                        {"step_index": 19, "command": "cd /app && pytest --collect-only -q --disable-warnings 2>&1"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["cd /app && pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertIn("pip3 install 'diffusers==0.30.3'", recipe["build_commands"])
        self.assertIn("pip3 install 'diffusers==0.31.0'", recipe["build_commands"])
        self.assertLess(
            recipe["build_commands"].index("pip3 install 'diffusers==0.31.0'"),
            recipe["build_commands"].index("pip3 install 'transformers==4.45.2'"),
        )
        self.assertNotIn("pip3 install pytest", recipe["build_commands"])

    def test_build_recipe_strips_pure_test_commands_from_build_commands(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install pytest",
                    "pytest --collect-only -q --disable-warnings",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Keep pytest installed but do not build-time execute tests.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["pytest --collect-only -q --disable-warnings"],
                }
            },
        )

        self.assertEqual(recipe["build_commands"], ["pip install pytest"])

    def test_build_recipe_strips_test_suffix_from_mixed_setup_and_test_build_command(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install pytest && pytest --collect-only -q --disable-warnings",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Only the setup prefix should remain in the Dockerfile.",
                "confidence": "high",
            },
            recipe_input={
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["pytest --collect-only -q --disable-warnings"],
                }
            },
        )

        self.assertEqual(recipe["build_commands"], ["pip install pytest"])

    def test_build_recipe_drops_failed_only_build_command_and_failed_exact_version_override(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install pytest",
                    "pip install mujoco==2.1.0.13",
                    "pytest --collect-only -q --disable-warnings",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Drop commands that only have failed evidence.",
                "confidence": "medium",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {
                            "step_index": 1,
                            "command": "pip install pytest 2>&1 | tail -5",
                            "observation_summary": "Successfully installed pytest\n",
                        },
                        {
                            "step_index": 2,
                            "command": "pip install 'mujoco==2.1.0.13' 2>&1 | tail -10",
                            "observation_summary": (
                                "ERROR: Could not find a version that satisfies the requirement mujoco==2.1.0.13\n"
                                "ERROR: No matching distribution found for mujoco==2.1.0.13\n"
                            ),
                        },
                        {
                            "step_index": 3,
                            "command": "pytest --collect-only -q --disable-warnings 2>&1",
                            "observation_summary": "24 tests collected in 1.39s\n",
                            "test_analysis": {
                                "is_test_command": True,
                                "is_effective_test_run": True,
                                "confidence": "high",
                                "reason": "observed_test_execution_signal",
                            },
                        },
                    ],
                    "failed_actions": [
                        {
                            "step_index": 4,
                            "command": "pip uninstall mujoco_py -y && pip install mujoco==2.1.0.13 2>&1 | tail -10",
                            "observation_summary": (
                                "ERROR: Could not find a version that satisfies the requirement mujoco==2.1.0.13\n"
                                "ERROR: No matching distribution found for mujoco==2.1.0.13\n"
                            ),
                        }
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                }
            },
        )

        self.assertEqual(recipe["build_commands"], ["pip install pytest"])

    def test_build_recipe_does_not_merge_llm_dependency_additions_into_observed_install(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install pytest gym tqdm zarr atomics wandb pyrealsense2",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Keep the dependency install even if an earlier pytest-only command failed.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {
                            "step_index": 13,
                            "command": "pip install gym tqdm zarr atomics wandb pyrealsense2 2>&1 | tail -20",
                            "observation_summary": "Successfully installed gym zarr atomics wandb pyrealsense2 tqdm",
                        },
                    ],
                    "failed_actions": [
                        {
                            "step_index": 11,
                            "command": "pip install pytest && pytest --collect-only -q --disable-warnings",
                            "observation_summary": "ModuleNotFoundError: No module named 'gym'",
                        },
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            ["pip install gym tqdm zarr atomics wandb pyrealsense2"],
        )

    def test_build_recipe_does_not_supplement_cleanup_prefix_from_test_command(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install pytest",
                    "python3 -c \"print('create stubs')\"",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [
                    "rm tests/conftest.py (cleanup of conftest.py created during failed attempts)",
                ],
                "rationale": "Do not replay cleanup from failed exploratory conftest attempts.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {
                            "step_index": 51,
                            "command": (
                                "rm tests/conftest.py && "
                                "pytest --collect-only -q --disable-warnings "
                                "--ignore=tests/test_robomimic_image_runner.py"
                            ),
                            "observation_summary": "22 tests collected successfully",
                            "test_analysis": {
                                "is_test_command": True,
                                "is_effective_test_run": True,
                                "confidence": "high",
                            },
                        },
                        {
                            "step_index": 57,
                            "command": "pytest --collect-only -q --disable-warnings 2>&1",
                            "observation_summary": "24 tests collected in 7.06s",
                            "test_analysis": {
                                "is_test_command": True,
                                "is_effective_test_run": True,
                                "confidence": "high",
                            },
                        },
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            ["pip install pytest", "python3 -c \"print('create stubs')\""],
        )

    def test_build_recipe_drops_commands_explicitly_excluded_by_llm(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install pytest",
                    "rm tests/conftest.py",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [
                    "rm tests/conftest.py (cleanup of conftest.py created during failed attempts)",
                ],
                "rationale": "The exclusion should win over the build command conflict.",
                "confidence": "medium",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 1, "command": "pip install pytest"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(recipe["build_commands"], ["pip install pytest"])

    def test_trajectory_first_honors_exclusions_and_replays_later_package_restores(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip3 install robosuite==1.5.0 --no-cache-dir",
                    "pip3 uninstall robosuite -y",
                    "git clone https://github.com/ARISE-Initiative/robosuite",
                    "cd /app/robosuite && pip install -e . --no-cache-dir",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                "excluded_commands": [
                    {
                        "command": "git clone https://github.com/ARISE-Initiative/robosuite",
                        "reason": "abandoned source install",
                    },
                    {
                        "command": (
                            "cd /app/robosuite && pip install -e . --no-cache-dir "
                            "(source install was later abandoned)"
                        ),
                        "reason": "source install was replaced by pip wheel",
                    },
                    {
                        "command": "pip3 install -e /app (keep the no-cache-dir variant)",
                        "reason": "parenthesized note must not overmatch a different command variant",
                    },
                ],
                "rationale": "Final state returns to the pip release after a failed source attempt.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 1, "command": "pip3 install robosuite==1.5.0 --no-cache-dir"},
                        {"step_index": 2, "command": "pip3 install mujoco==3.2.6 --no-cache-dir"},
                        {"step_index": 3, "command": "pip3 install -e /app --no-cache-dir"},
                        {"step_index": 4, "command": "pip3 uninstall robosuite -y 2>&1"},
                        {"step_index": 5, "command": "git clone https://github.com/ARISE-Initiative/robosuite 2>&1"},
                        {"step_index": 6, "command": "cd /app/robosuite && pip install -e . --no-cache-dir 2>&1"},
                        {"step_index": 7, "command": "pip3 install mujoco==3.2.6 --no-cache-dir 2>&1"},
                        {"step_index": 8, "command": "pip3 uninstall robosuite -y 2>&1"},
                        {"step_index": 9, "command": "pip3 install robosuite==1.5.0 --no-cache-dir 2>&1"},
                        {
                            "step_index": 10,
                            "command": "printf '%s\\n' 'import robosuite' > /app/tests/conftest.py",
                        },
                        {"step_index": 11, "command": "pip3 install mujoco==3.2.6 --no-cache-dir 2>&1"},
                        {
                            "step_index": 12,
                            "command": "pytest /app/tests --collect-only -q --disable-warnings 2>&1",
                        },
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertNotIn(
            "git clone https://github.com/ARISE-Initiative/robosuite",
            recipe["build_commands"],
        )
        self.assertNotIn(
            "cd /app/robosuite && pip install -e . --no-cache-dir",
            recipe["build_commands"],
        )
        self.assertIn("pip3 install -e /app --no-cache-dir", recipe["build_commands"])
        self.assertEqual(
            recipe["build_commands"].count("pip3 install robosuite==1.5.0 --no-cache-dir"),
            2,
        )
        self.assertEqual(recipe["build_commands"].count("pip3 uninstall robosuite -y"), 2)
        self.assertEqual(
            recipe["build_commands"].count("pip3 install mujoco==3.2.6 --no-cache-dir"),
            3,
        )
        self.assertLess(
            recipe["build_commands"].index("pip3 uninstall robosuite -y"),
            recipe["build_commands"].index("printf '%s\\n' 'import robosuite' > /app/tests/conftest.py"),
        )
        self.assertLess(
            recipe["build_commands"].index("printf '%s\\n' 'import robosuite' > /app/tests/conftest.py"),
            len(recipe["build_commands"]) - 1,
        )

    def test_excluded_compound_command_drops_matching_split_subcommands(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip3 install robosuite==1.5.0 --no-cache-dir",
                    "git clone https://github.com/ARISE-Initiative/robosuite",
                    "cd /app/robosuite && pip install -e . --no-cache-dir",
                    "pip3 install robosuite==1.5.0 --no-cache-dir",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                "excluded_commands": [
                    {
                        "command": (
                            "git clone https://github.com/ARISE-Initiative/robosuite && "
                            "cd /app/robosuite && pip install -e . --no-cache-dir"
                        ),
                        "reason": "source install was abandoned and replaced by PyPI reinstall",
                    },
                ],
                "rationale": "Compound excluded command should remove the same split replay steps.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 1, "command": "pip3 install robosuite==1.5.0 --no-cache-dir"},
                        {"step_index": 2, "command": "git clone https://github.com/ARISE-Initiative/robosuite 2>&1"},
                        {"step_index": 3, "command": "cd /app/robosuite && pip install -e . --no-cache-dir 2>&1"},
                        {"step_index": 4, "command": "pip3 install robosuite==1.5.0 --no-cache-dir 2>&1"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertNotIn(
            "git clone https://github.com/ARISE-Initiative/robosuite",
            recipe["build_commands"],
        )
        self.assertNotIn(
            "cd /app/robosuite && pip install -e . --no-cache-dir",
            recipe["build_commands"],
        )
        self.assertEqual(
            recipe["build_commands"],
            [
                "pip3 install robosuite==1.5.0 --no-cache-dir",
                "pip3 install robosuite==1.5.0 --no-cache-dir",
            ],
        )

    def test_excluded_step_range_drops_only_matching_trajectory_occurrences(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip3 install mujoco==3.2.6 --no-cache-dir",
                    "pip3 install robosuite==1.5.0 --no-cache-dir",
                    "git clone https://github.com/ARISE-Initiative/robosuite",
                    "cd /app/robosuite && pip install -e . --no-cache-dir",
                    "pip3 install mujoco==3.2.6 --no-cache-dir",
                    "pip3 install robosuite==1.5.0 --no-cache-dir",
                    "pip3 install mujoco==3.2.6 --no-cache-dir",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                "excluded_commands": [
                    {
                        "command": (
                            "Step 3-5 (failed attempts): source install of robosuite from git was "
                            "transient and did not persist in the final environment."
                        ),
                        "reason": "",
                    },
                ],
                "rationale": "Narrative exclusions should still map back to setup trajectory steps.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 1, "command": "pip3 install mujoco==3.2.6 --no-cache-dir"},
                        {"step_index": 2, "command": "pip3 install robosuite==1.5.0 --no-cache-dir"},
                        {"step_index": 3, "command": "git clone https://github.com/ARISE-Initiative/robosuite 2>&1"},
                        {"step_index": 4, "command": "cd /app/robosuite && pip install -e . --no-cache-dir 2>&1"},
                        {"step_index": 5, "command": "pip3 install mujoco==3.2.6 --no-cache-dir 2>&1"},
                        {"step_index": 6, "command": "pip3 install robosuite==1.5.0 --no-cache-dir 2>&1"},
                        {"step_index": 7, "command": "pip3 install mujoco==3.2.6 --no-cache-dir 2>&1"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertNotIn(
            "git clone https://github.com/ARISE-Initiative/robosuite",
            recipe["build_commands"],
        )
        self.assertNotIn(
            "cd /app/robosuite && pip install -e . --no-cache-dir",
            recipe["build_commands"],
        )
        self.assertEqual(
            recipe["build_commands"],
            [
                "pip3 install mujoco==3.2.6 --no-cache-dir",
                "pip3 install robosuite==1.5.0 --no-cache-dir",
                "pip3 install robosuite==1.5.0 --no-cache-dir",
                "pip3 install mujoco==3.2.6 --no-cache-dir",
            ],
        )

    def test_excluded_git_clone_drops_dependent_editable_install_from_clone_dir(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "git clone https://github.com/ARISE-Initiative/robosuite",
                    "cd /app/robosuite && pip install -e . --no-cache-dir",
                    "pip3 install -e /app --no-cache-dir",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                "excluded_commands": [
                    {
                        "command": "git clone https://github.com/ARISE-Initiative/robosuite",
                        "reason": "source checkout was abandoned",
                    }
                ],
                "rationale": "A local install from an excluded clone directory cannot be replayed.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 1, "command": "git clone https://github.com/ARISE-Initiative/robosuite 2>&1"},
                        {"step_index": 2, "command": "cd /app/robosuite && pip install -e . --no-cache-dir 2>&1"},
                        {"step_index": 3, "command": "pip3 install -e /app --no-cache-dir"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(recipe["build_commands"], ["pip3 install -e /app --no-cache-dir"])

    def test_apply_build_recipe_preserves_repeated_package_mutations(self):
        synthesizer = Synthesizer()

        synthesizer.apply_build_recipe(
            {
                "build_commands": [
                    "pip3 install robosuite==1.5.0 --no-cache-dir",
                    "pip3 uninstall robosuite -y",
                    "pip3 install robosuite==1.5.0 --no-cache-dir",
                ]
            }
        )

        self.assertEqual(
            synthesizer.instructions.count("RUN pip3 install robosuite==1.5.0 --no-cache-dir"),
            2,
        )
        self.assertIn("RUN pip3 uninstall robosuite -y", synthesizer.instructions)

    def test_excluded_pip_install_drops_orphaned_matching_uninstall(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip3 install -e /app --no-cache-dir",
                    "pip3 uninstall robosuite -y",
                    "pip3 install mujoco==3.2.6 --no-cache-dir",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                "excluded_commands": [
                    {
                        "command": "pip3 install robosuite==1.5.0",
                        "reason": "local editable install already resolves this dependency",
                    },
                ],
                "rationale": "If the install is excluded, the matching cleanup uninstall is not replayable.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"step_index": 1, "command": "pip3 install -e /app --no-cache-dir"},
                        {"step_index": 2, "command": "pip3 uninstall robosuite -y"},
                        {"step_index": 3, "command": "pip3 install mujoco==3.2.6 --no-cache-dir"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest /app/tests --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            [
                "pip3 install -e /app --no-cache-dir",
                "pip3 install mujoco==3.2.6 --no-cache-dir",
            ],
        )

    def test_build_recipe_preserves_observed_non_repository_file_rewrite(self):
        synthesizer = Synthesizer()
        recipe_command = (
            "mkdir -p /usr/local/lib/python3.9/site-packages/robomimic/envs && "
            "cat > /usr/local/lib/python3.9/site-packages/robomimic/__init__.py << 'EOF'\n"
            "# Mock robomimic module for testing purposes\n"
            "EOF"
        )
        observed_command = (
            "cat > /usr/local/lib/python3.9/site-packages/robomimic/__init__.py << 'EOF'\n"
            "# Mock robomimic module for testing purposes\n"
            "EOF\n"
            "cat > /usr/local/lib/python3.9/site-packages/robomimic/envs/__init__.py << 'EOF'\n"
            "# Mock robomimic.envs module\n"
            "EOF"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [recipe_command],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Preserve the original site-packages rewrite command.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"command": observed_command},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                }
            },
        )

        self.assertEqual(recipe["build_commands"], [observed_command])

    def test_pytest_collect_only_observation_with_tests_collected_counts_as_effective(self):
        synthesizer = Synthesizer()

        analysis = synthesizer.analyze_test_run(
            "pytest --collect-only -q --disable-warnings",
            "22 tests collected in 1.39s\n",
        )

        self.assertTrue(analysis["is_test_command"])
        self.assertTrue(analysis["is_effective_test_run"])
        self.assertEqual(analysis["reason"], "observed_test_execution_signal")

    def test_build_recipe_restores_observed_package_manager_install_chain(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install cmake && pip install robomimic",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Install robomimic after cmake.",
                "confidence": "medium",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {
                            "command": "apt-get install -y cmake && pip install robomimic 2>&1 | tail -20",
                            "observation_summary": "Successfully installed egl_probe robomimic",
                        },
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            ["apt-get install -y cmake", "pip install robomimic"],
        )

    def test_build_recipe_preserves_observed_robomimic_install_when_summary_mentions_mujoco_py(self):
        synthesizer = Synthesizer()

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "pip install mujoco-py",
                    "pip install robomimic",
                    "pip install 'cython<3' --force-reinstall",
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Keep the original successful install commands.",
                "confidence": "medium",
            },
            recipe_input={
                "setup_log_summary_text": (
                    "Step 74\n"
                    "Outcome: mujoco_py is fundamentally incompatible with MuJoCo 3.x Cython bindings.\n"
                    "Step 83-86\n"
                    "Outcome: mujoco_py requires MuJoCo 2.x binaries at ~/.mujoco/mujoco210.\n"
                ),
                "agent_run_summary": {
                    "successful_actions": [
                        {"command": "pip install mujoco-py"},
                        {"command": "pip install --no-deps robomimic"},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": [],
                        "test_commands": ["pytest --collect-only -q --disable-warnings"],
                    },
                },
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            [
                "pip install mujoco-py",
                "pip install --no-deps robomimic",
            ],
        )

    def test_build_recipe_canonicalization_preserves_multiline_heredoc_setup_command(self):
        synthesizer = Synthesizer()
        observed_command = (
            "cat /app/src/common/processors/opencv_processors/bilateral_denoise_processor.py | head -20 "
            "&& echo \"---Creating workaround module---\" && "
            "cat > /app/src/common/processors/opencv_processors/means_doising_processor.py << 'EOF'\n"
            "# This file exists as a workaround for a bug in the test file\n"
            "# The test imports BilateralDenoiseProcessor from this misspelled module name\n"
            "# It should import from bilateral_denoise_processor instead\n"
            "from src.common.processors.opencv_processors.bilateral_denoise_processor import BilateralDenoiseProcessor\n"
            "EOF\n"
            "cat /app/src/common/processors/opencv_processors/means_doising_processor.py"
        )
        heredoc_command = (
            "cat > /app/src/common/processors/opencv_processors/means_doising_processor.py << 'EOF'\n"
            "# This file exists as a workaround for a bug in the test file\n"
            "# The test imports BilateralDenoiseProcessor from this misspelled module name\n"
            "# It should import from bilateral_denoise_processor instead\n"
            "from src.common.processors.opencv_processors.bilateral_denoise_processor import BilateralDenoiseProcessor\n"
            "EOF"
        )

        recipe = synthesizer.normalize_build_recipe(
            {
                "build_commands": [
                    "apt-get update && apt-get install -y ffmpeg",
                    heredoc_command,
                ],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": ["export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8"],
                "test_commands": ["cd /app && pytest --collect-only -q --disable-warnings"],
                "excluded_commands": [],
                "rationale": "Preserve the successful workaround module creation.",
                "confidence": "high",
            },
            recipe_input={
                "agent_run_summary": {
                    "successful_actions": [
                        {"command": "apt-get update && apt-get install -y ffmpeg"},
                        {"command": observed_command},
                    ],
                    "verification_bundle": {
                        "runtime_preparation_commands": ["export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8"],
                        "test_commands": ["cd /app && pytest --collect-only -q --disable-warnings"],
                    },
                }
            },
        )

        self.assertEqual(
            recipe["build_commands"],
            [
                "apt-get update && apt-get install -y ffmpeg",
                heredoc_command,
            ],
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

        self.assertIn("PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install -e .'", dockerfile)
        self.assertNotIn("PIP_NO_CACHE_DIR=1 /bin/sh -lc 'python -m pytest tests'", dockerfile)

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
                    "setup_log_summary_text": (
                        "Step 1-2\n"
                        "Type: failed_attempts\n"
                        "Goal: locate the project root\n"
                        "Attempts:\n"
                        "- Step 1: cat /repo/pyproject.toml -> file not found\n"
                        "- Step 2: ls /repo -> directory not found\n"
                        "Outcome: repo was not under /repo\n\n"
                        "Step 3\n"
                        "Type: successful_state_change\n"
                        "Thought: install project\n"
                        "Action: pip install -e .\n"
                        "Observation: installed package"
                    ),
                    "agent_run_summary": {
                        "verification_bundle": {
                            "runtime_preparation_commands": [],
                            "test_commands": ["python -m pytest tests"],
                        }
                    },
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
        self.assertIn("setup_log_summary_text", log_text)
        self.assertIn("agent_run_summary", log_text)

    def test_setup_log_summary_logs_input_and_output(self):
        synthesizer = Synthesizer()
        summary_text = (
            "Step 1-2\n"
            "Type: read_only_inspection\n"
            "Goal: inspect dependency files\n"
            "Attempts:\n"
            "- Step 1: cat requirements.txt -> found pytest and numpy\n"
            "- Step 2: cat pyproject.toml -> no extra dependencies\n"
            "Outcome: requirements.txt is the dependency source of truth\n\n"
            "Step 3\n"
            "Type: successful_state_change\n"
            "Thought: install dependencies\n"
            "Action: pip install -r requirements.txt\n"
            "Observation: Installed pytest and numpy"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = synthesizer.summarize_setup_log_for_recipe(
                FakeRecipeClient(summary_text),
                "fake-model",
                (
                    "Step 1\n"
                    "Thought: inspect requirements\n"
                    "Action: cat requirements.txt\n"
                    "Observation:\npytest\nnumpy"
                ),
                log_dir=tmpdir,
            )
            log_path = f"{tmpdir}/setup_log_summary.md"
            with open(log_path, encoding="utf-8") as file_obj:
                log_text = file_obj.read()

        self.assertEqual(result.summary_text, summary_text)
        self.assertIn("LLM INPUT (setup log summary)", log_text)
        self.assertIn("Parsed Summary", log_text)
        self.assertIn("fake-model", log_text)
        self.assertIn("inspect requirements", log_text)
        self.assertIn("Installed pytest and numpy", log_text)
        self.assertIn("Type: successful_state_change", log_text)

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

    def test_recipe_extraction_prefers_last_recipe_after_thinking_and_examples(self):
        synthesizer = Synthesizer()
        response = """
<think>
```dockerfile
RUN echo '{"looks": "like json but is not the recipe"}'
RUN python -c "config = {'broken': True"
```
</think>
The Dockerfile would look like this:
```dockerfile
RUN echo '{"example": true}'
```
```json
{"build_commands": ["pip install wrong"],
 "post_test_patch_commands": [],
 "runtime_preparation_commands": [],
 "test_commands": ["pytest wrong"],
 "excluded_commands": [],
 "rationale": "format example",
 "confidence": "low"}
```
Final answer:
{"build_commands": ["pip install -e ."],
 "post_test_patch_commands": [],
 "runtime_preparation_commands": [],
 "test_commands": ["pytest --collect-only -q --disable-warnings"],
 "excluded_commands": [],
 "rationale": "use the verified trajectory",
 "confidence": "high"}
"""

        recipe = synthesizer.extract_build_recipe_json(response)

        self.assertEqual(recipe["build_commands"], ["pip install -e ."])
        self.assertEqual(recipe["confidence"], "high")

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

    def test_recipe_synthesis_uses_trajectory_fallback_after_invalid_llm_recipe(self):
        synthesizer = Synthesizer()

        result = synthesizer.synthesize_build_recipe(
            FakeRecipeClient("<think>not json</think>"),
            "fake-model",
            {
                "successful_actions": [{"command": "pip install -e ."}],
                "final_verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["pytest --collect-only -q --disable-warnings"],
                },
            },
        )

        self.assertEqual(result.source, "deterministic_fallback_after_llm_error")
        self.assertIsNone(result.error)
        self.assertEqual(result.recipe["build_commands"], ["pip install -e ."])
        self.assertEqual(
            result.recipe["test_commands"],
            ["pytest --collect-only -q --disable-warnings"],
        )

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

    def test_dockerfile_pip_bootstrap_helper_emits_timeout_and_retry_envs(self):
        instructions = build_dockerfile_pip_bootstrap_env_instructions(
            pip_timeout_seconds=420,
            pip_retries=7,
        )

        self.assertEqual(
            instructions,
            [
                "ENV PIP_DISABLE_PIP_VERSION_CHECK=1",
                "ENV PIP_DEFAULT_TIMEOUT=420",
                "ENV PIP_RETRIES=7",
            ],
        )

    def test_resilient_pip_install_run_instruction_wraps_retry_loop(self):
        instruction = build_resilient_pip_install_run_instruction(
            "pip install numpy scipy torch",
            max_attempts=4,
            retry_delay_seconds=9,
        )

        self.assertTrue(instruction.startswith("RUN JAYINT_PIP_ATTEMPT=1;"))
        self.assertIn("JAYINT_PIP_MAX_ATTEMPTS=4", instruction)
        self.assertIn("PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install numpy scipy torch'", instruction)
        self.assertIn("pip cache purge", instruction)
        self.assertIn("sleep 9", instruction)

    def test_resilient_apt_install_run_instruction_wraps_retry_loop_and_update(self):
        instruction = build_resilient_apt_install_run_instruction(
            "apt-get install -y libgl1 libglib2.0-0",
            max_attempts=4,
            retry_delay_seconds=9,
        )

        self.assertTrue(instruction.startswith("RUN JAYINT_APT_ATTEMPT=1;"))
        self.assertIn("JAYINT_APT_MAX_ATTEMPTS=4", instruction)
        self.assertIn(
            "DEBIAN_FRONTEND=noninteractive /bin/sh -lc 'apt-get update && apt-get install -y libgl1 libglib2.0-0'",
            instruction,
        )
        self.assertIn("apt-get clean", instruction)
        self.assertIn("sleep 9", instruction)

    def test_generated_dockerfile_wraps_apt_install_run_with_retries(self):
        synthesizer = Synthesizer(base_image="python:3.11", workdir="/app")
        synthesizer.record_success("apt-get install -y libgl1 libglib2.0-0")

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile = synthesizer.generate_dockerfile(file_path=f"{tmpdir}/Dockerfile")

        self.assertIn("RUN JAYINT_APT_ATTEMPT=1;", dockerfile)
        self.assertIn(
            "DEBIAN_FRONTEND=noninteractive /bin/sh -lc 'apt-get update && apt-get install -y libgl1 libglib2.0-0'",
            dockerfile,
        )
        self.assertNotIn("RUN apt-get install -y libgl1 libglib2.0-0\n", dockerfile)

    def test_generated_dockerfile_wraps_pip_install_run_with_retries(self):
        synthesizer = Synthesizer(base_image="python:3.11", workdir="/app")
        synthesizer.record_success("pip install numpy scipy torch")

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile = synthesizer.generate_dockerfile(file_path=f"{tmpdir}/Dockerfile")

        self.assertIn("RUN JAYINT_PIP_ATTEMPT=1;", dockerfile)
        self.assertIn("PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install numpy scipy torch'", dockerfile)
        self.assertNotIn("RUN pip install numpy scipy torch\n", dockerfile)

    def test_generated_dockerfile_includes_pip_bootstrap_envs_before_run_instructions(self):
        synthesizer = Synthesizer(base_image="python:3.11", workdir="/app")
        synthesizer.record_success("pip install pytest")

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile = synthesizer.generate_dockerfile(file_path=f"{tmpdir}/Dockerfile")

        self.assertIn("ENV PIP_DISABLE_PIP_VERSION_CHECK=1", dockerfile)
        self.assertIn("ENV PIP_DEFAULT_TIMEOUT=300", dockerfile)
        self.assertIn("ENV PIP_RETRIES=5", dockerfile)
        self.assertLess(
            dockerfile.index("ENV PIP_DEFAULT_TIMEOUT=300"),
            dockerfile.index("PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install pytest'"),
        )

    def test_safe_command_with_output_redirection_is_not_treated_as_readonly(self):
        synthesizer = Synthesizer()
        command = 'echo "hello" > /tmp/example.txt'

        self.assertFalse(synthesizer.is_readonly_command(command))

    def test_output_truncation_cleanup_preserves_simple_file_redirection(self):
        synthesizer = Synthesizer()
        command = 'echo "hello" > /tmp/example.txt 2>&1'

        self.assertEqual(
            synthesizer._strip_output_truncation_suffix(command),
            'echo "hello" > /tmp/example.txt',
        )

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


class ObservationEnvDefectSignalTests(unittest.TestCase):
    """Fix 3 §6 truth table: env-defect classifier fires ONLY on broken-environment
    failures, never on pre-existing source/assertion bugs."""

    def setUp(self):
        self.synth = Synthesizer()

    def test_module_not_found_dep_is_env_defect(self):
        self.assertTrue(self.synth.observation_has_env_defect_signal(
            "ModuleNotFoundError: No module named 'fastapi'"))

    def test_internal_tests_module_is_not_env_defect(self):
        # tests.* topology issue, not a missing dependency
        self.assertFalse(self.synth.observation_has_env_defect_signal(
            "ModuleNotFoundError: No module named 'tests.test_x'"))

    def test_cannot_import_name_is_env_defect(self):
        self.assertTrue(self.synth.observation_has_env_defect_signal(
            "ImportError: cannot import name 'edsl' from 'edsl'"))

    def test_error_collecting_is_env_defect(self):
        self.assertTrue(self.synth.observation_has_env_defect_signal(
            "ERROR collecting tests/foo.py"))

    def test_internalerror_is_env_defect(self):
        self.assertTrue(self.synth.observation_has_env_defect_signal(
            "INTERNALERROR> Traceback ... conftest.py"))

    def test_connection_refused_is_env_defect(self):
        self.assertTrue(self.synth.observation_has_env_defect_signal(
            "ConnectionRefusedError: [Errno 111] Connection refused"))

    def test_command_not_found_is_env_defect(self):
        self.assertTrue(self.synth.observation_has_env_defect_signal(
            "pytest: command not found"))

    def test_assertion_error_is_not_env_defect(self):
        self.assertFalse(self.synth.observation_has_env_defect_signal(
            "AssertionError: assert 1 == 2"))

    def test_attribute_and_type_error_not_env_defect(self):
        self.assertFalse(self.synth.observation_has_env_defect_signal("AttributeError: 'X' object ..."))
        self.assertFalse(self.synth.observation_has_env_defect_signal("TypeError: unexpected keyword ..."))

    def test_bare_n_failed_is_not_env_defect(self):
        self.assertFalse(self.synth.observation_has_env_defect_signal("5 failed in 2.3s"))

    def test_collected_zero_with_error_is_env_defect(self):
        self.assertTrue(self.synth.observation_has_env_defect_signal(
            "collected 0 items\nERROR: file or directory not found"))

    def test_ansi_wrapped_module_not_found_is_env_defect(self):
        self.assertTrue(self.synth.observation_has_env_defect_signal(
            "\x1b[31mModuleNotFoundError: No module named 'numpy'\x1b[0m"))


class ObservationPassingTestSignalTests(unittest.TestCase):
    """Fix 3 §6: passing signal requires >=1 PASSED; rejects 0-passed and collect-only."""

    def setUp(self):
        self.synth = Synthesizer()

    def test_n_passed_is_passing(self):
        self.assertTrue(self.synth.observation_has_passing_test_signal("1601 passed, 2 failed in 9s"))

    def test_zero_passed_is_not_passing(self):
        # C2 hollow-pass guard: '0 passed' must NOT register as a pass.
        self.assertFalse(self.synth.observation_has_passing_test_signal("5 failed, 0 passed"))

    def test_collect_only_is_not_passing(self):
        # C3 guard: 'collected N items' alone is not a pass.
        self.assertFalse(self.synth.observation_has_passing_test_signal("collected 150 items"))

    def test_empty_is_not_passing(self):
        self.assertFalse(self.synth.observation_has_passing_test_signal(""))

    def test_bare_failed_is_not_passing(self):
        self.assertFalse(self.synth.observation_has_passing_test_signal("5 failed in 2.3s"))


class ObservationPassRatioTests(unittest.TestCase):
    """Fix 3 §6: passed/(passed+failed+errors); skips excluded; None when uncountable."""

    def setUp(self):
        self.synth = Synthesizer()

    def test_high_ratio(self):
        self.assertAlmostEqual(self.synth.observation_pass_ratio("1601 passed, 2 failed"), 1601 / 1603, places=4)

    def test_minority_ratio(self):
        self.assertAlmostEqual(self.synth.observation_pass_ratio("26 passed, 33 failed"), 26 / 59, places=4)

    def test_boundary_half(self):
        self.assertAlmostEqual(self.synth.observation_pass_ratio("5 passed, 5 failed, 0 errors"), 0.5, places=4)

    def test_skips_excluded(self):
        self.assertAlmostEqual(self.synth.observation_pass_ratio("10 passed, 2 skipped"), 1.0, places=4)

    def test_uncountable_is_none(self):
        self.assertIsNone(self.synth.observation_pass_ratio("collected 5 items"))

    def test_min_pass_ratio_constant_is_half(self):
        self.assertEqual(Synthesizer.MIN_PASS_RATIO, 0.5)


if __name__ == "__main__":
    unittest.main()
