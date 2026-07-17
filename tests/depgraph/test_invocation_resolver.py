"""Deterministic test-invocation resolver — synthetic-fixture unit tests.

Every fixture is a small on-disk temp repo written by the test itself (no network,
no real clones). Each case is modeled on a real repo the collection-graph POC
exercised (repo named in the test docstring), covering one branch of the
resolver's three build priorities: interpreter policy, install-target discovery,
and the deterministic pytest path/config half (rootdir / pythonpath / import-mode
/ layout).

Design spec: docs/superpowers/specs/2026-07-16-collection-graph-simplification-design.md
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from graph.python.invocation_resolver import (
    SUPPORTED_MINORS,
    TestEnvPlan,
    resolve,
)


def _write(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")


def _repo(tmp_path: Path, name: str = "proj") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    return repo


# --------------------------------------------------------------------------- #
# Layout + pythonpath (deterministic pytest path/config half)
# --------------------------------------------------------------------------- #


def test_src_layout_declared_pythonpath(tmp_path):
    """gitingest: src-layout + declared ``pythonpath = ['src']``."""
    repo = _repo(tmp_path, "gitingest")
    _write(
        repo,
        "pyproject.toml",
        """
        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [project]
        name = "gitingest"
        version = "0.1.0"
        requires-python = ">=3.11"
        dependencies = ["click"]

        [tool.pytest.ini_options]
        pythonpath = ["src"]
        """,
    )
    _write(repo, "src/gitingest/__init__.py", "")

    plan = resolve(str(repo))

    assert isinstance(plan, TestEnvPlan)
    assert plan.layout == "src"
    assert plan.pythonpath == ("src",)
    assert plan.rootdir == "."
    assert plan.import_mode == "prepend"
    assert plan.project_dirs == (".",)
    assert plan.interpreter == "3.11"
    assert plan.interpreter_confidence == "declared"
    assert "editable:." in plan.install_plan


def test_flat_layout_declared_pythonpath(tmp_path):
    """DDNS: flat layout + declared ``pythonpath = ['.', 'tests']``."""
    repo = _repo(tmp_path, "ddns")
    _write(
        repo,
        "pyproject.toml",
        """
        [build-system]
        requires = ["setuptools"]
        build-backend = "setuptools.build_meta"

        [project]
        name = "ddns"
        version = "0.1.0"
        dependencies = ["requests"]

        [tool.pytest.ini_options]
        pythonpath = [".", "tests"]
        """,
    )
    _write(repo, "ddns/__init__.py", "")

    plan = resolve(str(repo))

    assert plan.pythonpath == (".", "tests")
    assert plan.layout == "flat"
    assert plan.project_dirs == (".",)
    assert "flat_layout_ambiguous" not in plan.flags


def test_pytest_ini_precedence_over_pyproject(tmp_path):
    """algo: pytest.ini config wins over pyproject's ``[tool.pytest.ini_options]``.

    (There is no ``testpaths`` field on the plan; precedence is asserted through
    ``pythonpath``, which pytest.ini and pyproject disagree on here.)
    """
    repo = _repo(tmp_path, "algo")
    _write(
        repo,
        "pytest.ini",
        """
        [pytest]
        pythonpath = lib
        """,
    )
    _write(
        repo,
        "pyproject.toml",
        """
        [build-system]
        requires = ["setuptools"]
        build-backend = "setuptools.build_meta"

        [project]
        name = "algo"
        version = "0.1.0"

        [tool.pytest.ini_options]
        pythonpath = ["wrong"]
        """,
    )
    _write(repo, "lib/algo/__init__.py", "")

    plan = resolve(str(repo))

    assert plan.pythonpath == ("lib",)  # pytest.ini won, not pyproject's "wrong"
    assert plan.rootdir == "."


# --------------------------------------------------------------------------- #
# Install-target discovery (subdir / monorepo / requirements / groups)
# --------------------------------------------------------------------------- #


def test_subdir_project_dir(tmp_path):
    """feast: the installable project lives in a subdir (``sdk/python``)."""
    repo = _repo(tmp_path, "feast")
    _write(repo, "Makefile", "build:\n\techo hi\n")
    _write(
        repo,
        "sdk/python/setup.py",
        """
        from setuptools import setup, find_packages

        setup(
            name="feast",
            version="0.1.0",
            packages=find_packages(),
            install_requires=["pandas"],
        )
        """,
    )
    _write(
        repo,
        "sdk/python/pytest.ini",
        """
        [pytest]
        testpaths = tests
        """,
    )
    _write(repo, "sdk/python/feast/__init__.py", "")

    plan = resolve(str(repo))

    assert plan.project_dirs == ("sdk/python",)
    assert "editable:sdk/python" in plan.install_plan
    assert plan.rootdir == "sdk/python"
    assert plan.layout == "flat"


def test_monorepo_multiple_package_dirs(tmp_path):
    """vizro: a monorepo of several independently-packaged dirs."""
    repo = _repo(tmp_path, "vizro")
    _write(
        repo,
        "pyproject.toml",
        """
        [tool.ruff]
        line-length = 88
        """,
    )
    for pkg, mod in (("vizro-core", "vizro"), ("vizro-ai", "vizro_ai")):
        _write(
            repo,
            f"packages/{pkg}/pyproject.toml",
            f"""
            [build-system]
            requires = ["hatchling"]
            build-backend = "hatchling.build"

            [project]
            name = "{pkg}"
            version = "0.1.0"
            """,
        )
        _write(repo, f"packages/{pkg}/{mod}/__init__.py", "")

    plan = resolve(str(repo))

    assert plan.layout == "monorepo"
    assert len(plan.project_dirs) > 1
    assert "packages/vizro-core" in plan.project_dirs
    assert "packages/vizro-ai" in plan.project_dirs
    editable = [a for a in plan.install_plan if a.startswith("editable:")]
    assert len(editable) >= 2


def test_flat_layout_auto_discovery_hazard(tmp_path):
    """Spoolman: no ``[build-system]``/packages + sibling top-level packages."""
    repo = _repo(tmp_path, "spoolman")
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "spoolman"
        version = "0.1.0"
        dependencies = ["fastapi"]
        """,
    )
    _write(repo, "spoolman/__init__.py", "")
    _write(repo, "client/__init__.py", "")  # a SECOND top-level package -> ambiguous

    plan = resolve(str(repo))

    assert "flat_layout_ambiguous" in plan.flags
    assert plan.layout == "flat_ambiguous"
    assert plan.project_dirs == (".",)


def test_pep735_dependency_group_in_install_plan(tmp_path):
    """Spoolman: ``httpx`` is declared only in the PEP 735 ``[dependency-groups]``."""
    repo = _repo(tmp_path, "spoolman_groups")
    _write(
        repo,
        "pyproject.toml",
        """
        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [project]
        name = "spoolman"
        version = "0.1.0"
        dependencies = ["fastapi"]

        [dependency-groups]
        dev = ["httpx>=0.24"]
        """,
    )
    _write(repo, "spoolman/__init__.py", "")

    plan = resolve(str(repo))

    assert "group:dev" in plan.install_plan
    assert "flat_layout_ambiguous" not in plan.flags


def test_nested_requirements_files_discovered(tmp_path):
    """Archipelago: nested per-subdir requirements files (``WebHostLib/``, ``worlds/*``)."""
    repo = _repo(tmp_path, "archipelago")
    _write(
        repo,
        "setup.py",
        """
        from setuptools import setup

        setup(name="archipelago", version="0.1.0", py_modules=["Main"])
        """,
    )
    _write(repo, "Main.py", "x = 1\n")
    _write(repo, "requirements.txt", "certifi\n")
    _write(repo, "WebHostLib/requirements.txt", "flask\n")
    _write(repo, "worlds/alpha/requirements.txt", "pyyaml\n")

    plan = resolve(str(repo))

    assert "requirements:WebHostLib/requirements.txt" in plan.install_plan
    assert "requirements:worlds/alpha/requirements.txt" in plan.install_plan
    assert "editable:." in plan.install_plan


# --------------------------------------------------------------------------- #
# Interpreter policy (prefer requires-python + CI default, never the max)
# --------------------------------------------------------------------------- #


def test_interpreter_declared_floor_when_default_excluded(tmp_path):
    """``requires-python = '>=3.12'`` -> 3.12 (floor), confidence 'declared'."""
    repo = _repo(tmp_path, "req312")
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "req312"
        version = "0.1.0"
        requires-python = ">=3.12"
        """,
    )

    plan = resolve(str(repo))

    assert plan.interpreter == "3.12"
    assert plan.interpreter_confidence == "declared"


def test_interpreter_ci_default_when_no_requires_python(tmp_path):
    """No ``requires-python`` + CI matrix [3.10, 3.11] -> CI default (not max)."""
    repo = _repo(tmp_path, "ci_only")
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "ci_only"
        version = "0.1.0"
        """,
    )
    _write(
        repo,
        ".github/workflows/ci.yml",
        """
        name: CI
        on: [push]
        jobs:
          test:
            runs-on: ubuntu-latest
            strategy:
              matrix:
                python-version: ["3.10", "3.11"]
            steps:
              - uses: actions/checkout@v4
        """,
    )

    plan = resolve(str(repo))

    assert plan.interpreter == "3.10"  # most-common tie -> lowest, never the max
    assert plan.interpreter_confidence == "ci_default"


def test_interpreter_default_when_nothing_declared(tmp_path):
    """Nothing declared anywhere -> known-stable default 3.11, confidence 'default'."""
    repo = _repo(tmp_path, "bare")
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "bare"
        version = "0.1.0"
        """,
    )
    _write(repo, "bare/__init__.py", "")

    plan = resolve(str(repo))

    assert plan.interpreter == "3.11"
    assert plan.interpreter_confidence == "default"


def test_interpreter_crosscheck_ci_within_requires_python(tmp_path):
    """``requires-python='>=3.9'`` + CI [3.11,3.12] -> 3.11 (CI default inside the constraint)."""
    repo = _repo(tmp_path, "crosscheck")
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "crosscheck"
        version = "0.1.0"
        requires-python = ">=3.9"
        """,
    )
    _write(
        repo,
        ".github/workflows/ci.yml",
        """
        jobs:
          test:
            strategy:
              matrix:
                python-version: ["3.11", "3.12"]
        """,
    )

    plan = resolve(str(repo))

    assert plan.interpreter == "3.11"
    assert plan.interpreter_confidence == "declared"


def test_interpreter_undiscoverable_when_unsatisfiable(tmp_path):
    """A declared ``requires-python`` no supported minor satisfies -> undiscoverable."""
    repo = _repo(tmp_path, "unsat")
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "unsat"
        version = "0.1.0"
        requires-python = ">=3.99"
        """,
    )

    plan = resolve(str(repo))

    assert plan.interpreter_confidence == "undiscoverable"
    assert "interpreter_undiscoverable" in plan.flags


# --------------------------------------------------------------------------- #
# import-mode read from the chosen pytest config (not recomputed)
# --------------------------------------------------------------------------- #


def test_import_mode_read_from_addopts(tmp_path):
    """``addopts = '--import-mode=importlib'`` -> import_mode 'importlib'."""
    repo = _repo(tmp_path, "importmode")
    _write(
        repo,
        "pyproject.toml",
        """
        [build-system]
        requires = ["setuptools"]
        build-backend = "setuptools.build_meta"

        [project]
        name = "importmode"
        version = "0.1.0"

        [tool.pytest.ini_options]
        addopts = "--import-mode=importlib"
        """,
    )
    _write(repo, "importmode/__init__.py", "")

    plan = resolve(str(repo))

    assert plan.import_mode == "importlib"


def test_plan_is_frozen(tmp_path):
    """The returned plan is an immutable frozen dataclass."""
    import dataclasses

    repo = _repo(tmp_path, "frozen")
    _write(repo, "pyproject.toml", "[project]\nname = 'frozen'\nversion = '0.1.0'\n")

    plan = resolve(str(repo))

    assert dataclasses.is_dataclass(plan)
    try:
        plan.interpreter = "9.9"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover - only reached on a mutability regression
        raise AssertionError("TestEnvPlan must be frozen (immutable)")


# --------------------------------------------------------------------------- #
# Review-round fixes (Opus BLOCK): correctness bugs + uncovered branches
# --------------------------------------------------------------------------- #


def test_hard_requirements_files_are_installed_not_phantom_groups(tmp_path):
    """#1: a HARD dev/test requirements file (root ``requirements-test.txt`` or
    anything under ``tests/``) must be installed via a ``requirements:`` action —
    NOT emitted as a phantom PEP 735 ``group:`` (``--group test`` would error) and
    NOT silently dropped."""
    repo = _repo(tmp_path, "reqfiles")
    _write(repo, "pyproject.toml", "[project]\nname = 'reqfiles'\nversion = '0.1.0'\n")
    _write(repo, "reqfiles/__init__.py", "")
    _write(repo, "requirements-test.txt", "pytest\n")
    _write(repo, "tests/requirements.txt", "responses\n")

    plan = resolve(str(repo))

    # No phantom PEP 735 group actions manufactured from requirements-file roles.
    assert not any(action.startswith("group:") for action in plan.install_plan)
    # Both HARD files are installed (the tests/ one was previously dropped).
    assert "requirements:tests/requirements.txt" in plan.install_plan
    assert "requirements:requirements-test.txt" in plan.install_plan


def test_interpreter_ci_only_unsupported_is_undiscoverable(tmp_path):
    """#2: a CI matrix naming only an out-of-range future version (3.14) must not
    pass 3.14 through unflagged — it hands off as undiscoverable."""
    repo = _repo(tmp_path, "future_ci")
    _write(repo, "pyproject.toml", "[project]\nname = 'future_ci'\nversion = '0.1.0'\n")
    _write(
        repo,
        ".github/workflows/ci.yml",
        """
        jobs:
          test:
            strategy:
              matrix:
                python-version: ["3.14"]
        """,
    )

    plan = resolve(str(repo))

    assert plan.interpreter_confidence == "undiscoverable"
    assert "interpreter_undiscoverable" in plan.flags
    assert plan.interpreter in SUPPORTED_MINORS  # never the raw out-of-range value


def test_interpreter_tox_envlist_below_floor_is_undiscoverable(tmp_path):
    """#2: ``tox envlist = py38`` (below the supported floor) is not passed through
    as 3.8 — it hands off as undiscoverable."""
    repo = _repo(tmp_path, "old_tox")
    _write(repo, "pyproject.toml", "[project]\nname = 'old_tox'\nversion = '0.1.0'\n")
    _write(repo, "tox.ini", "[tox]\nenvlist = py38\n")

    plan = resolve(str(repo))

    assert plan.interpreter_confidence == "undiscoverable"
    assert "interpreter_undiscoverable" in plan.flags


def test_vendored_tree_is_not_a_monorepo(tmp_path):
    """#3: packages vendored under ``third_party/`` / ``vendor/`` must never be
    mistaken for a monorepo and editable-installed."""
    repo = _repo(tmp_path, "vendored")
    _write(repo, "README.md", "root, no manifest\n")
    _write(
        repo,
        "third_party/pkg_a/pyproject.toml",
        "[project]\nname = 'pkg_a'\nversion = '0.1.0'\n",
    )
    _write(repo, "third_party/pkg_a/pkg_a/__init__.py", "")
    _write(
        repo,
        "vendor/pkg_b/pyproject.toml",
        "[project]\nname = 'pkg_b'\nversion = '0.1.0'\n",
    )
    _write(repo, "vendor/pkg_b/pkg_b/__init__.py", "")

    plan = resolve(str(repo))

    assert plan.layout == "none"
    assert plan.project_dirs == ()
    assert not any(action.startswith("editable:") for action in plan.install_plan)


def test_setuptools_flat_layout_two_packages_flagged(tmp_path):
    """#5: setuptools flat-layout auto-discovery DOES raise on multiple top-level
    packages even WITH a ``[build-system]`` — the hazard must still be flagged."""
    repo = _repo(tmp_path, "setuptools_flat")
    _write(
        repo,
        "pyproject.toml",
        """
        [build-system]
        requires = ["setuptools"]
        build-backend = "setuptools.build_meta"

        [project]
        name = "setuptools_flat"
        version = "0.1.0"
        """,
    )
    _write(repo, "alpha/__init__.py", "")
    _write(repo, "beta/__init__.py", "")

    plan = resolve(str(repo))

    assert "flat_layout_ambiguous" in plan.flags
    assert plan.layout == "flat_ambiguous"


def test_non_setuptools_backend_two_packages_not_flagged(tmp_path):
    """#5: a NON-setuptools backend (hatchling) handles discovery its own way, so
    two top-level packages are not the setuptools hazard."""
    repo = _repo(tmp_path, "hatch_flat")
    _write(
        repo,
        "pyproject.toml",
        """
        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [project]
        name = "hatch_flat"
        version = "0.1.0"
        """,
    )
    _write(repo, "alpha/__init__.py", "")
    _write(repo, "beta/__init__.py", "")

    plan = resolve(str(repo))

    assert "flat_layout_ambiguous" not in plan.flags


def test_top_level_dir_without_init_is_not_a_package(tmp_path):
    """#5: a stray top-level dir with a ``.py`` but no ``__init__.py`` is not a
    package (matches default ``find_packages``) — one real package => not
    ambiguous."""
    repo = _repo(tmp_path, "stray")
    _write(repo, "pyproject.toml", "[project]\nname = 'stray'\nversion = '0.1.0'\n")
    _write(repo, "stray/__init__.py", "")
    _write(repo, "helpers/util.py", "x = 1\n")  # no __init__.py -> not a package

    plan = resolve(str(repo))

    assert "flat_layout_ambiguous" not in plan.flags


def test_ci_block_list_matrix_form(tmp_path):
    """Branch: the YAML block-list matrix form is parsed, not only the inline list."""
    repo = _repo(tmp_path, "blocklist")
    _write(repo, "pyproject.toml", "[project]\nname = 'blocklist'\nversion = '0.1.0'\n")
    _write(
        repo,
        ".github/workflows/ci.yml",
        """
        jobs:
          test:
            strategy:
              matrix:
                python-version:
                  - "3.11"
                  - "3.12"
        """,
    )

    plan = resolve(str(repo))

    assert plan.interpreter == "3.11"  # most-common tie -> lowest
    assert plan.interpreter_confidence == "ci_default"


def test_poetry_tilde_constraint(tmp_path):
    """Branch: poetry ``~`` python constraint normalizes and drives the floor."""
    repo = _repo(tmp_path, "poetry")
    _write(
        repo,
        "pyproject.toml",
        """
        [tool.poetry]
        name = "poetry"
        version = "0.1.0"

        [tool.poetry.dependencies]
        python = "~3.10"
        """,
    )

    plan = resolve(str(repo))

    assert plan.interpreter == "3.10"  # ~3.10 => >=3.10,<3.11
    assert plan.interpreter_confidence == "declared"


def test_empty_repo_does_not_crash(tmp_path):
    """Branch: a repo with no manifest at all resolves without crashing."""
    repo = _repo(tmp_path, "empty")
    _write(repo, "README.md", "nothing to see\n")

    plan = resolve(str(repo))

    assert plan.layout == "none"
    assert plan.project_dirs == ()
    assert plan.interpreter == "3.11"
    assert plan.interpreter_confidence == "default"


def test_malformed_pyproject_does_not_crash(tmp_path):
    """Branch: a malformed pyproject.toml is tolerated (treated as no manifest)."""
    repo = _repo(tmp_path, "broken")
    _write(repo, "pyproject.toml", "[project\nname = \"broken\"\n=oops[[[\n")

    plan = resolve(str(repo))

    assert isinstance(plan, TestEnvPlan)
    assert plan.layout == "none"
    assert plan.project_dirs == ()


def test_ci_inline_trailing_comment_not_a_version(tmp_path):
    """LOW: a trailing YAML comment must not inject a spurious minor."""
    repo = _repo(tmp_path, "comment_ci")
    _write(repo, "pyproject.toml", "[project]\nname = 'comment_ci'\nversion = '0.1.0'\n")
    _write(
        repo,
        ".github/workflows/ci.yml",
        """
        jobs:
          test:
            strategy:
              matrix:
                python-version: ["3.13"]  # was 3.9
        """,
    )

    plan = resolve(str(repo))

    # Without stripping the comment the regex also captures 3.9 and (ties ->
    # lowest) wrongly picks it; 3.13 is the only real matrix value.
    assert plan.interpreter == "3.13"
    assert plan.interpreter_confidence == "ci_default"


# --------------------------------------------------------------------------- #
# cwd + unambiguous authoritative env (canonical collect invocation)
# --------------------------------------------------------------------------- #


def test_testenvplan_carries_cwd_and_unambiguous_env(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "tox.ini").write_text(
        "[testenv]\nsetenv =\n    DJANGO_SETTINGS_MODULE=app.settings\n"
    )
    from graph.python.invocation_resolver import resolve
    plan = resolve(str(tmp_path))
    assert plan.cwd == plan.rootdir            # default cwd = rootdir
    assert ("DJANGO_SETTINGS_MODULE", "app.settings") in plan.env


def test_env_and_pythonpath_scoped_to_config_dir(tmp_path):
    # feast-style: the authoritative config lives in a subdir, not the repo root.
    sdk = tmp_path / "sdk" / "python"
    sdk.mkdir(parents=True)
    (sdk / "pyproject.toml").write_text("[build-system]\nrequires=['setuptools']\n")
    (sdk / "tox.ini").write_text(
        "[pytest]\n"
        "[testenv]\nsetenv =\n"
        "    DJANGO_SETTINGS_MODULE=app.settings\n"
        "    PYTHONPATH=src\n"
    )
    from graph.python.invocation_resolver import resolve
    plan = resolve(str(tmp_path))
    assert plan.rootdir == "sdk/python"
    assert ("DJANGO_SETTINGS_MODULE", "app.settings") in plan.env  # found in the subdir, not root
    assert "src" in plan.pythonpath                                 # tox setenv PYTHONPATH sourced
