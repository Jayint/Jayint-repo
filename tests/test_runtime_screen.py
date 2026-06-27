from src.envstate.runtime_base import screen_runtime_pin


def test_declared_low_floor_would_change(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.9"\n')
    r = screen_runtime_pin(str(tmp_path))
    assert r["would_pin_to"] == "3.9"
    assert r["base_would_change"] is True


def test_undeclared_no_change(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    r = screen_runtime_pin(str(tmp_path))
    assert r["would_pin_to"] == "3.11"
    assert r["requires_python"] is None
    assert r["base_would_change"] is False


def test_declared_equals_default_no_change(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n')
    r = screen_runtime_pin(str(tmp_path))
    assert r["would_pin_to"] == "3.11"
    assert r["base_would_change"] is False
