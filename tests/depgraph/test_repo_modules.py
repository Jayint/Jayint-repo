"""Tests for repo_modules — the sys.path-accurate repo module walk (pure, no Docker)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.repo_modules import repo_modules, top_level_names


def _write(root: Path, rel: str, body: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _dotted(root: Path) -> dict[str, str]:
    """path -> dotted, for compact assertions."""
    return {m.path: m.dotted for m in repo_modules(str(root))}


def test_src_layout(tmp_path):
    _write(tmp_path, "src/flask/__init__.py")
    _write(tmp_path, "src/flask/app.py")
    assert _dotted(tmp_path)["src/flask/app.py"] == "flask.app"
    assert top_level_names(str(tmp_path)) == {"flask"}


def test_pep420_namespace_package_keeps_climbing(tmp_path):
    """flask/sansio: a real subpackage with NO __init__.py. It is NOT a sys.path
    root — its parent is a package — so the walk must climb THROUGH it."""
    _write(tmp_path, "src/flask/__init__.py")
    _write(tmp_path, "src/flask/sansio/app.py")          # no sansio/__init__.py
    assert _dotted(tmp_path)["src/flask/sansio/app.py"] == "flask.sansio.app"
    assert top_level_names(str(tmp_path)) == {"flask"}   # NOT {"flask", "app"}


def test_shadowing_submodule_is_not_a_top_level(tmp_path):
    """jupyterhub/traitlets.py is `jupyterhub.traitlets` — bare `import traitlets`
    resolves to PyPI, not to this file."""
    _write(tmp_path, "jupyterhub/__init__.py")
    _write(tmp_path, "jupyterhub/traitlets.py")
    assert top_level_names(str(tmp_path)) == {"jupyterhub"}


def test_non_standard_source_root(tmp_path):
    """netbox: source root is `netbox/`, which has no __init__.py."""
    _write(tmp_path, "netbox/extras/__init__.py")
    _write(tmp_path, "netbox/extras/models.py")
    _write(tmp_path, "netbox/utilities/__init__.py")
    _write(tmp_path, "netbox/utilities/jinja2.py")
    tops = top_level_names(str(tmp_path))
    assert "extras" in tops        # bare-importable; must stay LOCAL
    assert "utilities" in tops
    assert "jinja2" not in tops    # utilities.jinja2 — must be EXTERNAL


def test_test_fixture_package_is_bare_importable(tmp_path):
    """flask's tests/blueprintapp: tests/ has no __init__.py, so it IS a root."""
    _write(tmp_path, "tests/blueprintapp/__init__.py")
    assert top_level_names(str(tmp_path)) == {"blueprintapp"}


def test_flat_repo(tmp_path):
    _write(tmp_path, "foo.py")
    assert _dotted(tmp_path)["foo.py"] == "foo"
    assert top_level_names(str(tmp_path)) == {"foo"}


def test_never_climbs_above_repo_root(tmp_path):
    """A repo whose ROOT has __init__.py must not produce names from outside it."""
    _write(tmp_path, "__init__.py")
    _write(tmp_path, "mod.py")
    assert _dotted(tmp_path)["mod.py"] == "mod"
    assert top_level_names(str(tmp_path)) == {"mod"}


def test_skips_pruned_dirs(tmp_path):
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, ".git/hooks/thing.py")
    _write(tmp_path, "build/generated.py")
    _write(tmp_path, "__pycache__/cached.py")
    assert top_level_names(str(tmp_path)) == {"pkg"}


def test_typer_tutorial_leaf_package_under_non_package_parent(tmp_path):
    """typer docs_src: tutorial001/ HAS __init__.py, its parent does NOT.
    So items.py is `tutorial001.items` and `items` is NOT a top-level.
    This is the case the diagnosis router must treat as a STEM COLLISION,
    never as a plain external (see Task 4)."""
    _write(tmp_path, "docs_src/subcommands/tutorial001/__init__.py")
    _write(tmp_path, "docs_src/subcommands/tutorial001/items.py")
    _write(tmp_path, "docs_src/subcommands/tutorial001/main.py", "import items\n")
    mods = _dotted(tmp_path)
    assert mods["docs_src/subcommands/tutorial001/items.py"] == "tutorial001.items"
    assert "items" not in top_level_names(str(tmp_path))
