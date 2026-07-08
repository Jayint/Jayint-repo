import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.entry import docker_adapters


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
