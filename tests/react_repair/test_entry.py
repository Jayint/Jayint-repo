import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.entry import docker_adapters
from src.react_repair.log import ReactLog


class _FakeSandbox:
    def __init__(self, rc, out): self._rc, self._out = rc, out
    def reset_to_base(self): pass
    def run_install_script(self, script):
        from src.sandbox import InstallResult
        return InstallResult(rc=self._rc, failing_command=None if self._rc == 0 else "pip install x",
                             lineno=None, stderr=self._out)
    def exec_readonly(self, cmd): return (0, self._out)


def test_run_script_adapter_maps_installresult():
    _, run_script, _, _, _ = docker_adapters(_FakeSandbox(1, "boom"))
    r = run_script("pip install x")
    assert r.ok is False and r.failing_command == "pip install x" and "boom" in r.output

def test_run_tests_adapter_applies_80pct_verdict():
    _, _, _, _, run_tests = docker_adapters(_FakeSandbox(0, "9 passed, 1 failed in 1s"))
    assert run_tests().ok is True                       # 0.9 >= 0.8


class _CapSandbox:
    """Captures the command run_tests passes to exec_readonly, returns a scripted (rc, out)."""
    def __init__(self, rc, out): self._rc, self._out = rc, out
    def reset_to_base(self): pass
    def exec_readonly(self, cmd):
        self.last_cmd = cmd
        return (self._rc, self._out)


def test_run_tests_bounds_pytest_with_timeout():
    sb = _CapSandbox(0, "5 passed in 0.1s")
    _, _, _, _, run_tests = docker_adapters(sb)
    r = run_tests()
    assert "timeout" in sb.last_cmd and "pytest" in sb.last_cmd     # bounded
    assert "command -v timeout" in sb.last_cmd                      # graceful fallback if absent
    assert r.ok is True                                            # verdict still applied to output


def test_run_tests_timeout_kill_is_not_ok():
    # coreutils `timeout` exits 124 on kill; a killed run has no real passes -> not ok.
    sb = _CapSandbox(124, "")
    _, _, _, _, run_tests = docker_adapters(sb)
    assert run_tests().ok is False


def test_run_tests_threshold_is_configurable():
    _, _, _, _, rt_low = docker_adapters(_CapSandbox(0, "7 passed, 3 failed in 1s"), test_threshold=0.6)
    assert rt_low().ok is True                    # 0.7 >= 0.6
    _, _, _, _, rt_default = docker_adapters(_CapSandbox(0, "7 passed, 3 failed in 1s"))
    assert rt_default().ok is False               # 0.7 < 0.9 default


def test_run_react_arm_strips_and_forwards_seed(monkeypatch):
    import src.react_repair.entry as entry_mod
    captured = {}
    def fake_run_react(graph, **kw):
        captured.update(kw)
        return ("DONE", kw.get("_initial_script"), graph)
    monkeypatch.setattr(entry_mod, "run_react", fake_run_react)
    seed = ("#!/usr/bin/env bash\n#\n"
            "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.\n#\n"
            "set -Eeuo pipefail\npip install app\n")
    entry_mod.run_react_arm(object(), sandbox=_FakeSandbox(0, ""), client=object(),
                            model="m", initial_script=seed, log=ReactLog(silent=True))
    fwd = captured["_initial_script"]
    assert fwd is not None
    assert "DO NOT EDIT" not in fwd and "graph" not in fwd.lower()   # header stripped
    assert "pip install app" in fwd and "set -Eeuo pipefail" in fwd  # body preserved

def test_run_react_arm_injects_no_llm_compressor(monkeypatch):
    # The grouped history view IS the compaction; the old Tier-2 LLM compressor's output is never
    # rendered, so injecting it only burns wasted LLM calls. Lock that the arm builds no compressor.
    import src.react_repair.entry as entry_mod
    captured = {}
    class FakeHistory:
        def __init__(self, *a, compressor=None, **k):
            captured["compressor"] = compressor
    monkeypatch.setattr(entry_mod, "History", FakeHistory)
    monkeypatch.setattr(entry_mod, "run_react", lambda graph, **kw: ("DONE", "s", graph))
    entry_mod.run_react_arm(object(), sandbox=_FakeSandbox(0, ""), client=object(),
                            model="m", log=ReactLog(silent=True))
    assert captured["compressor"] is None

def test_run_react_arm_without_seed_forwards_none(monkeypatch):
    import src.react_repair.entry as entry_mod
    captured = {}
    monkeypatch.setattr(entry_mod, "run_react",
                        lambda graph, **kw: captured.update(kw) or ("DONE", None, graph))
    entry_mod.run_react_arm(object(), sandbox=_FakeSandbox(0, ""), client=object(),
                            model="m", log=ReactLog(silent=True))
    assert captured["_initial_script"] is None       # unchanged default behavior
