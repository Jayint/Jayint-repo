import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.execution_plan import compile_execution_plan
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.schema import EdgeType, Layer, NodeType
from src.ecosystems.build import build_polyglot_dep_graph
from src.language_handlers import detect_languages


class _MissingRuntimeExecutor:
    def run(self, command, *, timeout=300):
        return CommandResult(command, 1, "", "not installed")


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_non_python_packages_compile_as_one_dependency_set_not_pip(tmp_path):
    _write(
        tmp_path,
        "package.json",
        '{"scripts":{"test":"vitest","build":"vite build"},'
        '"dependencies":{"react":"^18.0.0"}}',
    )
    _write(
        tmp_path,
        "package-lock.json",
        '{"lockfileVersion":3,"packages":{"":{"name":"demo"},'
        '"node_modules/react":{"version":"18.3.1"}}}',
    )
    _write(tmp_path, "src/app.js", "const React = require('react');\n")
    languages = detect_languages(str(tmp_path))

    graph = build_polyglot_dep_graph(
        str(tmp_path),
        _MissingRuntimeExecutor(),
        language_requirements=languages,
        base_image="python:3.11-slim",
    )
    plan = compile_execution_plan(graph)
    script = render_build_script(graph)

    waves = [block.wave for block in plan]
    assert waves.index(Layer.RUNTIME.value) < waves.index(Layer.DEPENDENCIES.value)
    assert waves.index(Layer.DEPENDENCIES.value) < waves.index(Layer.BUILD.value)
    assert sum(
        node.type is NodeType.DEPENDENCY_SET for node in graph.nodes
    ) == 1
    assert "npm ci" in script
    assert "npm run build" in script
    assert "pip install" not in script
    assert "react==18.3.1" not in script
    test_node = next(node for node in graph.nodes if node.type is NodeType.TEST)
    assert test_node.data["test_commands"] == ("npm test",)


def test_build_tool_project_precedes_primary_runtime_project(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
[project]
name = "native-demo"
version = "0.1.0"
requires-python = ">=3.11"
[build-system]
requires = ["maturin"]
build-backend = "maturin"
""",
    )
    _write(tmp_path, "src/native_demo/__init__.py", "")
    _write(
        tmp_path,
        "native/Cargo.toml",
        """
[package]
name = "native-core"
version = "0.1.0"
rust-version = "1.82"
[dependencies]
pyo3 = "0.22"
""",
    )
    _write(tmp_path, "native/src/lib.rs", "use pyo3::prelude::*;\n")
    languages = detect_languages(str(tmp_path))

    graph = build_polyglot_dep_graph(
        str(tmp_path),
        _MissingRuntimeExecutor(),
        language_requirements=languages,
        base_image="python:3.11-slim",
    )

    python_project = next(
        node for node in graph.nodes
        if node.type is NodeType.PROJECT and node.language == "python"
    )
    rust_project = next(
        node for node in graph.nodes
        if node.type is NodeType.PROJECT and node.language == "rust"
    )
    cross_edge = next(
        edge for edge in graph.edges
        if edge.src == python_project.id and edge.dst == rust_project.id
    )
    assert cross_edge.relation is EdgeType.REQUIRES
    assert cross_edge.data["cross_language"] is True

    ordered_nodes = [
        block.target_node_ids[0] for block in compile_execution_plan(graph)
        if block.target_node_ids
    ]
    assert ordered_nodes.index(rust_project.id) < ordered_nodes.index(
        python_project.id
    )


def test_java_backend_and_typescript_frontend_share_one_ordered_plan(tmp_path):
    _write(
        tmp_path,
        "server/pom.xml",
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example</groupId><artifactId>server</artifactId><version>1.0</version>
  <properties><maven.compiler.release>17</maven.compiler.release></properties>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.11.0</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
""",
    )
    _write(
        tmp_path,
        "server/src/main/java/example/App.java",
        "package example;\npublic final class App {}\n",
    )
    _write(
        tmp_path,
        "frontend/package.json",
        '{"scripts":{"build":"tsc","test":"vitest"},'
        '"dependencies":{"typescript":"^5.5.0"}}',
    )
    _write(
        tmp_path,
        "frontend/package-lock.json",
        '{"lockfileVersion":3,"packages":{"":{"name":"frontend"},'
        '"node_modules/typescript":{"version":"5.5.4"}}}',
    )
    _write(tmp_path, "frontend/tsconfig.json", '{"compilerOptions":{}}')
    _write(tmp_path, "frontend/src/index.ts", "export const value = 1\n")
    languages = detect_languages(str(tmp_path))

    graph = build_polyglot_dep_graph(
        str(tmp_path),
        _MissingRuntimeExecutor(),
        language_requirements=languages,
        base_image="ubuntu:24.04",
    )
    plan = compile_execution_plan(graph)
    script = render_build_script(graph)

    ecosystems = {
        node.ecosystem.value for node in graph.nodes
        if node.type is NodeType.DEPENDENCY_SET
    }
    assert ecosystems == {"maven", "npm"}
    waves = [block.wave for block in plan]
    assert max(i for i, wave in enumerate(waves) if wave == Layer.RUNTIME.value) < min(
        i for i, wave in enumerate(waves) if wave == Layer.DEPENDENCIES.value
    )
    assert max(i for i, wave in enumerate(waves) if wave == Layer.DEPENDENCIES.value) < min(
        i for i, wave in enumerate(waves) if wave == Layer.BUILD.value
    )
    assert "cd server && mvn -B -DskipTests dependency:go-offline" in script
    assert "cd frontend && npm ci" in script
    test_node = next(node for node in graph.nodes if node.type is NodeType.TEST)
    assert set(test_node.data["test_commands"]) == {
        "cd server && mvn -B test",
        "cd frontend && npm test",
    }
    syntax = subprocess.run(
        ["bash", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    for block in plan:
        for command in block.commands:
            assert command in script
