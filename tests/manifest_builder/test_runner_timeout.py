import pathlib
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import runner


def test_default_run_timeout_bytes_stdout_none_stderr(monkeypatch):
    # Repro of the nexent/feast ERROR: on a heavy repo the agent hits the 60-min timeout and
    # subprocess returns partial stdout as BYTES while stderr is None. The old handler did
    # `(e.stdout or "") + (e.stderr or "")` == bytes + "" -> TypeError: can't concat str to bytes,
    # which crashed build_one into ERROR instead of a graceful rc-124 soft-fail.
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1,
                                        output=b"partial \xff bytes out", stderr=None)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    rc, out = runner._default_run(["claude"], timeout=1)
    assert rc == 124
    assert isinstance(out, str)
    assert "partial" in out            # partial output preserved (undecodable byte replaced)
    assert "timed out after 1s" in out


def test_default_run_timeout_str_stdout(monkeypatch):
    # text=True path where the captured output is already str, stderr present.
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=2, output="strout", stderr="strerr")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    rc, out = runner._default_run(["claude"], timeout=2)
    assert rc == 124
    assert "strout" in out and "strerr" in out and "timed out after 2s" in out


def test_default_run_timeout_both_none(monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=3, output=None, stderr=None)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    rc, out = runner._default_run(["claude"], timeout=3)
    assert rc == 124 and isinstance(out, str) and "timed out after 3s" in out
