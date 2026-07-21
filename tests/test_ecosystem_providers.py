import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.ecosystems.base import (
    ProviderTarget,
    Workspace,
    run_native_resolver_probe,
)
from src.ecosystems.go import GoProvider
from src.ecosystems.java import GradleProvider, MavenProvider
from src.ecosystems.node import NodeProvider
from src.ecosystems.registry import discover_polyglot_test_commands
from src.ecosystems.rust import RustProvider
from src.language_handlers import detect_languages
from python_deps.depgraph.schema import Ecosystem, EdgeType, NodeType


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class _ResolverSandbox:
    def __init__(self, result=(0, "resolved")):
        self.result = result
        self.created = []
        self.commands = []
        self.closed = []

    def create_resolver_container(self, resolver_id):
        handle = object()
        self.created.append((resolver_id, handle))
        return handle

    def resolver_exec(self, handle, command):
        self.commands.append((handle, command))
        return self.result

    def close_resolver_container(self, handle):
        self.closed.append(handle)


def test_native_resolver_probe_is_isolated_bounded_and_always_removed():
    sandbox = _ResolverSandbox(result=(1, "metadata failed"))
    workspace = Workspace(
        language="go",
        ecosystem=Ecosystem.GO_MODULE,
        root="backend",
        manifest_file="backend/go.mod",
        package_manager="go",
        version_constraint="1.22",
        role="primary_runtime",
    )

    evidence = run_native_resolver_probe(
        sandbox,
        ecosystem=Ecosystem.GO_MODULE,
        workspace=workspace,
        command="cd backend && go list -m -json all",
        timeout_seconds=17,
    )

    assert evidence.status == "failed"
    assert "resolver exited 1" in evidence.error
    assert len(sandbox.created) == 1
    assert sandbox.commands[0][1].startswith("timeout 17s bash -lc ")
    assert sandbox.closed == [sandbox.created[0][1]]


def test_node_provider_builds_workspace_transaction_and_import_mapping(tmp_path):
    _write(
        tmp_path,
        "frontend/package.json",
        '{"engines":{"node":">=20"},"scripts":{"test":"vitest","build":"vite build"},'
        '"dependencies":{"react":"^18.0.0"}}',
    )
    _write(
        tmp_path,
        "frontend/package-lock.json",
        '{"lockfileVersion":3,"packages":{"":{"name":"frontend"},'
        '"node_modules/react":{"version":"18.3.1"}}}',
    )
    _write(tmp_path, "frontend/src/app.tsx", "import React from 'react';\n")
    _write(tmp_path, "frontend/tsconfig.json", '{"compilerOptions":{}}')
    languages = detect_languages(str(tmp_path))
    provider = NodeProvider()
    workspace = provider.detect_workspaces(str(tmp_path), languages)[0]

    resolver_sandbox = _ResolverSandbox()
    fragment = provider.build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("python:3.11-slim"),
        sandbox=resolver_sandbox,
    )

    by_type = {}
    for node in fragment.graph.nodes:
        by_type.setdefault(node.type, []).append(node)
    assert len(by_type[NodeType.DEPENDENCY_SET]) == 1
    runtime = by_type[NodeType.RUNTIME][0]
    assert runtime.id == "runtime:node:20.0.0"
    assert runtime.language == "node"
    assert workspace.language == "typescript"
    assert workspace.runtime_language == "node"
    assert by_type[NodeType.DEPENDENCY_SET][0].setup_commands == (
        "cd frontend && npm ci",
    )
    assert any(node.name == "react" and node.version == "18.3.1"
               for node in by_type[NodeType.PACKAGE])
    assert any(edge.relation is EdgeType.MAPS_TO for edge in fragment.graph.edges)
    assert fragment.test_commands == ("cd frontend && npm test",)
    native = by_type[NodeType.DEPENDENCY_SET][0].data["native_resolver"]
    assert native["status"] == "resolved"
    assert len(resolver_sandbox.closed) == 1


def test_node_provider_uses_direct_dependency_engine_floor_for_typescript(tmp_path):
    _write(
        tmp_path,
        "package.json",
        '{"engines":{"node":">=18.18"},"scripts":{"test":"vitest"},'
        '"devDependencies":{"vite":"^7.0.0"}}',
    )
    _write(
        tmp_path,
        "package-lock.json",
        '{"lockfileVersion":3,"packages":{"":{"name":"addons-server"},'
        '"node_modules/vite":{"version":"7.0.0",'
        '"engines":{"node":"^20.19.0 || >=22.12.0"}}}}',
    )
    _write(tmp_path, "tsconfig.json", '{"compilerOptions":{}}')
    _write(tmp_path, "src/index.ts", "export const ok = true\n")

    provider = NodeProvider()
    workspace = provider.detect_workspaces(
        str(tmp_path), detect_languages(str(tmp_path))
    )[0]
    fragment = provider.build_fragment(
        str(tmp_path), workspace, ProviderTarget("python:3.11-slim"),
        sandbox=_ResolverSandbox(result=(1, "offline")),
    )
    runtime = next(node for node in fragment.graph.nodes if node.type is NodeType.RUNTIME)

    assert workspace.language == "typescript"
    assert workspace.runtime_language == "node"
    assert workspace.resolved_runtime_version == "20.19.0"
    assert runtime.id == "runtime:node:20.19.0"
    assert any("node-v20.19.0" in command for command in runtime.setup_commands)
    assert "a[0]>b[0]" in runtime.check_command


def test_node_major_base_image_reuses_new_enough_runtime(tmp_path):
    _write(
        tmp_path,
        "package.json",
        '{"engines":{"node":">=20.19.0"},"scripts":{"test":"vitest"}}',
    )
    _write(tmp_path, "package-lock.json", '{"lockfileVersion":3,"packages":{}}')
    _write(tmp_path, "src/index.js", "module.exports = true\n")
    provider = NodeProvider()
    workspace = provider.detect_workspaces(
        str(tmp_path), detect_languages(str(tmp_path))
    )[0]
    fragment = provider.build_fragment(
        str(tmp_path), workspace, ProviderTarget("node:20-bookworm"),
        sandbox=_ResolverSandbox(result=(1, "offline")),
    )
    runtime = next(node for node in fragment.graph.nodes if node.type is NodeType.RUNTIME)

    assert runtime.setup_commands == ()
    assert "process.versions.node" in runtime.check_command


def test_node_provider_accepts_typescript_declaration_build_output(
    tmp_path, monkeypatch
):
    _write(
        tmp_path,
        "package.json",
        '{"scripts":{"build":"dts-buddy"},"types":"./types/index.d.ts"}',
    )
    languages = detect_languages(str(tmp_path))
    provider = NodeProvider()
    workspace = provider.detect_workspaces(str(tmp_path), languages)[0]

    monkeypatch.chdir(tmp_path)
    commands, check = provider.project_commands(workspace)
    fragment = provider.build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("node:24"),
        sandbox=_ResolverSandbox(result=(1, "offline")),
    )
    project = next(
        node for node in fragment.graph.nodes if node.type is NodeType.PROJECT
    )

    assert commands == ("npm run build",)
    assert check is not None and "test -d types" in check
    assert project.check_command is not None
    assert "test -d types" in project.check_command


def test_node_ast_scanner_handles_reexports_aliases_and_dynamic_imports(tmp_path):
    _write(
        tmp_path,
        "package.json",
        '{"dependencies":{"react":"18","lodash":"4","side-effect":"1"}}',
    )
    _write(
        tmp_path,
        "package-lock.json",
        '{"lockfileVersion":3,"packages":{'
        '"node_modules/react":{"version":"18.3.1"},'
        '"node_modules/lodash":{"version":"4.17.21"},'
        '"node_modules/side-effect":{"version":"1.0.0"}}}',
    )
    _write(
        tmp_path,
        "tsconfig.json",
        '{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"]}}}',
    )
    _write(tmp_path, "src/local.ts", "export const local = 1\n")
    _write(
        tmp_path,
        "src/app.ts",
        """
import React from "react";
export { chunk } from "lodash/fp";
import "side-effect";
const local = import("@app/local");
const unknown = import(moduleName);
""",
    )
    languages = detect_languages(str(tmp_path))
    provider = NodeProvider()
    workspace = provider.detect_workspaces(str(tmp_path), languages)[0]

    refs = provider.scan_imports(
        str(tmp_path), workspace, ProviderTarget("node:20-bookworm")
    )
    names = {item.raw_specifier for item in refs}

    assert {"react", "lodash/fp", "side-effect"} <= names
    assert "@app/local" not in names
    dynamic = next(item for item in refs if item.raw_specifier.startswith("<dynamic-import:"))
    assert dynamic.confidence == "low"


def test_node_workspace_root_owns_nested_package_install_transaction(tmp_path):
    _write(
        tmp_path,
        "package.json",
        '{"packageManager":"pnpm@9.15.0","workspaces":["packages/*"]}',
    )
    _write(tmp_path, "packages/web/package.json", '{"dependencies":{"react":"18"}}')
    _write(tmp_path, "packages/web/tsconfig.json", '{"compilerOptions":{}}')
    languages = detect_languages(str(tmp_path))

    workspaces = NodeProvider().detect_workspaces(str(tmp_path), languages)

    assert len(workspaces) == 1
    assert workspaces[0].root == "."
    assert workspaces[0].package_manager == "pnpm"
    assert workspaces[0].package_manager_version == "9.15.0"


def test_node_resolver_parses_generated_lock_without_mutating_repo(tmp_path):
    _write(
        tmp_path,
        "package.json",
        '{"dependencies":{"react":"^18.0.0"}}',
    )
    generated = (
        '{"lockfileVersion":3,"packages":{'
        '"node_modules/react":{"version":"18.3.1"}}}'
    )
    sandbox = _ResolverSandbox(
        result=(0, "__JAYINT_NODE_LOCK__\n" + generated)
    )
    languages = detect_languages(str(tmp_path))
    workspace = NodeProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = NodeProvider().build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("node:20-bookworm"),
        sandbox=sandbox,
    )

    package = next(
        node for node in fragment.graph.nodes if node.type is NodeType.PACKAGE
    )
    deps = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.DEPENDENCY_SET
    )
    assert package.name == "react"
    assert package.version == "18.3.1"
    assert deps.data["resolution_source"] == "resolver_sandbox"
    assert not (tmp_path / "package-lock.json").exists()
    assert deps.setup_commands == ("npm install",)


def test_typescript_compiler_api_result_is_used_when_resolver_returns_it(tmp_path):
    _write(
        tmp_path,
        "package.json",
        '{"dependencies":{"react":"18","typescript":"5.5.4"}}',
    )
    _write(
        tmp_path,
        "package-lock.json",
        '{"lockfileVersion":3,"packages":{'
        '"node_modules/react":{"version":"18.3.1"},'
        '"node_modules/typescript":{"version":"5.5.4"}}}',
    )
    _write(tmp_path, "tsconfig.json", '{"compilerOptions":{}}')
    _write(tmp_path, "src/app.ts", 'import React from "react"\n')
    output = (
        "__JAYINT_NODE_LOCK__\n"
        '{"lockfileVersion":3,"packages":{'
        '"node_modules/react":{"version":"18.3.1"},'
        '"node_modules/typescript":{"version":"5.5.4"}}}\n'
        "__JAYINT_TYPESCRIPT_IMPORTS__\n"
        '[{"file":"src/app.ts","specifier":"react","dynamic":false,"line":1}]'
    )
    sandbox = _ResolverSandbox(result=(0, output))
    languages = detect_languages(str(tmp_path))
    workspace = NodeProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = NodeProvider().build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("node:20-bookworm"),
        sandbox=sandbox,
    )

    import_node = next(
        node for node in fragment.graph.nodes if node.type is NodeType.IMPORT
    )
    package = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.PACKAGE and node.name == "react"
    )
    assert import_node.provenance == "src/app.ts"
    assert any(
        edge.src == import_node.id and edge.dst == package.id
        and edge.relation is EdgeType.MAPS_TO
        for edge in fragment.graph.edges
    )


def test_pnpm_lockfile_is_a_reproducible_dependency_set(tmp_path):
    _write(
        tmp_path,
        "package.json",
        '{"packageManager":"pnpm@9.15.0","dependencies":{"react":"^18"}}',
    )
    _write(
        tmp_path,
        "pnpm-lock.yaml",
        """
lockfileVersion: '9.0'
packages:
  react@18.3.1: {}
snapshots:
  react@18.3.1: {}
""",
    )
    languages = detect_languages(str(tmp_path))
    workspace = NodeProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = NodeProvider().build_fragment(
        str(tmp_path), workspace, ProviderTarget("node:20-bookworm")
    )

    package = next(
        node for node in fragment.graph.nodes if node.type is NodeType.PACKAGE
    )
    deps = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.DEPENDENCY_SET
    )
    assert package.name == "react"
    assert package.version == "18.3.1"
    assert deps.lock_digest.startswith("sha256:")
    assert deps.data["reproducibility_confidence"] == "high"
    assert deps.setup_commands == (
        "corepack enable && pnpm install --frozen-lockfile",
    )


def test_go_provider_maps_source_import_to_module(tmp_path):
    _write(
        tmp_path,
        "go.mod",
        "module example.com/demo\n\ngo 1.22\n\n"
        "require github.com/gin-gonic/gin v1.10.0\n",
    )
    _write(tmp_path, "go.sum", "github.com/gin-gonic/gin v1.10.0 h1:demo\n")
    _write(
        tmp_path,
        "main.go",
        'package main\nimport "github.com/gin-gonic/gin"\nfunc main(){_ = gin.Mode()}\n',
    )
    languages = detect_languages(str(tmp_path))
    provider = GoProvider()
    workspace = provider.detect_workspaces(str(tmp_path), languages)[0]

    fragment = provider.build_fragment(
        str(tmp_path), workspace, ProviderTarget("golang:1.22-bookworm")
    )

    imports = [node for node in fragment.graph.nodes if node.type is NodeType.IMPORT]
    assert [node.name for node in imports] == ["github.com/gin-gonic/gin"]
    assert any(edge.relation is EdgeType.MAPS_TO for edge in fragment.graph.edges)
    deps = next(node for node in fragment.graph.nodes
                if node.type is NodeType.DEPENDENCY_SET)
    assert deps.setup_commands == ("go mod download",)
    assert fragment.test_commands == ("go test ./...",)


def test_go_provider_ignores_test_fixture_modules(tmp_path):
    _write(tmp_path, "package.json", '{"scripts":{"test":"jest"}}')
    _write(tmp_path, "src/index.ts", "export const value = 1\n")
    _write(
        tmp_path,
        "__tests__/fixtures/repo-map/go/go.mod",
        "module example.com/fixture\n\ngo 1.21\n",
    )
    languages = detect_languages(str(tmp_path))

    workspaces = GoProvider().detect_workspaces(str(tmp_path), languages)

    assert workspaces == ()


def test_go_scanner_honors_target_build_tags_and_go_work_modules(tmp_path):
    _write(
        tmp_path,
        "go.mod",
        "module example.com/main\n\ngo 1.22\n"
        "require example.com/linuxdep v1.0.0\n"
        "require example.com/windowsdep v1.0.0\n",
    )
    _write(tmp_path, "go.work", "go 1.22\nuse ./local\n")
    _write(tmp_path, "local/go.mod", "module example.com/local\n\ngo 1.22\n")
    _write(
        tmp_path,
        "platform_linux.go",
        '//go:build linux && arm64\npackage main\n'
        'import "example.com/linuxdep/pkg"\n'
        'import "example.com/local/pkg"\n',
    )
    _write(
        tmp_path,
        "platform_windows.go",
        '//go:build windows\npackage main\nimport "example.com/windowsdep/pkg"\n',
    )
    languages = detect_languages(str(tmp_path))
    workspace = GoProvider().detect_workspaces(str(tmp_path), languages)[0]

    refs = GoProvider().scan_imports(
        str(tmp_path),
        workspace,
        ProviderTarget("golang:1.22", platform="linux/arm64"),
    )

    assert [item.raw_specifier for item in refs] == ["example.com/linuxdep/pkg"]


def test_go_native_resolver_adds_transitive_module_edges(tmp_path):
    _write(
        tmp_path,
        "go.mod",
        "module example.com/demo\n\ngo 1.22\nrequire example.com/a v1.2.0\n",
    )
    output = """
__JAYINT_GO_MODULES_JSON__
{"Path":"example.com/demo","Main":true}
{"Path":"example.com/a","Version":"v1.2.0"}
{"Path":"example.com/b","Version":"v2.0.0"}
__JAYINT_GO_MODULE_GRAPH__
example.com/demo example.com/a@v1.2.0
example.com/a@v1.2.0 example.com/b@v2.0.0
"""
    sandbox = _ResolverSandbox(result=(0, output))
    languages = detect_languages(str(tmp_path))
    workspace = GoProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = GoProvider().build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("golang:1.22"),
        sandbox=sandbox,
    )

    packages = {
        node.name: node for node in fragment.graph.nodes
        if node.type is NodeType.PACKAGE
    }
    assert set(packages) == {"example.com/a", "example.com/b"}
    assert any(
        edge.src == packages["example.com/a"].id
        and edge.dst == packages["example.com/b"].id
        and edge.relation is EdgeType.REQUIRES
        for edge in fragment.graph.edges
    )


def test_rust_provider_resolves_package_workspace_version_inheritance(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        """
[workspace]
resolver = "3"

[workspace.package]
rust-version = "1.85"

[package]
name = "demo"
version = "0.1.0"
rust-version.workspace = true
""",
    )
    languages = detect_languages(str(tmp_path))

    workspace = RustProvider().detect_workspaces(str(tmp_path), languages)[0]

    assert workspace.version_constraint == "1.85"


def test_rust_image_commands_preserve_official_cargo_path(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        """
[package]
name = "demo"
version = "0.1.0"
rust-version = "1.85"
edition = "2024"
""",
    )
    languages = detect_languages(str(tmp_path))
    workspace = RustProvider().detect_workspaces(str(tmp_path), languages)[0]
    sandbox = _ResolverSandbox(result=(1, "offline"))

    fragment = RustProvider().build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("rust:1.85"),
        sandbox=sandbox,
    )

    deps = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.DEPENDENCY_SET
    )
    project = next(
        node for node in fragment.graph.nodes if node.type is NodeType.PROJECT
    )
    tool = next(
        node for node in fragment.graph.nodes if node.type is NodeType.TOOL
    )
    assert deps.setup_commands == ("cargo fetch",)
    assert deps.check_command == "cargo metadata --format-version 1"
    assert project.setup_commands == ("cargo build --all-targets",)
    assert tool.setup_commands == (
        "ln -sf /usr/local/cargo/bin/cargo /usr/local/bin/cargo && "
        "ln -sf /usr/local/cargo/bin/rustc /usr/local/bin/rustc",
    )
    assert tool.check_command == "cargo --version"
    assert fragment.test_commands == ("cargo test --all-targets",)
    assert discover_polyglot_test_commands(str(tmp_path), languages) == (
        "cargo test --all-targets",
    )
    assert (
        "export PATH=/usr/local/cargo/bin:/root/.cargo/bin:$PATH && "
        in sandbox.commands[0][1]
    )


def test_rust_provider_preserves_dependency_rename_mapping(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname="demo"\nversion="0.1.0"\nrust-version="1.82"\n'
        '[dependencies]\njson={package="serde_json",version="1"}\n',
    )
    _write(
        tmp_path,
        "Cargo.lock",
        'version = 3\n'
        '[[package]]\nname="demo"\nversion="0.1.0"\n'
        'dependencies=["serde_json 1.0.120"]\n'
        '[[package]]\nname="serde_json"\nversion="1.0.120"\n',
    )
    _write(tmp_path, "src/lib.rs", "use json::Value;\n")
    languages = detect_languages(str(tmp_path))
    provider = RustProvider()
    workspace = provider.detect_workspaces(str(tmp_path), languages)[0]

    fragment = provider.build_fragment(
        str(tmp_path), workspace, ProviderTarget("python:3.11-slim")
    )

    requirement = next(node for node in fragment.graph.nodes
                       if node.type is NodeType.REQUIREMENT)
    import_node = next(node for node in fragment.graph.nodes
                       if node.type is NodeType.IMPORT)
    package = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.PACKAGE and node.name == "serde_json"
    )
    assert requirement.name == "serde_json"
    assert import_node.name == "json"
    assert package.name == "serde_json"
    assert any(
        edge.src == import_node.id and edge.dst == package.id
        and edge.relation is EdgeType.MAPS_TO
        for edge in fragment.graph.edges
    )


def test_cargo_metadata_preserves_multiple_packages_and_transitive_edges(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname="demo"\nversion="0.1.0"\n'
        '[dependencies]\nserde="1"\n',
    )
    metadata = {
        "workspace_members": ["path+file:///app#demo@0.1.0"],
        "packages": [
            {"id": "path+file:///app#demo@0.1.0", "name": "demo", "version": "0.1.0"},
            {"id": "registry+serde@1.0.210", "name": "serde", "version": "1.0.210"},
            {"id": "registry+serde_core@1.0.210", "name": "serde_core", "version": "1.0.210"},
        ],
        "resolve": {
            "nodes": [
                {
                    "id": "registry+serde@1.0.210",
                    "deps": [{"name": "serde_core", "pkg": "registry+serde_core@1.0.210"}],
                },
                {"id": "registry+serde_core@1.0.210", "deps": []},
            ]
        },
    }
    sandbox = _ResolverSandbox(
        result=(0, "__JAYINT_CARGO_METADATA_JSON__\n" + __import__("json").dumps(metadata))
    )
    languages = detect_languages(str(tmp_path))
    workspace = RustProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = RustProvider().build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("rust:1.82"),
        sandbox=sandbox,
    )

    packages = {
        node.name: node for node in fragment.graph.nodes
        if node.type is NodeType.PACKAGE
    }
    assert set(packages) == {"serde", "serde_core"}
    assert any(
        edge.src == packages["serde"].id
        and edge.dst == packages["serde_core"].id
        for edge in fragment.graph.edges
    )


def test_maven_provider_preserves_manifest_requirement_and_build_blocks(tmp_path):
    _write(
        tmp_path,
        "pom.xml",
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example</groupId><artifactId>demo</artifactId><version>1.0</version>
  <properties><maven.compiler.release>17</maven.compiler.release></properties>
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
      <version>2.17.2</version>
    </dependency>
  </dependencies>
</project>
""",
    )
    _write(
        tmp_path,
        "src/main/java/example/App.java",
        "package example;\nimport com.fasterxml.jackson.databind.ObjectMapper;\n",
    )
    languages = detect_languages(str(tmp_path))
    workspace = MavenProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = MavenProvider().build_fragment(
        str(tmp_path), workspace, ProviderTarget("eclipse-temurin:17-jdk")
    )

    requirement = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.REQUIREMENT
    )
    deps = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.DEPENDENCY_SET
    )
    project = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.PROJECT
    )
    assert requirement.name == "com.fasterxml.jackson.core:jackson-databind"
    assert requirement.declared_constraint == "2.17.2"
    assert deps.setup_commands == ("mvn -B -DskipTests dependency:go-offline",)
    assert project.setup_commands == ("mvn -B -DskipTests package",)
    assert fragment.test_commands == ("mvn -B test",)


def test_maven_class_index_maps_java_import_to_resolved_artifact(tmp_path):
    _write(
        tmp_path,
        "pom.xml",
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example</groupId><artifactId>demo</artifactId><version>1</version>
  <dependencies><dependency>
    <groupId>com.fasterxml.jackson.core</groupId>
    <artifactId>jackson-databind</artifactId><version>2.17.2</version>
  </dependency></dependencies>
</project>
""",
    )
    _write(
        tmp_path,
        "src/main/java/example/App.java",
        "package example;\nimport com.fasterxml.jackson.databind.ObjectMapper;\n",
    )
    tree = {
        "groupId": "example",
        "artifactId": "demo",
        "version": "1",
        "children": [{
            "groupId": "com.fasterxml.jackson.core",
            "artifactId": "jackson-databind",
            "type": "jar",
            "version": "2.17.2",
            "children": [],
        }],
    }
    output = (
        "__JAYINT_MAVEN_TREE_JSON__\n"
        + __import__("json").dumps(tree)
        + "\n__JAYINT_JAVA_CLASS__\t"
        "com.fasterxml.jackson.databind.ObjectMapper\t"
        "com.fasterxml.jackson.core:jackson-databind:jar:2.17.2\n"
    )
    sandbox = _ResolverSandbox(result=(0, output))
    languages = detect_languages(str(tmp_path))
    workspace = MavenProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = MavenProvider().build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("maven:3.9-eclipse-temurin-17"),
        sandbox=sandbox,
    )

    import_node = next(
        node for node in fragment.graph.nodes if node.type is NodeType.IMPORT
    )
    package = next(
        node for node in fragment.graph.nodes if node.type is NodeType.PACKAGE
    )
    assert any(
        edge.src == import_node.id
        and edge.dst == package.id
        and edge.relation is EdgeType.MAPS_TO
        for edge in fragment.graph.edges
    )


def test_java_wrapper_is_used_by_build_and_test_discovery(tmp_path):
    _write(
        tmp_path,
        "server/pom.xml",
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example</groupId><artifactId>demo</artifactId><version>1</version>
</project>
""",
    )
    _write(tmp_path, "server/mvnw", "#!/bin/sh\n")
    languages = detect_languages(str(tmp_path))
    workspace = MavenProvider().detect_workspaces(str(tmp_path), languages)[0]

    assert workspace.command_runner == "./mvnw"
    assert MavenProvider().test_commands(workspace) == (
        "cd server && ./mvnw -B test",
    )


def test_maven_zip_wrapper_installs_unzip_before_wrapper_execution(tmp_path):
    _write(
        tmp_path,
        "pom.xml",
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example</groupId><artifactId>demo</artifactId><version>1</version>
</project>
""",
    )
    _write(tmp_path, "mvnw", "#!/bin/sh\n")
    _write(
        tmp_path,
        ".mvn/wrapper/maven-wrapper.properties",
        (
            "distributionUrl=https://repo.maven.apache.org/maven2/org/apache/"
            "maven/apache-maven/3.9.12/apache-maven-3.9.12-bin.zip\n"
            "distributionSha256Sum=expected\n"
        ),
    )
    languages = detect_languages(str(tmp_path))
    workspace = MavenProvider().detect_workspaces(str(tmp_path), languages)[0]
    sandbox = _ResolverSandbox(result=(1, "resolver not needed for this assertion"))

    fragment = MavenProvider().build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("eclipse-temurin:21-jdk-noble"),
        sandbox=sandbox,
    )

    tool = next(
        node for node in fragment.graph.nodes if node.type is NodeType.TOOL
    )
    assert tool.setup_commands == (
        "apt-get update && apt-get install -y --no-install-recommends unzip",
    )
    assert tool.check_command == "./mvnw -version"
    resolver_command = sandbox.commands[0][1]
    assert "apt-get install -y --no-install-recommends unzip" in resolver_command
    assert resolver_command.index(
        "apt-get install -y --no-install-recommends unzip"
    ) < resolver_command.index("./mvnw -B -DskipTests dependency:tree")


def test_gradle_provider_creates_java_import_and_dependency_transaction(tmp_path):
    _write(
        tmp_path,
        "build.gradle.kts",
        """
plugins { java }
java { toolchain { languageVersion.set(JavaLanguageVersion.of(21)) } }
dependencies {
    implementation("com.google.guava:guava:33.2.1-jre")
}
""",
    )
    _write(
        tmp_path,
        "src/main/java/example/App.java",
        "package example;\nimport com.google.common.collect.ImmutableList;\n",
    )
    languages = detect_languages(str(tmp_path))
    workspace = GradleProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = GradleProvider().build_fragment(
        str(tmp_path), workspace, ProviderTarget("eclipse-temurin:21-jdk")
    )

    assert any(
        node.type is NodeType.IMPORT
        and node.name == "com.google.common.collect.ImmutableList"
        for node in fragment.graph.nodes
    )
    requirement = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.REQUIREMENT
    )
    deps = next(
        node for node in fragment.graph.nodes
        if node.type is NodeType.DEPENDENCY_SET
    )
    assert requirement.name == "com.google.guava:guava"
    assert requirement.declared_constraint == "33.2.1-jre"
    assert deps.setup_commands == ("gradle --no-daemon dependencies",)


def test_gradle_class_index_maps_import_to_artifact(tmp_path):
    _write(
        tmp_path,
        "build.gradle",
        'plugins { id "java" }\n'
        'dependencies { implementation "com.google.guava:guava:33.2.1-jre" }\n',
    )
    _write(
        tmp_path,
        "src/main/java/example/App.java",
        "package example;\nimport com.google.common.collect.ImmutableList;\n",
    )
    output = (
        "__JAYINT_GRADLE_ARTIFACT__\t"
        "com.google.guava:guava:33.2.1-jre\t/cache/guava.jar\n"
        "__JAYINT_JAVA_CLASS__\t"
        "com.google.common.collect.ImmutableList\t"
        "com.google.guava:guava:33.2.1-jre\n"
    )
    sandbox = _ResolverSandbox(result=(0, output))
    languages = detect_languages(str(tmp_path))
    workspace = GradleProvider().detect_workspaces(str(tmp_path), languages)[0]

    fragment = GradleProvider().build_fragment(
        str(tmp_path),
        workspace,
        ProviderTarget("gradle:8.10-jdk21"),
        sandbox=sandbox,
    )

    import_node = next(
        node for node in fragment.graph.nodes if node.type is NodeType.IMPORT
    )
    package = next(
        node for node in fragment.graph.nodes if node.type is NodeType.PACKAGE
    )
    assert any(
        edge.src == import_node.id and edge.dst == package.id
        and edge.relation is EdgeType.MAPS_TO
        for edge in fragment.graph.edges
    )
