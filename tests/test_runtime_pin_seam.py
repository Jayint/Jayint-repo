"""_apply_runtime_pin returns the RuntimeBaseDecision (or None when off/unusable).
Extracted from DockerAgent._run so it is unit-testable without an OpenAI client
or Docker."""
import src.envstate.runtime_base as rb
from agent import _apply_runtime_pin


def test_disabled_returns_none(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10,<3.13"\n')
    assert _apply_runtime_pin(False, str(tmp_path), "python:3.11-slim") is None


def test_enabled_pins_to_floor(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10,<3.13"\n')
    d = _apply_runtime_pin(True, str(tmp_path), "python:3.11-slim")
    assert d is not None
    assert d.base_image == "python:3.10-slim"
    assert d.minor == "3.10"


def test_enabled_undeclared_leaves_base(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    d = _apply_runtime_pin(True, str(tmp_path), "python:3.11-slim")
    assert d is not None
    assert d.base_image == "python:3.11-slim"   # unchanged
    assert d.minor == "3.11"


def test_enabled_missing_workplace_is_none():
    assert _apply_runtime_pin(True, "/nonexistent/repo", "python:3.11-slim") is None


def test_enabled_resolve_raises_returns_none(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10"\n')

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(rb, "resolve_runtime_base", _boom)
    assert _apply_runtime_pin(True, str(tmp_path), "python:3.11-slim") is None


def test_seam_assigns_self_base_image_and_stores_decision():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agent.py").read_text()
    # the seam must persist the pinned base for the scratch build at line ~1020 ...
    assert "self.base_image = base_image" in src
    # ... and store the decision so _build_run_summary can emit the metric.
    assert "self._runtime_pin_decision = _apply_runtime_pin(" in src
