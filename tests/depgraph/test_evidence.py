"""Evidence-parsing unit tests for extras handling (Task 8: targeted extras).

Bug this guards: per-dep extras (``uvicorn[standard]``) were silently stripped
by ``_parse_requirement_line`` before ever reaching a ``PythonRequirement``, so
the extra's transitive deps vanished under a ``--no-deps`` install. These tests
pin the 3-tuple -> 4-tuple parse change and the ``PythonRequirement.extras``
field it feeds, plus the existing optional-dependency group tag it must not
disturb.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from python_deps.evidence import (
    _add_requirement_line,
    _parse_requirement_line,
    collect_python_dependency_evidence,
    discover_test_requirement_files,
    discover_test_project_roots,
)
from python_deps.models import PythonRequirement


def test_parse_requirement_line_returns_four_tuple_with_extras():
    parsed = _parse_requirement_line("uvicorn[standard]>=0.20")
    assert parsed == ("uvicorn", ">=0.20", "", ("standard",))


def test_parse_requirement_line_no_extras_yields_empty_tuple():
    parsed = _parse_requirement_line("requests>=2.0")
    assert parsed == ("requests", ">=2.0", "", ())


def test_parse_requirement_line_multiple_extras_sorted():
    parsed = _parse_requirement_line("uvicorn[standard,dotenv]>=0.20")
    assert parsed is not None
    name, specifier, marker, extras = parsed
    assert name == "uvicorn"
    assert specifier == ">=0.20"
    assert marker == ""
    assert extras == ("dotenv", "standard")  # sorted, deterministic


def test_parse_requirement_line_vcs_url_yields_no_extras():
    # The git+/egg= fallback path never parses extras; must still be a 4-tuple.
    parsed = _parse_requirement_line("git+https://example.com/x.git#egg=widget")
    assert parsed == ("widget", "git+https://example.com/x.git#egg=widget", "", ())


def test_add_requirement_line_stores_extras_on_requirement():
    target: list[PythonRequirement] = []
    _add_requirement_line(target, "uvicorn[standard]>=0.20", "pyproject.toml:project.dependencies")
    assert len(target) == 1
    req = target[0]
    assert req.name == "uvicorn"
    assert req.extras == ("standard",)
    assert req.specifier == ">=0.20"


def test_add_requirement_line_no_extras_defaults_empty_tuple():
    target: list[PythonRequirement] = []
    _add_requirement_line(target, "requests>=2.0", "pyproject.toml:project.dependencies")
    assert target[0].extras == ()


def _write(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def test_collect_pyproject_optional_dependency_group_tag_preserved(tmp_path):
    # The group tag (kind + source suffix) must survive alongside the new
    # extras field -- this is what roots.py's needed_extras gate reads.
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["uvicorn[standard]>=0.20"]

        [project.optional-dependencies]
        test = ["pytest"]
        docs = ["sphinx"]
        """,
    )
    evidence = collect_python_dependency_evidence(str(tmp_path))
    by_name = {req.name: req for req in evidence.declared_dependencies}

    uvicorn = by_name["uvicorn"]
    assert uvicorn.kind == "dependency"
    assert uvicorn.extras == ("standard",)

    pytest_req = by_name["pytest"]
    assert pytest_req.kind == "optional_dependency"
    assert pytest_req.source.endswith("optional-dependencies.test")

    sphinx_req = by_name["sphinx"]
    assert sphinx_req.kind == "optional_dependency"
    assert sphinx_req.source.endswith("optional-dependencies.docs")


def test_collect_pep735_dependency_group_as_optional_evidence(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["requests"]

        [dependency-groups]
        dev = ["pytest", "pytest-xdist>=3"]
        docs = ["sphinx"]
        """,
    )
    evidence = collect_python_dependency_evidence(tmp_path)
    by_name = {req.name: req for req in evidence.declared_dependencies}

    assert by_name["pytest-xdist"].kind == "optional_dependency"
    assert by_name["pytest-xdist"].source.endswith("dependency-groups.dev")
    assert by_name["sphinx"].source.endswith("dependency-groups.docs")


def test_collect_nested_test_requirements_as_optional_evidence(tmp_path):
    _write(tmp_path, "tests/requirements.txt", "trio>=0.29\npytest-asyncio\n")
    evidence = collect_python_dependency_evidence(tmp_path)
    by_name = {req.name: req for req in evidence.declared_dependencies}

    assert by_name["trio"].kind == "optional_dependency"
    assert "test-requirements.test" in by_name["trio"].source


def test_collect_tox_deps_as_optional_evidence(tmp_path):
    _write(
        tmp_path,
        "tox.ini",
        """
        [testenv]
        deps =
            pytest
            trio>=0.29
        commands = pytest
        """,
    )
    evidence = collect_python_dependency_evidence(tmp_path)
    trio = next(req for req in evidence.declared_dependencies if req.name == "trio")

    assert trio.kind == "optional_dependency"
    assert trio.source == "tox.ini:tox-deps.test"


def test_collects_only_test_bearing_nested_projects_with_provenance(tmp_path):
    _write(
        tmp_path,
        "packages/core/pyproject.toml",
        """
        [project]
        name = "core-pkg"
        version = "0.1.0"
        dependencies = ["requests>=2"]

        [project.optional-dependencies]
        test = ["pytest-xdist"]
        """,
    )
    _write(tmp_path, "packages/core/tests/test_core.py", "def test_ok(): pass\n")
    _write(
        tmp_path,
        "packages/unrelated/pyproject.toml",
        """
        [project]
        name = "unrelated"
        version = "0.1.0"
        dependencies = ["sphinx"]
        """,
    )

    roots = discover_test_project_roots(tmp_path)
    assert [path.relative_to(tmp_path).as_posix() for path in roots] == ["packages/core"]

    evidence = collect_python_dependency_evidence(tmp_path)
    by_name = {req.name: req for req in evidence.declared_dependencies}
    assert by_name["requests"].source.startswith("packages/core/pyproject.toml:")
    assert by_name["pytest-xdist"].source.endswith("optional-dependencies.test")
    assert "sphinx" not in by_name


def test_nested_tool_only_pyproject_is_not_an_installable_project(tmp_path):
    _write(
        tmp_path,
        "apps/proxy/pyproject.toml",
        """
        [tool.setuptools]
        py-modules = []
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """,
    )
    _write(tmp_path, "apps/proxy/tests/test_proxy.py", "def test_ok(): pass\n")
    assert discover_test_project_roots(tmp_path) == ()


def test_installable_project_discovery_uses_manifest_content(tmp_path):
    cases = {
        "pep621": ("pyproject.toml", "[project]\nname='demo'\n"),
        "backend": ("pyproject.toml", "[build-system]\nbuild-backend='demo.backend'\n"),
        "setup_py": ("setup.py", "from setuptools import setup\nsetup()\n"),
        "setup_cfg": ("setup.cfg", "[metadata]\nname = demo\n"),
    }
    for name, (manifest, body) in cases.items():
        root = tmp_path / name
        _write(root, manifest, body)
        _write(root, "tests/test_demo.py", "def test_ok(): pass\n")
        assert discover_test_project_roots(root) == (root,)

    non_installable = tmp_path / "pytest_cfg"
    _write(non_installable, "setup.cfg", "[tool:pytest]\naddopts = -q\n")
    _write(non_installable, "tests/test_demo.py", "def test_ok(): pass\n")
    assert discover_test_project_roots(non_installable) == ()


def test_recursive_requirement_include_stays_inside_repo_and_avoids_cycles(tmp_path):
    _write(tmp_path, "requirements.txt", "-r requirements/base.txt\nrequests\n")
    _write(
        tmp_path,
        "requirements/base.txt",
        "urllib3>=2\n-r ../requirements.txt\n-r ../../outside.txt\n",
    )
    evidence = collect_python_dependency_evidence(tmp_path)
    names = [req.name for req in evidence.declared_dependencies]
    assert names.count("requests") == 1
    assert names.count("urllib3") == 1


def test_pytest_config_infers_only_high_confidence_plugins(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "configured"
        version = "0.1.0"
        dependencies = ["pytest-cov<6"]

        [tool.pytest.ini_options]
        addopts = "--cov=src -n auto --timeout=20 -ra"
        asyncio_mode = "auto"
        """,
    )
    evidence = collect_python_dependency_evidence(tmp_path)
    by_name = {}
    for requirement in evidence.declared_dependencies:
        by_name.setdefault(requirement.name, []).append(requirement)
    assert len(by_name["pytest-cov"]) == 1
    assert by_name["pytest-cov"][0].specifier == "<6"
    assert by_name["pytest-xdist"][0].source.startswith("pytest-config:")
    assert by_name["pytest-timeout"][0].kind == "optional_dependency"
    assert by_name["pytest-asyncio"][0].kind == "optional_dependency"


def test_hatch_test_env_extra_dependencies_are_test_scoped(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "hatch-project"
        version = "0.1.0"

        [project.optional-dependencies]
        all = ["pandas"]

        [tool.hatch.envs.hatch-test]
        features = ["all"]
        extra-dependencies = ["openai"]
        """,
    )
    _write(tmp_path, "tests/test_hatch.py", "def test_ok(): pass\n")
    evidence = collect_python_dependency_evidence(tmp_path)
    openai = next(req for req in evidence.declared_dependencies if req.name == "openai")
    assert openai.kind == "optional_dependency"
    assert "hatch-test-deps.test" in openai.source


def test_collects_nested_requirements_only_from_test_bearing_subtrees(tmp_path):
    _write(tmp_path, "impl_a/requirements.txt", "torch==2.0.1\n")
    _write(tmp_path, "impl_a/src/test_model.py", "def test_model(): pass\n")
    _write(tmp_path, "impl_b/requirements-dev.txt", "paddlepaddle==2.6\n")
    _write(tmp_path, "impl_b/tests/test_op.py", "def test_op(): pass\n")
    _write(tmp_path, "docs/requirements.txt", "sphinx\n")
    _write(tmp_path, "unused/requirements.txt", "mkdocs\n")

    files = discover_test_requirement_files(tmp_path)
    assert [path.relative_to(tmp_path).as_posix() for path in files] == [
        "impl_a/requirements.txt",
        "impl_b/requirements-dev.txt",
    ]

    evidence = collect_python_dependency_evidence(tmp_path)
    by_name = {req.name: req for req in evidence.declared_dependencies}
    assert by_name["torch"].source == "impl_a/requirements.txt"
    assert by_name["paddlepaddle"].source == "impl_b/requirements-dev.txt"
    assert "sphinx" not in by_name
    assert "mkdocs" not in by_name
