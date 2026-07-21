import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.env_state import env_state_enabled, render_declared


def test_flag_defaults_on_and_off_only_for_zero(monkeypatch):
    monkeypatch.delenv("REACT_ENV_STATE", raising=False)
    assert env_state_enabled() is True
    monkeypatch.setenv("REACT_ENV_STATE", "0")
    assert env_state_enabled() is False
    monkeypatch.setenv("REACT_ENV_STATE", "1")
    assert env_state_enabled() is True


def test_render_declared_reads_requirements_and_pyproject_extras(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask>=3\npsycopg2-binary\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["click"]\n'
        '[project.optional-dependencies]\ntest = ["pytest", "pytest-cov"]\n')
    out = render_declared(tmp_path)
    assert "DECLARED (from the repo, static)" in out
    assert "requirements.txt:" in out and "psycopg2-binary" in out
    assert "[project.dependencies]" in out and "click" in out
    assert "optional-dependencies].test" in out and "pytest-cov" in out


def test_render_declared_caps_oversized_file(tmp_path):
    (tmp_path / "requirements.txt").write_text("x==1\n" * 5000)
    out = render_declared(tmp_path, cap_bytes=200)
    assert "… (truncated)" in out
    assert len(out) < 1000


def test_render_declared_empty_when_no_manifests(tmp_path):
    assert render_declared(tmp_path) == ""
    assert render_declared(None) == ""
    assert render_declared(tmp_path / "does-not-exist") == ""
