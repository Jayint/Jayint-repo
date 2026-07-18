"""container.commit() under concurrency hits a transient containerd content-store race
(500: 'failed to export layer: CreateDiff mount callback failed ... no such file'). This killed
darts+checkmk at c3 and azure+darts at c2. A short retry clears it (the race is non-deterministic).
"""
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from types import SimpleNamespace
import docker
import src.orchestrate.loop.sandbox as sandbox_mod
from src.orchestrate.loop.sandbox import _commit_with_retry

_RACE_MSG = "500 Server Error: failed to export layer: CreateDiff: mount callback failed ... no such file"


class _FlakyContainer:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0
    def commit(self, *a, **k):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise docker.errors.APIError(_RACE_MSG)
        return SimpleNamespace(id="sha256:baseline123")


def test_commit_retries_transient_containerd_race(monkeypatch):
    monkeypatch.setattr(sandbox_mod.time, "sleep", lambda *a: None)
    c = _FlakyContainer(fail_times=2)
    img = _commit_with_retry(c, attempts=5)
    assert img.id == "sha256:baseline123" and c.calls == 3   # 2 failures + 1 success

def test_commit_reraises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr(sandbox_mod.time, "sleep", lambda *a: None)
    c = _FlakyContainer(fail_times=99)
    try:
        _commit_with_retry(c, attempts=3)
        assert False, "should have raised"
    except docker.errors.APIError:
        assert c.calls == 3

def test_commit_returns_immediately_on_success(monkeypatch):
    monkeypatch.setattr(sandbox_mod.time, "sleep", lambda *a: None)
    c = _FlakyContainer(fail_times=0)
    img = _commit_with_retry(c)
    assert img.id == "sha256:baseline123" and c.calls == 1
