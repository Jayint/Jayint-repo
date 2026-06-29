import sys, inspect
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate.orchestrator import run_v3


def test_run_v3_accepts_binding_install_kwargs():
    params = inspect.signature(run_v3).parameters
    assert "enable_binding_install" in params
    assert "reset_to_base" in params
    assert "run_install_script" in params


def _read(rel):
    return (_ROOT / rel).read_text()


def test_agent_forwards_binding_install_to_run_v3():
    src = _read("agent.py")
    assert "enable_binding_install=" in src
    assert "reset_to_base=self.sandbox.reset_to_base" in src
    assert "run_install_script=self.sandbox.run_install_script" in src
    assert "self.enable_binding_install" in src


def test_l2_smoke_exposes_binding_install_flag():
    src = _read("scripts/l2_repair_loop_smoke.py")
    assert "--enable-binding-install" in src
    assert "reset_to_base=sandbox.reset_to_base" in src
    assert "run_install_script=sandbox.run_install_script" in src
