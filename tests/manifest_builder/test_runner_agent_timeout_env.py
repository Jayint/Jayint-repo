import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.runner import ClaudeRunner


def _capture_runner(**kw):
    captured = {}

    def fake_run(argv, timeout=None, cwd=None):
        captured["timeout"] = timeout
        return 0, "ok"

    return ClaudeRunner(run=fake_run, **kw), captured


def test_agent_timeout_defaults_to_3600(monkeypatch, tmp_path):
    monkeypatch.delenv("MANIFEST_AGENT_TIMEOUT", raising=False)
    r, cap = _capture_runner()
    r.run(cwd=str(tmp_path), prompt="p", autonomous=True)
    assert cap["timeout"] == 3600


def test_agent_timeout_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MANIFEST_AGENT_TIMEOUT", "7200")
    r, cap = _capture_runner()
    r.run(cwd=str(tmp_path), prompt="p", autonomous=True)
    assert cap["timeout"] == 7200


def test_agent_timeout_explicit_arg_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MANIFEST_AGENT_TIMEOUT", "7200")
    r, cap = _capture_runner(timeout=1234)
    r.run(cwd=str(tmp_path), prompt="p", autonomous=True)
    assert cap["timeout"] == 1234
