import unittest
import tempfile

from src.synthesizer import Synthesizer, build_dockerfile_apt_bootstrap_run_instructions


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


if __name__ == "__main__":
    unittest.main()
