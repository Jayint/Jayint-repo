# tests/test_resolve_project_name.py
"""Regression tests for DockerAgent._resolve_project_name (Bug 1: project-in-closure).

When a repo's pyproject.toml has no [project]/[tool.poetry] table and there is no
setup.cfg, _resolve_project_name previously returned None. With project_name=None,
build_pin_instructions cannot exclude the project's own package, so a non-existent
spec like proxy_pool==1.0.0 leaks into the pinned closure and breaks `pip install -r`.

The fix adds a repo-URL-basename fallback so the project package is still excluded.
"""
from src.envstate.synthesis import build_pin_instructions
from src.envstate.world_model import Fact
from agent import DockerAgent


def _resolve(workplace, repo_url):
    a = DockerAgent.__new__(DockerAgent)
    a.workplace = workplace
    a.repo_url = repo_url
    return DockerAgent._resolve_project_name(a)


def test_url_basename_fallback_when_no_project_table(tmp_path):
    # Empty workplace (no pyproject/setup.cfg) -> fall back to URL basename.
    name = _resolve(str(tmp_path), "https://github.com/jhao104/proxy_pool")
    assert name == "proxy_pool"


def test_url_basename_strips_git_suffix(tmp_path):
    name = _resolve(str(tmp_path), "https://github.com/foo/bar-baz.git")
    assert name == "bar-baz"


def test_url_basename_strips_trailing_slash(tmp_path):
    name = _resolve(str(tmp_path), "https://github.com/foo/widget/")
    assert name == "widget"


def test_pyproject_project_table_takes_precedence_over_url(tmp_path):
    # A real [project] name must win over the URL-basename fallback.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "actual-name"\nversion = "1.0.0"\n'
    )
    name = _resolve(str(tmp_path), "https://github.com/foo/url-basename")
    assert name == "actual-name"


def test_empty_url_and_workplace_returns_none(tmp_path):
    assert _resolve("", "") is None
    assert _resolve(str(tmp_path), "") is None


def test_resolved_basename_excludes_project_from_pin(tmp_path):
    # End-to-end: the resolved basename feeds build_pin_instructions and excludes the
    # project's own (non-existent) package while keeping real deps. Normalization maps
    # proxy_pool -> proxy-pool, so the underscore form is excluded too.
    project_name = _resolve(str(tmp_path), "https://github.com/jhao104/proxy_pool")
    cmds = build_pin_instructions(
        (Fact(name="proxy_pool", detail="1.0.0"), Fact(name="requests", detail="2.31.0")),
        project_name=project_name,
    )
    assert cmds, "expected a pin layer"
    assert "requests==2.31.0" in cmds[0]
    assert "proxy_pool==" not in cmds[0]
    assert "proxy-pool==" not in cmds[0]
