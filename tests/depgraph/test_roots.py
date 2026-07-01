"""Root selection — manifest-first, scan-gap-filled, non-distribution filtered."""

from __future__ import annotations

import textwrap
from pathlib import Path

from python_deps.depgraph.roots import select_roots
from python_deps.depgraph.scan import scan_to_nodes
from python_deps.depgraph.target_env import TargetEnv


def _write(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["flask", "requests"]
        """,
    )
    # A py2 shim, a stdlib import, a real external import, and a declared dep.
    _write(
        repo,
        "proj/app.py",
        """
        import os
        import StringIO
        import requests
        import boto3
        """,
    )
    return repo


def test_declared_dependencies_become_roots_with_none_import_id(tmp_path):
    repo = _fixture_repo(tmp_path)
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)

    declared = {dist for imp, dist in roots if imp is None}
    assert "flask" in declared
    assert "requests" in declared


def test_py2_shim_is_filtered_out(tmp_path):
    repo = _fixture_repo(tmp_path)
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)

    dists = {dist for _imp, dist in roots}
    assert "StringIO" not in dists


def test_scanned_import_gap_fills_only_uncovered(tmp_path):
    repo = _fixture_repo(tmp_path)
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)

    # boto3 is imported but not declared -> gap-filled with its import_id.
    gap = {dist: imp for imp, dist in roots}
    assert "boto3" in gap
    assert gap["boto3"] == "import:boto3"


def test_declared_import_not_duplicated(tmp_path):
    repo = _fixture_repo(tmp_path)
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)

    # requests is BOTH declared and imported; it must appear exactly once,
    # via the declared (import_id=None) entry, not a second scanned entry.
    requests_entries = [(imp, dist) for imp, dist in roots if dist == "requests"]
    assert requests_entries == [(None, "requests")]


def test_stdlib_import_never_a_root(tmp_path):
    repo = _fixture_repo(tmp_path)
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)

    dists = {dist for _imp, dist in roots}
    assert "os" not in dists


def test_no_duplicate_distributions(tmp_path):
    repo = _fixture_repo(tmp_path)
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)

    dists = [dist for _imp, dist in roots]
    assert len(dists) == len(set(dists))


def test_typing_only_stub_filtered(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(repo, "pyproject.toml", """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = []
        """)
    _write(repo, "proj/app.py", "import _typeshed\n")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)
    assert "_typeshed" not in {dist for _imp, dist in roots}


def test_junk_and_dunder_filtered(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(repo, "pyproject.toml", """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = []
        """)
    _write(repo, "proj/app.py", "import __main__\nimport _private\n")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)
    dists = {dist for _imp, dist in roots}
    assert "__main__" not in dists
    assert "_private" not in dists


def test_declared_py2_shim_filtered(tmp_path):
    # The manifest path (declared deps) must also drop py2-shim non-distributions.
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(repo, "pyproject.toml", """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["urllib2", "requests"]
        """)
    _write(repo, "proj/app.py", "import requests\n")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)
    dists = {dist for _imp, dist in roots}
    assert "urllib2" not in dists
    assert "requests" in dists


def test_declared_version_specifier_is_propagated(tmp_path):
    # A declared version pin must reach the resolver so a conflict is visible
    # (spec's "project pinning numpy<2 plus a dep requiring numpy>=2").
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(repo, "pyproject.toml", """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["urllib3<1.21", "requests==2.32.3"]
        """)
    _write(repo, "proj/app.py", "import requests\n")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)
    dists = {dist for _imp, dist in roots}
    assert "urllib3<1.21" in dists
    assert "requests==2.32.3" in dists


def test_unsafe_specifier_falls_back_to_bare_name(tmp_path):
    # A specifier carrying a marker / odd chars is dropped (bare name kept) rather
    # than risking injection into the resolver's temp pyproject.
    from python_deps.depgraph.roots import _manifest_root_token
    from python_deps.models import PythonRequirement

    assert _manifest_root_token(PythonRequirement("flask", ">=2.0")) == "flask>=2.0"
    assert (
        _manifest_root_token(PythonRequirement("flask", ">=2.0; python_version<'3.9'"))
        == "flask"
    )
    assert _manifest_root_token(PythonRequirement("flask", "")) == "flask"


def test_manifest_scan_dedup_via_normalization(tmp_path):
    # Declared `Flask` and imported `flask` must dedup via normalize_package_name.
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(repo, "pyproject.toml", """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["Flask"]
        """)
    _write(repo, "proj/app.py", "import flask\n")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)
    flask_entries = [(imp, dist) for imp, dist in roots if dist.lower() == "flask"]
    assert flask_entries == [(None, "Flask")]


# --------------------------------------------------------------------------- #
# Task 8 — targeted extras: needed_extras gating + per-dep extras preserved.
# --------------------------------------------------------------------------- #
def _fixture_repo_with_optional_groups(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["requests"]

        [project.optional-dependencies]
        test = ["pytest"]
        docs = ["sphinx"]
        """,
    )
    _write(repo, "proj/app.py", "import requests\n")
    return repo


def test_only_needed_extra_group_becomes_a_root(tmp_path):
    repo = _fixture_repo_with_optional_groups(tmp_path)
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph, needed_extras=frozenset({"test"}))

    names = {tok for _imp, tok in roots}
    assert any(t.startswith("requests") for t in names)   # runtime always
    assert any(t.startswith("pytest") for t in names)     # needed extra
    assert not any(t.startswith("sphinx") for t in names)  # unneeded group excluded


def test_no_needed_extras_default_excludes_all_optional_groups(tmp_path):
    repo = _fixture_repo_with_optional_groups(tmp_path)
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)  # default needed_extras=frozenset()

    names = {tok for _imp, tok in roots}
    assert any(t.startswith("requests") for t in names)
    assert not any(t.startswith("pytest") for t in names)
    assert not any(t.startswith("sphinx") for t in names)


def test_per_dep_extra_specifier_is_preserved(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["uvicorn[standard]>=0.20"]
        """,
    )
    _write(repo, "proj/app.py", "import uvicorn\n")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)

    names = {tok for _imp, tok in roots}
    assert any("uvicorn[standard]" in t for t in names)   # extra NOT stripped
    assert not any(t == "uvicorn" for t in names)          # not silently bare


# --------------------------------------------------------------------------- #
# Task 8 review fix — target_env-conditioned environment-marker filtering.
#
# CRITICAL: "no silent shrink" — a marker'd dep must be dropped ONLY when a
# target_env is present, the marker has no `extra` reference, and it evaluates
# False for that target. Every other case (no target_env, no marker, an
# extra-gated marker, a True evaluation) must KEEP the dep.
# --------------------------------------------------------------------------- #
_LINUX_TARGET_ENV = TargetEnv(
    python_full="3.11.0",
    python_version="3.11",
    platform_machine="x86_64",
    sys_platform="linux",
    os_name="posix",
    platform_system="Linux",
    python_platform_tag="x86_64-manylinux_2_28",
)


def _fixture_repo_with_marker_dep(tmp_path: Path, marker: str) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(
        repo,
        "pyproject.toml",
        f"""
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["foo ; {marker}"]
        """,
    )
    _write(repo, "proj/app.py", "")
    return repo


def test_env_marker_false_dep_skipped_for_target(tmp_path):
    repo = _fixture_repo_with_marker_dep(tmp_path, "sys_platform == 'win32'")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph, target_env=_LINUX_TARGET_ENV)

    dists = {dist for _imp, dist in roots}
    assert "foo" not in dists


def test_env_marker_true_dep_kept_for_target(tmp_path):
    repo = _fixture_repo_with_marker_dep(tmp_path, "sys_platform == 'linux'")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph, target_env=_LINUX_TARGET_ENV)

    dists = {dist for _imp, dist in roots}
    assert "foo" in dists


def test_extra_marker_dep_not_dropped_by_env_filter(tmp_path):
    # An extra-gated marker must NEVER be judged by the env filter — that is
    # needed_extras' job. Even with a target_env given (and no needed_extras
    # requested), the env filter itself must not be the reason it's dropped;
    # to isolate that, request the "x" extra so the ONLY question left is
    # whether the env filter wrongly re-excludes it.
    repo = _fixture_repo_with_marker_dep(tmp_path, "extra == 'x'")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(
        str(repo),
        graph,
        needed_extras=frozenset({"x"}),
        target_env=_LINUX_TARGET_ENV,
    )

    dists = {dist for _imp, dist in roots}
    assert "foo" in dists


def test_no_target_env_keeps_marker_deps(tmp_path):
    repo = _fixture_repo_with_marker_dep(tmp_path, "sys_platform == 'win32'")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph)  # target_env=None (default)

    dists = {dist for _imp, dist in roots}
    assert "foo" in dists


def test_unmarked_dep_always_kept(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["foo"]
        """,
    )
    _write(repo, "proj/app.py", "")
    graph = scan_to_nodes(str(repo))
    roots = select_roots(str(repo), graph, target_env=_LINUX_TARGET_ENV)

    dists = {dist for _imp, dist in roots}
    assert "foo" in dists
