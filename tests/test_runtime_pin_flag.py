# tests/test_runtime_pin_flag.py
from __future__ import annotations
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_docker_agent_has_enable_runtime_pin_param():
    src = (_ROOT / "agent.py").read_text()
    assert "enable_runtime_pin=False" in src
    assert "self.enable_runtime_pin: bool = bool(enable_runtime_pin)" in src


def test_runtime_pin_is_independent_no_implications():
    # must NOT imply dep_graph/v1 — measured as an orthogonal toggle on any arm
    src = (_ROOT / "agent.py").read_text()
    assert "or self.enable_runtime_pin" not in src


def test_argparse_exposes_enable_runtime_pin():
    src = (_ROOT / "agent.py").read_text()
    assert '"--enable-runtime-pin"' in src
    assert "enable_runtime_pin=args.enable_runtime_pin" in src


def test_adapter_reads_env():
    src = (_ROOT / "multi_docker_eval_adapter.py").read_text()
    assert "DOCKERAGENT_ENABLE_RUNTIME_PIN" in src
    assert "enable_runtime_pin=_enable_runtime_pin" in src


def test_rat_benchmark_sets_env_and_arm():
    src = (_ROOT / "run_rat_benchmark.py").read_text()
    assert "DOCKERAGENT_ENABLE_RUNTIME_PIN" in src
    assert '"v3"' in src  # renamed v1gsps→v3 in Phase 1 Task 4


def test_repo2run_has_v3_preset_and_forward():
    src = (_ROOT / "run_repo2run_benchmark.py").read_text()
    assert '"v3"' in src
    assert '"enable_runtime_pin": True' in src
    assert "--enable-runtime-pin" in src
