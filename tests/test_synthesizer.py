import unittest

from src.synthesizer import Synthesizer


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

    def test_public_observation_signal_wrapper_detects_real_test_output(self):
        synthesizer = Synthesizer()

        self.assertTrue(
            synthesizer.observation_has_effective_test_signal("OK (94 tests, 185 assertions)")
        )

    def test_public_runtime_command_wrappers_distinguish_service_and_healthcheck(self):
        synthesizer = Synthesizer()

        self.assertTrue(synthesizer.is_runtime_service_command("redis-server --daemonize yes"))
        self.assertTrue(synthesizer.is_runtime_healthcheck_command("redis-cli ping"))
        self.assertFalse(synthesizer.is_runtime_healthcheck_command("redis-server --daemonize yes"))


if __name__ == "__main__":
    unittest.main()
