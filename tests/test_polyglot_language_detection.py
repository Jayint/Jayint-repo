from src.language_handlers import detect_language, detect_languages


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_detects_language_set_versions_roles_and_evidence(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
[project]
name = "demo"
requires-python = ">=3.10,<3.13"
[build-system]
requires = ["maturin"]
build-backend = "maturin"
""",
    )
    _write(tmp_path, "tests/test_demo.py", "def test_ok(): assert True\n")
    _write(
        tmp_path,
        "frontend/package.json",
        '{"engines":{"node":">=20"},"scripts":{"test":"vitest"},"dependencies":{"react":"^18"}}',
    )
    _write(tmp_path, "frontend/src/app.ts", "import React from 'react'\n")
    _write(
        tmp_path,
        "native/Cargo.toml",
        '[package]\nname="native"\nversion="0.1.0"\nrust-version="1.82"\n'
        '[dependencies]\npyo3="0.22"\n',
    )
    _write(tmp_path, "native/src/lib.rs", "use pyo3::prelude::*;\n")

    requirements = detect_languages(str(tmp_path))
    by_language = {item.language: item for item in requirements}

    assert set(by_language) == {"python", "typescript", "rust"}
    assert by_language["python"].role == "primary_runtime"
    assert by_language["python"].version_constraint == ">=3.10,<3.13"
    assert by_language["typescript"].role == "secondary_runtime"
    assert by_language["typescript"].version_constraint == ">=20"
    assert by_language["rust"].role == "build_tool"
    assert by_language["rust"].version_constraint == "1.82"
    assert "pyproject.toml" in by_language["python"].evidence
    assert detect_language(*_legacy_inventory(tmp_path)) == "python"


def _legacy_inventory(root):
    paths = []
    contents = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            paths.append(relative)
            if path.name in {
                "pyproject.toml", "package.json", "Cargo.toml",
                "rust-toolchain.toml",
            }:
                contents[relative] = path.read_text()
    return "\n".join(paths), contents


def test_typescript_does_not_create_a_second_javascript_runtime(tmp_path):
    _write(tmp_path, "package.json", '{"scripts":{"test":"vitest"}}')
    _write(tmp_path, "tsconfig.json", '{"compilerOptions":{}}')
    _write(tmp_path, "src/index.ts", "export const value = 1\n")

    requirements = detect_languages(str(tmp_path))

    assert [item.language for item in requirements] == ["typescript"]


def test_test_only_requirements_without_python_source_is_not_a_runtime(tmp_path):
    _write(tmp_path, "package.json", '{"scripts":{"test":"grunt test"}}')
    _write(tmp_path, "src/index.js", "module.exports = 1\n")
    _write(
        tmp_path,
        "tests/api/requirements.txt",
        "wikitools>=1.1\nposter>=0.8\n",
    )

    requirements = detect_languages(str(tmp_path))

    assert [item.language for item in requirements] == ["javascript"]


def test_test_requirements_with_python_source_remains_detectable(tmp_path):
    _write(tmp_path, "tests/requirements.txt", "pytest>=8\n")
    _write(tmp_path, "tests/test_api.py", "def test_api():\n    assert True\n")

    requirements = detect_languages(str(tmp_path))

    assert [item.language for item in requirements] == ["python"]


def test_rust_workspace_version_is_detected(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[workspace]\nmembers=["cli"]\n'
        '[workspace.package]\nrust-version="1.85"\n',
    )
    _write(
        tmp_path,
        "cli/Cargo.toml",
        '[package]\nname="cli"\nversion="0.1.0"\nrust-version.workspace=true\n',
    )
    _write(tmp_path, "cli/src/lib.rs", "pub fn ok() {}\n")

    rust = next(
        item for item in detect_languages(str(tmp_path))
        if item.language == "rust"
    )

    assert rust.version_constraint == "1.85"


def test_java_runtime_requirement_precedes_compiler_release(tmp_path):
    _write(
        tmp_path,
        "pom.xml",
        """
<project>
  <properties><maven.compiler.release>11</maven.compiler.release></properties>
  <build><plugins><plugin><configuration><rules>
    <requireJavaVersion><version>21</version></requireJavaVersion>
  </rules></configuration></plugin></plugins></build>
</project>
""",
    )
    _write(tmp_path, "src/main/java/App.java", "class App {}\n")

    java = next(
        item for item in detect_languages(str(tmp_path))
        if item.language == "java"
    )

    assert java.version_constraint == "21"
