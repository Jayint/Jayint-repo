from __future__ import annotations

import textwrap
from pathlib import Path

from python_deps.depgraph.test_intent import discover_test_dependency_intent


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _add_test(root: Path) -> None:
    _write(root, "tests/test_example.py", "def test_ok():\n    assert True\n")


def test_selects_named_test_extra_but_not_docs(tmp_path):
    _add_test(tmp_path)
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        [project.optional-dependencies]
        test = ["pytest-cov"]
        docs = ["sphinx"]
        """,
    )

    intent = discover_test_dependency_intent(tmp_path)
    assert intent.needed_groups == frozenset({"test"})


def test_selects_pep735_dev_group_only_when_it_contains_pytest_tools(tmp_path):
    _add_test(tmp_path)
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        [dependency-groups]
        dev = ["ruff", "pytest-xdist"]
        development = ["mypy"]
        """,
    )

    intent = discover_test_dependency_intent(tmp_path)
    assert intent.needed_groups == frozenset({"dev"})


def test_selects_nested_test_requirements_and_tox_deps(tmp_path):
    _add_test(tmp_path)
    _write(tmp_path, "tests/requirements.txt", "trio\n")
    _write(tmp_path, "tox.ini", "[testenv]\ndeps = pytest-asyncio\ncommands = pytest\n")

    intent = discover_test_dependency_intent(tmp_path)
    assert intent.needed_groups == frozenset({"test"})


def test_no_pytest_surface_keeps_optional_groups_dormant(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        [project.optional-dependencies]
        test = ["pytest-cov"]
        """,
    )
    assert discover_test_dependency_intent(tmp_path).needed_groups == frozenset()


def test_hatch_test_features_activate_named_optional_group_in_monorepo(tmp_path):
    project = tmp_path / "packages" / "core"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_core.py").write_text("def test_ok(): pass\n")
    (project / "pyproject.toml").write_text(
        "[project]\nname='core'\nversion='0.1.0'\n"
        "[project.optional-dependencies]\nall=['pandas']\n"
        "[tool.hatch.envs.hatch-test]\nfeatures=['all']\n"
        "extra-dependencies=['openai']\n"
    )

    intent = discover_test_dependency_intent(tmp_path)
    assert intent.needed_groups == frozenset({"all", "test"})


def test_package_style_tests_in_multiple_projects_select_importlib_mode(tmp_path):
    for name in ("alpha", "beta"):
        project = tmp_path / "packages" / name
        _write(
            project,
            "pyproject.toml",
            f"""
            [project]
            name = "{name}"
            version = "0.1.0"
            """,
        )
        _write(project, "tests/__init__.py", "")
        _write(project, f"tests/test_{name}.py", "def test_ok(): pass\n")

    intent = discover_test_dependency_intent(tmp_path)
    assert intent.pytest_addopts == ("--import-mode=importlib",)


def test_single_or_namespace_tests_package_keeps_default_import_mode(tmp_path):
    for name in ("alpha", "beta"):
        project = tmp_path / "packages" / name
        _write(
            project,
            "pyproject.toml",
            f"[project]\nname='{name}'\nversion='0.1.0'\n",
        )
        _write(project, f"tests/test_{name}.py", "def test_ok(): pass\n")
    _write(tmp_path / "packages" / "alpha", "tests/__init__.py", "")

    assert discover_test_dependency_intent(tmp_path).pytest_addopts == ()


def test_explicit_repo_import_mode_is_never_overridden(tmp_path):
    for name in ("alpha", "beta"):
        project = tmp_path / "packages" / name
        _write(
            project,
            "pyproject.toml",
            f"[project]\nname='{name}'\nversion='0.1.0'\n",
        )
        _write(project, "tests/__init__.py", "")
        _write(project, f"tests/test_{name}.py", "def test_ok(): pass\n")
    _write(
        tmp_path,
        "pytest.ini",
        "[pytest]\naddopts = --import-mode=prepend\n",
    )

    assert discover_test_dependency_intent(tmp_path).pytest_addopts == ()
