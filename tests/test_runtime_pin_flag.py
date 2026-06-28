# tests/test_runtime_pin_flag.py
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


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


# ---------------------------------------------------------------------------
# Guard: env-var leak fix — both directions must hold.
# ---------------------------------------------------------------------------

def _make_args(**kwargs) -> argparse.Namespace:
    """Minimal namespace accepted by build_agent_command."""
    defaults = dict(
        base_image="auto",
        model="claude-sonnet-4-6",
        max_steps=30,
        agent_command_timeout=1800,
        enable_observation_compression=False,
        enable_long_term_memory=False,
        memory_embedding_model="text-embedding-3-small",
        memory_path=None,
        keep_container=False,
        enable_supervisor=False,
        enable_fullstate_worker=False,
        fullstate_worker_prompt=False,
        enable_envstate=False,
        enable_cleanroom=False,
        enable_v1=False,
        enable_dep_graph=False,
        enable_dep_emit=False,
        enable_runtime_feedback=False,
        enable_graph_scheduler=False,
        enable_runtime_pin=False,
        enable_service_provision=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_service_provision_env_set_when_true(monkeypatch, tmp_path):
    """When enable_service_provision=True, build_agent_command must set the env var to '1'."""
    monkeypatch.delenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", raising=False)

    from run_repo2run_benchmark import build_agent_command

    build_agent_command(
        python_executable="/usr/bin/python3",
        repo_root=_ROOT,
        instance={"repo_url": "https://github.com/example/repo", "base_commit": "abc"},
        workplace=tmp_path,
        args=_make_args(enable_service_provision=True),
    )
    assert os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"


def test_service_provision_env_cleared_when_false(monkeypatch, tmp_path):
    """When enable_service_provision=False, build_agent_command must POP the env var
    even if a previous call (or test) had set it to '1'.

    RED proof: before the else-branch fix, the second assertion would fail because
    the env var remained as '1' from the previous call with enable_service_provision=True.
    """
    # Pre-set the env var as if a previous v3-arm call had left it in place.
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")

    from run_repo2run_benchmark import build_agent_command

    build_agent_command(
        python_executable="/usr/bin/python3",
        repo_root=_ROOT,
        instance={"repo_url": "https://github.com/example/repo", "base_commit": "abc"},
        workplace=tmp_path,
        args=_make_args(enable_service_provision=False),
    )
    assert "DOCKERAGENT_ENABLE_SERVICE_PROVISION" not in os.environ
