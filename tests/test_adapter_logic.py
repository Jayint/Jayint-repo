import base64
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from multi_docker_eval_adapter import MultiDockerEvalAdapter


class FakeRepairClient:
    def __init__(self, content):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
                )
            )
        )


class AdapterLogicTests(unittest.TestCase):
    def test_builds_benchmark_evaluation_target_from_test_patch_metadata_only(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """diff --git a/test/resize.t b/test/resize.t
--- a/test/resize.t
+++ b/test/resize.t
@@ -1,3 +1,6 @@
+class TestResize(TestCase):
+    def test_resize_full_month_interval(self):
+        self.assertIn('Resized @1 to 720:00:00', out)
diff --git a/src/Range.cpp b/src/Range.cpp
--- a/src/Range.cpp
+++ b/src/Range.cpp
@@ -1,2 +1,2 @@
 int x = 1;
"""

        target = adapter.build_benchmark_evaluation_target(test_patch)

        self.assertEqual(target["changed_test_files"], ["test/resize.t"])
        self.assertIn("python unittest", target["test_framework_clues"])
        self.assertNotIn("src/Range.cpp", target["changed_test_files"])
        self.assertNotIn("Resized @1 to 720:00:00", json.dumps(target))

    def test_builds_benchmark_evaluation_target_for_javascript_spec_patch(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """diff --git a/packages/foo/__tests__/bar.spec.ts b/packages/foo/__tests__/bar.spec.ts
--- a/packages/foo/__tests__/bar.spec.ts
+++ b/packages/foo/__tests__/bar.spec.ts
@@ -1,2 +1,4 @@
+describe('bar', () => {
+  it('works', () => expect(true).toBe(true))
+})
"""

        target = adapter.build_benchmark_evaluation_target(test_patch)

        self.assertEqual(
            target["changed_test_files"],
            ["packages/foo/__tests__/bar.spec.ts"],
        )
        self.assertIn("Jest/Vitest-style JS tests", target["test_framework_clues"])

    def test_skips_windows_specific_instances_before_agent_execution(self):
        instance = {
            "instance_id": "cpputest__cpputest-1842",
            "repo": "cpputest/cpputest",
            "problem_statement": "old Visual C++ builds are broken in AppVeyor.",
            "patch": "diff --git a/CppUTest.vcxproj b/CppUTest.vcxproj\n",
            "test_patch": "diff --git a/tests/AllTests.vcproj b/tests/AllTests.vcproj\n",
            "language": "cpp",
        }

        with tempfile.TemporaryDirectory() as output_dir:
            adapter = MultiDockerEvalAdapter(output_dir=output_dir)
            result = adapter.process_single_instance(instance, max_steps=1)

        self.assertTrue(result["logs"]["skip_evaluation"])
        self.assertFalse(result["logs"]["platform_support"]["supported"])
        self.assertEqual(result["logs"]["platform_support"]["required_platform"], "windows")
        self.assertEqual(result["logs"]["test_command_source"], "unsupported_platform")
        self.assertIsNone(result["dockerfile"])
        self.assertIsNone(result["eval_script"])

    def test_uses_verified_test_command_list_when_building_eval_script(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            summary_path = Path(workplace) / "agent_run_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "verification_source": "agent_report",
                        "verified_test_commands": [
                            "pytest tests/unit",
                            "pytest tests/integration",
                        ],
                        "verification_bundle": {
                            "runtime_preparation_commands": [],
                            "test_commands": [
                                "pytest tests/unit",
                                "pytest tests/integration",
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            eval_script, _, _ = adapter._generate_test_script(
                workplace=workplace,
                language="python",
                problem_statement="",
                test_patch="",
                dockerfile_content="FROM python:3.11\nWORKDIR /testbed\n",
            )

        self.assertIn("pytest tests/unit", eval_script)
        self.assertIn("pytest tests/integration", eval_script)
        self.assertIn(") && \\", eval_script)
        self.assertEqual(adapter._last_test_command_source, "agent_report_verification_bundle")

    def test_uses_verified_runtime_preparation_commands_when_building_eval_script(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            summary_path = Path(workplace) / "agent_run_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "verification_source": "agent_report",
                        "verified_runtime_preparation_commands": [
                            "redis-server --daemonize yes",
                        ],
                        "verified_test_commands": [
                            "pytest tests",
                        ],
                        "verification_bundle": {
                            "runtime_preparation_commands": [
                                "redis-server --daemonize yes",
                            ],
                            "test_commands": [
                                "pytest tests",
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            eval_script, _, _ = adapter._generate_test_script(
                workplace=workplace,
                language="python",
                problem_statement="",
                test_patch="",
                dockerfile_content="FROM python:3.11\nWORKDIR /testbed\n",
            )

        self.assertIn("# Runtime preparation commands verified by the setup agent", eval_script)
        self.assertIn("redis-server --daemonize yes", eval_script)
        self.assertIn("pytest tests", eval_script)
        self.assertEqual(
            adapter._last_runtime_preparation_source,
            "agent_report_verification_bundle",
        )

    def test_uses_build_recipe_runtime_preparation_commands_for_eval(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        agent = SimpleNamespace(
            verified_runtime_preparation_commands=[],
            build_recipe={
                "runtime_preparation_commands": [
                    "ln -sf /app/build/src/timew /app/src/timew",
                ],
            },
        )

        runtime_commands = adapter._select_runtime_preparation_commands_for_eval(
            agent,
            accepted_verification=True,
        )
        eval_script, _, _ = adapter._generate_test_script(
            workplace=tempfile.mkdtemp(),
            language="cpp",
            problem_statement="",
            test_patch="",
            dockerfile_content="FROM buildpack-deps:jammy\nWORKDIR /testbed\n",
            structured_runtime_preparation_commands=runtime_commands,
            structured_test_command="cd /app/test && python2 ./resize.t",
        )

        self.assertIn("ln -sf /testbed/build/src/timew /testbed/src/timew", eval_script)
        self.assertIn("cd /testbed/test && python2 ./resize.t", eval_script)
        self.assertEqual(adapter._last_runtime_preparation_source, "agent_runtime_argument_list")

    def test_adds_recipe_rebuild_commands_before_tests_when_source_patch_changes_compiled_files(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        agent = SimpleNamespace(
            verified_runtime_preparation_commands=[],
            verified_test_commands=["python3 test/summary.t"],
            build_recipe={
                "build_commands": [
                    "apt-get update && apt-get install -y cmake ninja-build",
                    "git submodule update --init --recursive",
                    "cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release",
                    "cmake --build build --parallel",
                    "cp build/src/timew src/ && cp build/src/lex src/",
                    "ln -sf /usr/bin/python3 /usr/local/bin/python",
                ],
                "runtime_preparation_commands": [],
            },
        )
        source_patch = """diff --git a/src/Range.cpp b/src/Range.cpp
--- a/src/Range.cpp
+++ b/src/Range.cpp
@@ -1,2 +1,2 @@
-old
+new
"""

        runtime_commands = adapter._select_runtime_preparation_commands_for_eval(
            agent,
            accepted_verification=True,
            language="cpp",
            source_patch=source_patch,
        )
        eval_script, _, _ = adapter._generate_test_script(
            workplace=tempfile.mkdtemp(),
            language="cpp",
            problem_statement="",
            test_patch="",
            dockerfile_content="FROM gcc:11\nWORKDIR /testbed\n",
            structured_runtime_preparation_commands=runtime_commands,
            structured_test_command="python3 test/summary.t",
        )

        self.assertEqual(
            adapter._last_source_patch_rebuild_commands,
            [
                "cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release",
                "cmake --build build --parallel",
                "cp build/src/timew src/ && cp build/src/lex src/",
            ],
        )
        self.assertNotIn("apt-get update", eval_script)
        self.assertNotIn("git submodule update", eval_script)
        self.assertNotIn("ln -sf /usr/bin/python3", eval_script)
        self.assertIn("cp build/src/timew src/ && cp build/src/lex src/", eval_script)
        self.assertLess(
            eval_script.index("cp build/src/timew src/ && cp build/src/lex src/"),
            eval_script.index("python3 test/summary.t"),
        )

    def test_does_not_add_source_rebuild_commands_for_test_only_patch(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        agent = SimpleNamespace(
            verified_runtime_preparation_commands=[],
            verified_test_commands=["pytest tests"],
            build_recipe={
                "build_commands": ["cmake --build build --parallel"],
                "runtime_preparation_commands": [],
            },
        )
        source_patch = """diff --git a/tests/test_example.py b/tests/test_example.py
--- a/tests/test_example.py
+++ b/tests/test_example.py
@@ -1,2 +1,2 @@
-old
+new
"""

        runtime_commands = adapter._select_runtime_preparation_commands_for_eval(
            agent,
            accepted_verification=True,
            language="python",
            source_patch=source_patch,
        )

        self.assertIsNone(runtime_commands)
        self.assertEqual(adapter._last_source_patch_rebuild_commands, [])

    def test_requires_agent_report_bundle_for_eval_script_generation(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            summary_path = Path(workplace) / "agent_run_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "verification_source": "heuristic_fallback",
                        "verified_test_commands": [
                            "pytest tests",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            eval_script, _, _ = adapter._generate_test_script(
                workplace=workplace,
                language="python",
                problem_statement="",
                test_patch="",
                dockerfile_content="FROM python:3.11\nWORKDIR /testbed\n",
            )

        self.assertEqual(eval_script, "")
        self.assertEqual(adapter._last_test_command_source, "missing_agent_verification_bundle")

    def test_does_not_infer_runtime_service_setup_without_agent_bundle(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        eval_script, _, _ = adapter._build_eval_script(
            base_commands=["python3 -m pytest tests/"],
            language="python",
            test_patch="",
            dockerfile_content="""FROM python:3.6
WORKDIR /testbed
RUN apt-get install -y redis-server
""",
        )

        self.assertNotIn("redis-server --daemonize yes", eval_script)
        self.assertIn("python3 -m pytest tests/", eval_script)

    def test_does_not_auto_install_python_test_framework_after_test_patch(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            eval_script, _, updated_dockerfile = adapter._generate_test_script(
                workplace=workplace,
                language="python",
                problem_statement="",
                test_patch="diff --git a/tests/test_example.py b/tests/test_example.py\n+def test_example():\n+    assert True\n",
                dockerfile_content="FROM python:3.11\nWORKDIR /testbed\n",
                structured_test_command="python -m pytest tests",
            )

        self.assertIn("python -m pytest tests", eval_script)
        self.assertIn("COPY test.patch /tmp/test.patch", updated_dockerfile)
        self.assertNotIn("pip install pytest", updated_dockerfile)
        self.assertNotIn("pip install -r requirements.txt", updated_dockerfile)

    def test_uses_agent_post_test_patch_commands_only_when_supplied(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            _, setup_scripts, updated_dockerfile = adapter._generate_test_script(
                workplace=workplace,
                language="javascript",
                problem_statement="",
                test_patch="diff --git a/test/foo.test.js b/test/foo.test.js\n",
                dockerfile_content="FROM node:22\nWORKDIR /testbed\n",
                structured_test_command="npm test",
                structured_post_test_patch_commands=["npm install"],
            )

        self.assertIn("COPY post_test_patch_commands.sh /tmp/post_test_patch_commands.sh", updated_dockerfile)
        self.assertIn("/bin/bash /tmp/post_test_patch_commands.sh", updated_dockerfile)
        self.assertIn("npm install", setup_scripts["post_test_patch_commands.sh"])
        self.assertEqual(adapter._last_post_test_patch_source, "agent_runtime_build_recipe")

    def test_normalizes_root_app_path_in_env_assignments(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        adjusted = adapter._adjust_command_for_testbed(
            "cd /app && PYTHONPATH=/app:/app/src python -m pytest tests -q"
        )

        self.assertEqual(
            adjusted,
            "cd /testbed && PYTHONPATH=/testbed:/testbed/src python -m pytest tests -q",
        )

    def test_post_test_patch_multiline_commands_are_written_to_script(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        multiline_command = 'python -c "\nimport pathlib\npathlib.Path(\"/app/out.txt\").write_text(\"ok\")\n"'

        _, setup_scripts, updated_dockerfile = adapter._build_eval_script(
            base_commands=["pytest tests"],
            language="python",
            test_patch="diff --git a/tests/test_example.py b/tests/test_example.py\n",
            dockerfile_content="FROM python:3.11\nWORKDIR /testbed\n",
            post_test_patch_commands=[multiline_command],
        )

        self.assertIn("COPY post_test_patch_commands.sh /tmp/post_test_patch_commands.sh", updated_dockerfile)
        self.assertNotIn('RUN python -c "', updated_dockerfile)
        self.assertIn('python -c "', setup_scripts["post_test_patch_commands.sh"])
        self.assertIn('/testbed/out.txt', setup_scripts["post_test_patch_commands.sh"])

    def test_normalizes_source_for_docker_run_replay(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        normalized = adapter._normalize_run_instruction_for_docker(
            'RUN source "/usr/local/cargo/env" && rustc --version && cargo --version'
        )

        self.assertEqual(
            normalized,
            'RUN . "/usr/local/cargo/env" && rustc --version && cargo --version',
        )

    def test_normalizes_docker_run_replay_pip_requirement_specs(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        normalized = adapter._normalize_run_instruction_for_docker(
            "RUN pip install packaging>=24 setuptools>=65.6.3"
        )

        self.assertEqual(
            normalized,
            "RUN pip install 'packaging>=24' 'setuptools>=65.6.3'",
        )

    def test_multiline_run_instruction_is_wrapped_for_eval_dockerfile(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        instruction = 'RUN cd /testbed && python -c "\nprint(123)\n"'

        processed = adapter._prepare_agent_run_instruction_for_eval(instruction, 1)

        self.assertIn("base64 -d > /tmp/jayint_eval_run_1.sh", processed)
        self.assertNotIn("\nprint(123)\n", processed)
        encoded = re.search(r"printf '%s' '([^']+)'", processed).group(1)
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, 'cd /testbed && python -c "\nprint(123)\n"')

    def test_existing_heredoc_run_instruction_is_script_encoded(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        instruction = """RUN cat > /usr/local/bin/tool << 'EOF'
#!/bin/bash
args=("$@")
EOF"""

        processed = adapter._prepare_agent_run_instruction_for_eval(instruction, 1)

        self.assertIn("base64 -d > /tmp/jayint_eval_run_1.sh", processed)
        self.assertNotIn("RUN cat > /usr/local/bin/tool", processed)
        self.assertNotIn("\\\n#!/bin/bash", processed)
        encoded = re.search(r"printf '%s' '([^']+)'", processed).group(1)
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, "cat > /usr/local/bin/tool << 'EOF'\n#!/bin/bash\nargs=(\"$@\")\nEOF")

    def test_eval_dockerfile_injects_apt_bootstrap_before_git_install(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        dockerfile = adapter._build_eval_dockerfile(
            base_image_line="FROM ubuntu:24.04",
            repo_url="https://github.com/example/repo.git",
            base_commit="abc123",
            processed_instructions=[
                "RUN printf '%s\\n' 'Acquire::Retries \"5\";' > /etc/apt/apt.conf.d/99jayint-retries",
                "RUN apt-get update && apt-get install -y maven",
            ],
        )

        self.assertIn("# Configure apt reliability for eval image builds", dockerfile)
        self.assertEqual(dockerfile.count("99jayint-retries"), 1)
        self.assertLess(
            dockerfile.index("99jayint-retries"),
            dockerfile.index("RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git)"),
        )
        self.assertLess(
            dockerfile.index("RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git)"),
            dockerfile.index("RUN apt-get update && apt-get install -y maven"),
        )

    def test_eval_dockerfile_adds_old_pytest_plugin_cleanup(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        dockerfile = adapter._build_eval_dockerfile(
            base_image_line="FROM python:3.7",
            repo_url="https://github.com/example/repo.git",
            base_commit="abc123",
            processed_instructions=[
                'RUN pip install -e ".[testing]"',
                'RUN pip install "pytest>=3.6,<4.0" "pluggy>=0.12,<1.0"',
            ],
        )

        self.assertIn("RUN python -m pip uninstall -y pytest-xdist pytest-forked || true", dockerfile)

    def test_eval_dockerfile_adds_unzip_for_composer_install(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        dockerfile = adapter._build_eval_dockerfile(
            base_image_line="FROM php:8.1-cli",
            repo_url="https://github.com/example/repo.git",
            base_commit="abc123",
            processed_instructions=[
                "RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer",
                "RUN composer install --no-interaction",
            ],
        )

        self.assertIn("RUN apt-get update && apt-get install -y unzip", dockerfile)
        self.assertLess(
            dockerfile.index("RUN apt-get update && apt-get install -y unzip"),
            dockerfile.index("RUN composer install --no-interaction"),
        )

    def test_defers_rebuild_post_test_patch_commands_to_eval_runtime(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            eval_script, setup_scripts, updated_dockerfile = adapter._generate_test_script(
                workplace=workplace,
                language="cpp",
                problem_statement="",
                test_patch="diff --git a/tests/new_test.cpp b/tests/new_test.cpp\n",
                dockerfile_content="FROM gcc:13\nWORKDIR /testbed\n",
                structured_test_command="/testbed/build/CppUTestTests",
                structured_post_test_patch_commands=[
                    "cmake --build /app/build --parallel",
                ],
            )

        self.assertIn("cmake --build /testbed/build --parallel", eval_script)
        self.assertNotIn("post_test_patch_commands.sh", setup_scripts)
        self.assertNotIn("post_test_patch_commands.sh", updated_dockerfile)
        self.assertEqual(
            adapter._last_deferred_post_test_patch_commands,
            ["cmake --build /app/build --parallel"],
        )

    def test_filters_conflicting_runtime_env_export_from_eval(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        agent = SimpleNamespace(
            verified_runtime_preparation_commands=[],
            verified_test_commands=["python -m pytest tests"],
            build_recipe={
                "runtime_preparation_commands": [
                    "export DJANGO_SETTINGS_MODULE=pytest_django_test.settings_sqlite_file",
                    "redis-server --daemonize yes",
                ],
            },
        )
        test_patch = """
diff --git a/tests/test_django_settings_module.py b/tests/test_django_settings_module.py
--- a/tests/test_django_settings_module.py
+++ b/tests/test_django_settings_module.py
@@ -1,2 +1,3 @@
+monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
"""

        runtime_commands = adapter._select_runtime_preparation_commands_for_eval(
            agent,
            accepted_verification=True,
            test_patch=test_patch,
        )

        self.assertEqual(runtime_commands, ["redis-server --daemonize yes"])
        self.assertEqual(
            adapter._last_filtered_runtime_preparation_commands,
            ["export DJANGO_SETTINGS_MODULE=pytest_django_test.settings_sqlite_file"],
        )

    def test_extracts_added_pytest_targets_from_test_patch(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_django_settings_module.py b/tests/test_django_settings_module.py
--- a/tests/test_django_settings_module.py
+++ b/tests/test_django_settings_module.py
@@ -10,6 +10,10 @@ class TestSettings:
     def test_existing(self):
         pass
+    def test_debug_no_force(self, testdir, monkeypatch):
+        monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
"""

        targets = adapter._extract_changed_test_targets(test_patch)

        self.assertEqual(
            targets,
            ["tests/test_django_settings_module.py::TestSettings::test_debug_no_force"],
        )

    def test_extracts_class_pytest_target_from_source_context(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_database.py b/tests/test_database.py
--- a/tests/test_database.py
+++ b/tests/test_database.py
@@ -4,3 +4,6 @@ def test_reset_sequences_disabled(self, request) -> None:
+    def test_transaction_reset_sequences_enabled(self, request) -> None:
+        pass
"""

        with tempfile.TemporaryDirectory() as source_root:
            test_file = Path(source_root) / "tests" / "test_database.py"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                "\n".join(
                    [
                        "class TestDatabaseMarker:",
                        "    def test_reset_sequences_disabled(self, request) -> None:",
                        "        pass",
                    ]
                ),
                encoding="utf-8",
            )
            targets = adapter._extract_changed_test_targets(
                test_patch,
                source_root=source_root,
            )

        self.assertEqual(
            targets,
            ["tests/test_database.py::TestDatabaseMarker::test_transaction_reset_sequences_enabled"],
        )

    def test_extracts_existing_pytest_target_from_changed_decorator(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_database.py b/tests/test_database.py
--- a/tests/test_database.py
+++ b/tests/test_database.py
@@ -2,7 +2,11 @@ def test_reset_sequences_disabled(self, request) -> None:
-    @pytest.mark.django_db(transaction=True, reset_sequences=True)
+    @pytest.mark.django_db(reset_sequences=True)
     def test_reset_sequences_enabled(self, request) -> None:
         pass
+    @pytest.mark.django_db(transaction=True, reset_sequences=True)
+    def test_transaction_reset_sequences_enabled(self, request) -> None:
+        pass
"""

        with tempfile.TemporaryDirectory() as source_root:
            test_file = Path(source_root) / "tests" / "test_database.py"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                "\n".join(
                    [
                        "class TestDatabaseMarker:",
                        "    def test_reset_sequences_disabled(self, request) -> None:",
                        "        pass",
                        "    @pytest.mark.django_db(transaction=True, reset_sequences=True)",
                        "    def test_reset_sequences_enabled(self, request) -> None:",
                        "        pass",
                    ]
                ),
                encoding="utf-8",
            )
            targets = adapter._extract_changed_test_targets(
                test_patch,
                source_root=source_root,
            )

        self.assertEqual(
            targets,
            [
                "tests/test_database.py::TestDatabaseMarker::test_reset_sequences_enabled",
                "tests/test_database.py::TestDatabaseMarker::test_transaction_reset_sequences_enabled",
            ],
        )

    def test_ignores_nested_pytest_defs_inside_added_helper_body(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_manage_py_scan.py b/tests/test_manage_py_scan.py
--- a/tests/test_manage_py_scan.py
+++ b/tests/test_manage_py_scan.py
@@ -83,3 +83,8 @@ def test_existing():
+def test_runs_without_error_on_long_args(django_testdir):
+    django_testdir.makepyfile(\"\"\"
+    def test_nested_generated_test():
+        pass
+    \"\"\")
"""

        targets = adapter._extract_changed_test_targets(test_patch)

        self.assertEqual(
            targets,
            ["tests/test_manage_py_scan.py::test_runs_without_error_on_long_args"],
        )

    def test_ignores_nested_pytest_defs_inside_added_string_with_source_context(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_database.py b/tests/test_database.py
--- a/tests/test_database.py
+++ b/tests/test_database.py
@@ -2,3 +2,14 @@ def test_existing(self):
+    def test_serialized_rollback(self, db, django_testdir):
+        django_testdir.create_test_module(
+            \"\"\"
+            import pytest
+            def test_serialized_rollback_1():
+                pass
+            def test_serialized_rollback_2():
+                pass
+            \"\"\"
+        )
+        assert True
"""

        with tempfile.TemporaryDirectory() as source_root:
            test_file = Path(source_root) / "tests" / "test_database.py"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                "\n".join(
                    [
                        "class TestDatabaseFixtures:",
                        "    def test_existing(self):",
                        "        pass",
                    ]
                ),
                encoding="utf-8",
            )
            targets = adapter._extract_changed_test_targets(
                test_patch,
                source_root=source_root,
            )

        self.assertEqual(
            targets,
            ["tests/test_database.py::TestDatabaseFixtures::test_serialized_rollback"],
        )

    def test_preserves_inline_env_assignment_in_test_command(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_django_settings_module.py b/tests/test_django_settings_module.py
--- a/tests/test_django_settings_module.py
+++ b/tests/test_django_settings_module.py
@@ -1,2 +1,3 @@
+monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
"""

        commands = adapter._filter_test_commands_for_eval(
            [
                (
                    "DJANGO_SETTINGS_MODULE=pytest_django_test.settings_sqlite_file "
                    "python -m pytest tests/test_django_settings_module.py -v"
                )
            ],
            test_patch,
            reset_log=True,
        )

        self.assertEqual(
            commands,
            [
                (
                    "DJANGO_SETTINGS_MODULE=pytest_django_test.settings_sqlite_file "
                    "python -m pytest tests/test_django_settings_module.py -v"
                )
            ],
        )
        self.assertEqual(adapter._last_filtered_test_commands, [])

    def test_refines_pytest_file_command_to_added_patch_target(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_django_settings_module.py b/tests/test_django_settings_module.py
--- a/tests/test_django_settings_module.py
+++ b/tests/test_django_settings_module.py
@@ -20,6 +20,9 @@
+def test_debug_no_force(testdir, monkeypatch):
+    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
"""

        commands = adapter._refine_test_commands_to_changed_targets(
            [
                (
                    "python -m pytest "
                    "tests/test_django_settings_module.py::test_django_settings_configure -v"
                )
            ],
            test_patch,
            reset_log=True,
        )

        self.assertEqual(
            commands,
            ["python -m pytest tests/test_django_settings_module.py::test_debug_no_force -v"],
        )
        self.assertEqual(
            adapter._last_refined_test_commands,
            [
                {
                    "original": (
                        "python -m pytest "
                        "tests/test_django_settings_module.py::test_django_settings_configure -v"
                    ),
                    "refined": (
                        "python -m pytest "
                        "tests/test_django_settings_module.py::test_debug_no_force -v"
                    ),
                }
            ],
        )

    def test_refines_pytest_command_by_removing_same_file_sibling_nodes(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_django_settings_module.py b/tests/test_django_settings_module.py
--- a/tests/test_django_settings_module.py
+++ b/tests/test_django_settings_module.py
@@ -20,6 +20,9 @@
+def test_debug_no_force(testdir, monkeypatch):
+    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
"""

        commands = adapter._refine_test_commands_to_changed_targets(
            [
                (
                    "python -m pytest "
                    "tests/test_django_settings_module.py::test_debug_no_force "
                    "tests/test_django_settings_module.py::test_ds_env "
                    "tests/test_django_settings_module.py::test_ds_ini -v"
                )
            ],
            test_patch,
            reset_log=True,
        )

        self.assertEqual(
            commands,
            ["python -m pytest tests/test_django_settings_module.py::test_debug_no_force -v"],
        )

    def test_normalizes_cmake_wrapper_script_name_to_added_target(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/test/CMakeLists.txt b/test/CMakeLists.txt
--- a/test/CMakeLists.txt
+++ b/test/CMakeLists.txt
@@ -1,3 +1,3 @@
-set (test_SRCS data.t)
+set (test_SRCS atomic data.t)
"""

        commands = adapter._normalize_cmake_build_targets_from_test_patch(
            ["cmake --build build --target atomic.t data.t"],
            test_patch,
        )

        self.assertEqual(commands, ["cmake --build build --target atomic data.t"])

    def test_drops_broad_command_when_narrow_command_covers_changed_file(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/test/basic.test.js b/test/basic.test.js
--- a/test/basic.test.js
+++ b/test/basic.test.js
@@ -1,2 +1,3 @@
+test('new behavior', () => {})
"""

        commands = adapter._select_target_covering_test_commands(
            ["npm test -- test/basic.test.js", "npm test"],
            test_patch,
            reset_log=True,
        )

        self.assertEqual(commands, ["npm test -- test/basic.test.js"])
        self.assertEqual(adapter._last_dropped_broad_test_commands, ["npm test"])

    def test_eval_script_preserves_env_and_targets_added_pytest_node(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_django_settings_module.py b/tests/test_django_settings_module.py
--- a/tests/test_django_settings_module.py
+++ b/tests/test_django_settings_module.py
@@ -20,6 +20,9 @@
+def test_debug_no_force(testdir, monkeypatch):
+    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
"""

        with tempfile.TemporaryDirectory() as workplace:
            eval_script, _, _ = adapter._generate_test_script(
                workplace=workplace,
                language="python",
                problem_statement="",
                test_patch=test_patch,
                dockerfile_content="FROM python:3.11\nWORKDIR /testbed\n",
                structured_test_command=(
                    "DJANGO_SETTINGS_MODULE=pytest_django_test.settings_sqlite_file "
                    "python -m pytest "
                    "tests/test_django_settings_module.py::test_django_settings_configure -v"
                ),
            )

        self.assertIn(
            "python -m pytest tests/test_django_settings_module.py::test_debug_no_force -v",
            eval_script,
        )
        self.assertIn("DJANGO_SETTINGS_MODULE=", eval_script)
        self.assertEqual(adapter._last_filtered_test_commands, [])
        self.assertTrue(adapter._last_refined_test_commands)

    def test_recipe_repair_writes_llm_log_and_parses_recipe(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        response = json.dumps(
            {
                "build_commands": ["pip install -e ."],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["pytest tests/test_target.py::test_new"],
                "excluded_commands": [],
                "rationale": "Target command was missing from the fresh artifact.",
                "confidence": "high",
            }
        )

        with tempfile.TemporaryDirectory() as workplace:
            recipe, record = adapter._repair_build_recipe(
                client=FakeRepairClient(response),
                model="fake-model",
                instance={"instance_id": "example__1", "problem_statement": "fix it"},
                workplace=workplace,
                current_result={
                    "instance_id": "example__1",
                    "repo_url": "https://github.com/example/repo.git",
                    "language": "python",
                    "eval_script": "pytest tests",
                    "logs": {"benchmark_evaluation_target": {}},
                },
                current_recipe={"test_commands": ["pytest tests"]},
                preflight={
                    "returncode": 0,
                    "resolved": False,
                    "log_excerpt": "ERROR: missing dependency",
                },
                repair_round=0,
            )
            log_path = Path(record["log_path"])
            log_text = log_path.read_text(encoding="utf-8")

        self.assertEqual(recipe["build_commands"], ["pip install -e ."])
        self.assertEqual(recipe["test_commands"], ["pytest tests/test_target.py::test_new"])
        self.assertEqual(record["usage"]["total_tokens"], 18)
        self.assertIn("LLM INPUT", log_text)
        self.assertIn("LLM OUTPUT", log_text)
        self.assertIn("PARSED RESULT", log_text)

    def test_recipe_repair_input_includes_project_config_context(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            Path(workplace, "tox.ini").write_text(
                "\n".join(
                    [
                        "[testenv:py34-django18]",
                        "deps =",
                        "    pytest==2.6.4",
                        "    pytest-xdist==1.11",
                        "    Django>=1.8,<1.9",
                    ]
                ),
                encoding="utf-8",
            )
            Path(workplace, "setup.cfg").write_text(
                "[pytest]\nDJANGO_SETTINGS_MODULE = pytest_django_test.settings_sqlite_file\n",
                encoding="utf-8",
            )

            repair_input = adapter._build_recipe_repair_input(
                instance={"problem_statement": "broken test runner"},
                workplace=workplace,
                current_result={
                    "instance_id": "example__1",
                    "repo_url": "https://github.com/example/repo.git",
                    "language": "python",
                    "eval_script": "pytest tests",
                    "logs": {"benchmark_evaluation_target": {}},
                },
                current_recipe={"test_commands": ["pytest tests"]},
                preflight={"log_excerpt": "Settings already configured"},
            )

        self.assertIn("tox.ini", repair_input["project_config_context"])
        self.assertIn("setup.cfg", repair_input["project_config_context"])
        self.assertIn("pytest==2.6.4", repair_input["project_config_context"]["tox.ini"])
        self.assertIn(
            "DJANGO_SETTINGS_MODULE",
            repair_input["project_config_context"]["setup.cfg"],
        )

    def test_render_result_from_repaired_recipe_does_not_fallback_to_summary_bundle(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        recipe = {
            "build_commands": ["pip install -e ."],
            "post_test_patch_commands": [],
            "runtime_preparation_commands": [],
            "test_commands": ["pytest tests/test_target.py::test_new"],
            "excluded_commands": [],
            "rationale": "Use benchmark target.",
            "confidence": "high",
        }
        result = {
            "instance_id": "example__1",
            "repo_url": "https://github.com/example/repo.git",
            "language": "python",
            "dockerfile": "",
            "eval_script": "",
            "build_success": False,
            "test_success": False,
            "logs": {
                "benchmark_evaluation_target": {},
                "artifact_preflight": [],
                "artifact_repair_rounds": [],
            },
        }
        test_patch = """
diff --git a/tests/test_target.py b/tests/test_target.py
--- a/tests/test_target.py
+++ b/tests/test_target.py
@@ -1,2 +1,3 @@
+def test_new():
+    assert True
"""

        with tempfile.TemporaryDirectory() as workplace:
            summary_path = Path(workplace) / "agent_run_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "verification_source": "agent_report",
                        "verification_bundle": {
                            "runtime_preparation_commands": ["redis-server --daemonize yes"],
                            "test_commands": ["pytest old_tests"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            rendered = adapter._render_result_from_build_recipe(
                result=result,
                recipe=recipe,
                instance={"problem_statement": ""},
                workplace=workplace,
                base_image_line="FROM python:3.11",
                repo_url="https://github.com/example/repo.git",
                base_commit="abc123",
                language="python",
                source_patch="",
                test_patch=test_patch,
            )

        self.assertIn("RUN pip install -e .", rendered["dockerfile"])
        self.assertIn("pytest tests/test_target.py::test_new", rendered["eval_script"])
        self.assertNotIn("pytest old_tests", rendered["eval_script"])
        self.assertNotIn("redis-server --daemonize yes", rendered["eval_script"])
        self.assertEqual(rendered["logs"]["build_recipe_source"], "artifact_repair_llm")

    def test_render_result_adds_local_install_for_pytest_plugin_recipe(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        recipe = {
            "build_commands": ["pip install 'pytest>=3,<4' 'django>=1.11,<2'"],
            "post_test_patch_commands": [],
            "runtime_preparation_commands": [],
            "test_commands": ["python -m pytest tests/test_plugin.py::test_changed"],
            "excluded_commands": [],
            "rationale": "Use local plugin code.",
            "confidence": "high",
        }
        result = {
            "instance_id": "example__plugin",
            "repo_url": "https://github.com/example/plugin.git",
            "language": "python",
            "dockerfile": "",
            "eval_script": "",
            "build_success": False,
            "test_success": False,
            "logs": {
                "benchmark_evaluation_target": {},
                "artifact_preflight": [],
                "artifact_repair_rounds": [],
            },
        }

        with tempfile.TemporaryDirectory() as workplace:
            Path(workplace, "setup.py").write_text(
                "setup(name='pytest-example', entry_points={'pytest11': ['example = example.plugin']})",
                encoding="utf-8",
            )
            rendered = adapter._render_result_from_build_recipe(
                result=result,
                recipe=recipe,
                instance={"problem_statement": ""},
                workplace=workplace,
                base_image_line="FROM python:3.11",
                repo_url="https://github.com/example/plugin.git",
                base_commit="abc123",
                language="python",
                source_patch="",
                test_patch="",
            )

        self.assertIn("RUN cd /testbed && pip install -e .", rendered["dockerfile"])
        self.assertIn(
            "cd /testbed && pip install -e .",
            rendered["logs"]["build_recipe"]["build_commands"],
        )

    def test_render_result_pins_pip_deps_from_project_exact_config(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        recipe = {
            "build_commands": [
                "pip install 'django>=1.8,<1.9'",
                "pip install 'pytest>=2.5,<4.0' pytest-xdist==1.26 django-configurations south",
            ],
            "post_test_patch_commands": [],
            "runtime_preparation_commands": [],
            "test_commands": ["pytest tests/test_target.py::test_new"],
            "excluded_commands": [],
            "rationale": "Use project-compatible deps.",
            "confidence": "high",
        }
        result = {
            "instance_id": "example__pins",
            "repo_url": "https://github.com/example/repo.git",
            "language": "python",
            "dockerfile": "",
            "eval_script": "",
            "build_success": False,
            "test_success": False,
            "logs": {
                "benchmark_evaluation_target": {},
                "artifact_preflight": [],
                "artifact_repair_rounds": [],
            },
        }

        with tempfile.TemporaryDirectory() as workplace:
            Path(workplace, "tox.ini").write_text(
                """
[testenv:py36]
deps =
    pytest==2.6.4
    pytest-xdist==1.11
    Django>=1.8,<1.9
    django-configurations==0.8
    south==1.0.2
commands = pytest tests
""",
                encoding="utf-8",
            )
            rendered = adapter._render_result_from_build_recipe(
                result=result,
                recipe=recipe,
                instance={"problem_statement": ""},
                workplace=workplace,
                base_image_line="FROM python:3.11",
                repo_url="https://github.com/example/repo.git",
                base_commit="abc123",
                language="python",
                source_patch="",
                test_patch="",
            )

        self.assertIn("RUN pip install 'django>=1.8,<1.9'", rendered["dockerfile"])
        self.assertIn(
            "RUN pip install 'pytest>=2.5,<4.0' pytest-xdist==1.26 django-configurations==0.8 south==1.0.2",
            rendered["dockerfile"],
        )
        self.assertNotIn(" django-configurations south", rendered["dockerfile"])
        self.assertEqual(len(rendered["logs"]["project_dependency_pin_rewrites"]), 1)

    def test_nested_pytester_django_target_uses_null_pytest_config(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        test_patch = """
diff --git a/tests/test_django_settings_module.py b/tests/test_django_settings_module.py
--- a/tests/test_django_settings_module.py
+++ b/tests/test_django_settings_module.py
@@ -1,2 +1,14 @@
+def test_debug_no_force(testdir, monkeypatch):
+    monkeypatch.delenv('DJANGO_SETTINGS_MODULE')
+    testdir.makeconftest(\"\"\"
+        from django.conf import settings
+        def pytest_configure():
+            settings.configure(SECRET_KEY='x')
+    \"\"\")
+    r = testdir.runpytest('--no-force-no-debug')
+    assert r.ret == 0
"""

        eval_script, _, _ = adapter._generate_test_script(
            workplace=tempfile.mkdtemp(),
            language="python",
            problem_statement="",
            test_patch=test_patch,
            dockerfile_content="FROM python:3.11",
            structured_test_commands=[
                "pytest tests/test_django_settings_module.py::test_debug_no_force -v"
            ],
            allow_summary_fallback=False,
        )

        self.assertIn(
            "pytest -c /dev/null tests/test_django_settings_module.py::test_debug_no_force -v",
            eval_script,
        )
        self.assertIn(
            "nested_pytester_uses_own_django_settings",
            json.dumps(adapter._last_refined_test_commands),
        )

    def test_adds_compiled_test_artifact_publication_for_source_tree_runner(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        build_recipe = {
            "build_commands": [
                "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release",
                "cmake --build build --parallel",
            ],
        }
        source_patch = """diff --git a/src/Range.cpp b/src/Range.cpp
--- a/src/Range.cpp
+++ b/src/Range.cpp
@@ -1,2 +1,2 @@
-old
+new
"""
        test_patch = """diff --git a/test/atomic.cpp b/test/atomic.cpp
--- /dev/null
+++ b/test/atomic.cpp
@@ -0,0 +1,2 @@
+int main() { return 0; }
diff --git a/test/CMakeLists.txt b/test/CMakeLists.txt
--- a/test/CMakeLists.txt
+++ b/test/CMakeLists.txt
@@ -1,2 +1,3 @@
+add_executable(atomic atomic.cpp)
"""

        commands = adapter._select_source_patch_rebuild_commands(
            build_recipe,
            source_patch=source_patch,
            test_patch=test_patch,
            test_commands=["cd test && python3 run_all"],
        )

        self.assertIn("cmake --build build --parallel", commands)
        self.assertTrue(
            any("build/test build/tests" in command and "ln -sf" in command for command in commands)
        )

    def test_adds_expected_executable_publication_for_test_binary_paths(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        build_recipe = {
            "build_commands": [
                "cmake -S . -B build",
                "cmake --build build --parallel",
            ],
        }
        source_patch = """diff --git a/src/TestRegistry.cpp b/src/TestRegistry.cpp
--- a/src/TestRegistry.cpp
+++ b/src/TestRegistry.cpp
@@ -1,2 +1,2 @@
-old
+new
"""

        commands = adapter._select_source_patch_rebuild_commands(
            build_recipe,
            source_patch=source_patch,
            test_commands=["/app/build/CppUTestTests -v"],
        )

        self.assertTrue(
            any("CppUTestTests" in command and "find build -type f" in command for command in commands)
        )

    def test_does_not_treat_cd_testbed_as_expected_executable(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        names = adapter._extract_expected_executable_names(
            ["cd /testbed && python -m pytest tests/test_example.py -v"]
        )

        self.assertEqual(names, [])

    def test_extracts_multiline_shell_heredoc_run_instruction(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        original_dockerfile = """FROM python:3.12
WORKDIR /app
RUN mkdir -p ~/.local/bin && cat > ~/.local/bin/appimagetool << 'EOF'
#!/bin/bash
echo "mock"
EOF
chmod +x ~/.local/bin/appimagetool
RUN pytest tests
"""

        base_image, instructions = adapter._extract_agent_dockerfile_instructions(original_dockerfile)

        self.assertEqual(base_image, "FROM python:3.12")
        self.assertEqual(len(instructions), 2)
        self.assertIn("#!/bin/bash", instructions[0])
        self.assertIn("chmod +x ~/.local/bin/appimagetool", instructions[0])
        self.assertEqual(instructions[1], "RUN pytest tests")

    def test_extracts_multiple_heredocs_from_one_run_instruction(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        original_dockerfile = """FROM python:3.6
WORKDIR /app
RUN cat > one.py << 'EOF'
print("one")
EOF
cat > two.py << 'PYEOF'
print("two")
PYEOF
RUN python one.py
"""

        _, instructions = adapter._extract_agent_dockerfile_instructions(original_dockerfile)

        self.assertEqual(len(instructions), 2)
        self.assertIn("cat > one.py", instructions[0])
        self.assertIn("cat > two.py", instructions[0])
        self.assertIn("PYEOF", instructions[0])


if __name__ == "__main__":
    unittest.main()
