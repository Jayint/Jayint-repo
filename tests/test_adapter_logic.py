import json
import tempfile
import unittest
from pathlib import Path

from multi_docker_eval_adapter import MultiDockerEvalAdapter


class AdapterLogicTests(unittest.TestCase):
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

    def test_infers_cpp_rebuild_commands_after_test_patch(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        dockerfile = """FROM buildpack-deps:jammy
WORKDIR /testbed
RUN apt-get update && apt-get install -y build-essential cmake
RUN cd build && cmake .. && make -j$(nproc)
RUN cd build && ctest --output-on-failure
"""

        rebuild_commands = adapter._infer_post_patch_rebuild_commands(
            language="cpp",
            dockerfile_content=dockerfile,
            base_command="cd build && ctest --output-on-failure",
            test_patch="diff --git a/tests/foo_test.cpp b/tests/foo_test.cpp\n",
        )

        self.assertEqual(rebuild_commands, ["cd build && cmake .. && make -j$(nproc)"])

    def test_infers_java_rebuild_commands_without_running_tests_during_build(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        dockerfile = """FROM eclipse-temurin:17-jdk-noble
WORKDIR /testbed
RUN ./mvnw -q -pl scheduler -am package
"""

        rebuild_commands = adapter._infer_post_patch_rebuild_commands(
            language="java",
            dockerfile_content=dockerfile,
            base_command="java -jar /testbed/build/tests.jar",
            test_patch="diff --git a/src/test/java/FooTest.java b/src/test/java/FooTest.java\n",
        )

        self.assertEqual(
            rebuild_commands,
            ["./mvnw -q -pl scheduler -am package -DskipTests -DskipITs"],
        )

    def test_skips_java_rebuild_when_eval_command_already_recompiles(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        dockerfile = """FROM eclipse-temurin:17-jdk-noble
WORKDIR /testbed
RUN ./mvnw -q -pl scheduler -am package
"""

        rebuild_commands = adapter._infer_post_patch_rebuild_commands(
            language="java",
            dockerfile_content=dockerfile,
            base_command="./mvnw -q -pl scheduler -am test",
            test_patch="diff --git a/src/test/java/FooTest.java b/src/test/java/FooTest.java\n",
        )

        self.assertEqual(rebuild_commands, [])

    def test_infers_rust_rebuild_commands(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        dockerfile = """FROM rust:1.80
WORKDIR /testbed
RUN cargo build --workspace
"""

        rebuild_commands = adapter._infer_post_patch_rebuild_commands(
            language="rust",
            dockerfile_content=dockerfile,
            base_command="/testbed/target/debug/deps/my_crate_tests",
            test_patch="diff --git a/tests/foo.rs b/tests/foo.rs\n",
        )

        self.assertEqual(rebuild_commands, ["cargo build --workspace"])

    def test_infers_go_rebuild_commands(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        dockerfile = """FROM golang:1.23
WORKDIR /testbed
RUN go build ./...
"""

        rebuild_commands = adapter._infer_post_patch_rebuild_commands(
            language="go",
            dockerfile_content=dockerfile,
            base_command="./bin/integration-tests",
            test_patch="diff --git a/foo_test.go b/foo_test.go\n",
        )

        self.assertEqual(rebuild_commands, ["go build ./..."])

    def test_uses_verified_test_command_list_when_building_eval_script(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            summary_path = Path(workplace) / "agent_run_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "verified_test_commands": [
                            "pytest tests/unit",
                            "pytest tests/integration",
                        ]
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
        self.assertEqual(adapter._last_test_command_source, "runtime_verified_test_commands")

    def test_uses_verified_runtime_preparation_commands_when_building_eval_script(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        with tempfile.TemporaryDirectory() as workplace:
            summary_path = Path(workplace) / "agent_run_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "verified_runtime_preparation_commands": [
                            "redis-server --daemonize yes",
                        ],
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

        self.assertIn("# Runtime preparation commands verified by the setup agent", eval_script)
        self.assertIn("redis-server --daemonize yes", eval_script)
        self.assertIn("pytest tests", eval_script)
        self.assertEqual(
            adapter._last_runtime_preparation_source,
            "runtime_verified_runtime_preparation_commands",
        )

    def test_adds_runtime_redis_setup_when_eval_commands_need_redis(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        eval_script, _, _ = adapter._build_eval_script(
            base_commands=["redis-cli ping && python3 -m pytest tests/"],
            language="python",
            test_patch="",
            dockerfile_content="""FROM python:3.6
WORKDIR /testbed
RUN apt-get install -y redis-server
""",
        )

        self.assertIn("redis-server --daemonize yes", eval_script)
        self.assertIn("redis-cli ping >/dev/null 2>&1 || exit 1", eval_script)
        self.assertIn("redis-cli ping && python3 -m pytest tests/", eval_script)

    def test_normalizes_source_for_docker_run_replay(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())

        normalized = adapter._normalize_run_instruction_for_docker(
            'RUN source "/usr/local/cargo/env" && rustc --version && cargo --version'
        )

        self.assertEqual(
            normalized,
            'RUN . "/usr/local/cargo/env" && rustc --version && cargo --version',
        )

    def test_moves_cpp_rebuild_from_docker_build_to_eval_script(self):
        adapter = MultiDockerEvalAdapter(output_dir=tempfile.mkdtemp())
        dockerfile = """FROM buildpack-deps:jammy
WORKDIR /testbed
RUN cd build && cmake .. && make -j$(nproc)
"""

        with tempfile.TemporaryDirectory() as workplace:
            eval_script, _, updated_dockerfile = adapter._generate_test_script(
                workplace=workplace,
                language="cpp",
                problem_statement="",
                test_patch="diff --git a/tests/foo_test.cpp b/tests/foo_test.cpp\n",
                dockerfile_content=dockerfile,
                structured_test_command="./build/tests/CppUTest/CppUTestTests",
            )

        self.assertIn("cd build && cmake .. && make -j$(nproc)", eval_script)
        self.assertEqual(updated_dockerfile.count("RUN cd build && cmake .. && make -j$(nproc)"), 1)


if __name__ == "__main__":
    unittest.main()
