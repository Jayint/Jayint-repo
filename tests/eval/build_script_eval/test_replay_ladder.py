import pytest
from src.eval.build_script_eval.replay import _disconnect_network_cmd, run_replay_ladder
from src.eval.language_package_eval.coverage import _docker_available


def test_disconnect_network_cmd_targets_bridge_and_container():
    cmd = _disconnect_network_cmd("probe-abc123")
    assert cmd[:3] == ["docker", "network", "disconnect"]
    assert "probe-abc123" in cmd


@pytest.mark.skipif(not _docker_available(), reason="docker unavailable")
def test_ladder_on_trivial_pure_python_repo(tmp_path):
    # a repo that installs cleanly, imports, and has one passing test
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n"
        "[project]\nname='triv'\nversion='0.0.0'\n"
    )
    (tmp_path / "triv").mkdir()
    (tmp_path / "triv" / "__init__.py").write_text("x = 1\n")
    (tmp_path / "test_triv.py").write_text("from triv import x\n\ndef test_x():\n    assert x == 1\n")
    setup_sh = "#!/usr/bin/env bash\nset -e\npip install -e .\n"
    res = run_replay_ladder(str(tmp_path), "python:3.11-slim", setup_sh, "triv", test_timeout=180)
    assert res.install_ok is True
    assert res.env_works is True
    assert res.tests_ran is True
    assert res.tests_passed is True
    assert res.highest_rung == "tests_passed"
