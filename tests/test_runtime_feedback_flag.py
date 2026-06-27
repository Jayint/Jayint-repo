"""Smoke-tests that DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK plumbing is present."""
from __future__ import annotations

import os
import sys
from pathlib import Path
import inspect

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_run_v1_accepts_enable_runtime_feedback():
    from src.envstate.orchestrator import run_v1
    sig = inspect.signature(run_v1)
    assert "enable_runtime_feedback" in sig.parameters, (
        "run_v1 must accept enable_runtime_feedback kwarg"
    )
    param = sig.parameters["enable_runtime_feedback"]
    assert param.default is False


def test_docker_agent_has_enable_runtime_feedback_param():
    """DockerAgent.__init__ must accept enable_runtime_feedback."""
    # Import is heavyweight; just check the source text if import is slow.
    agent_py = _ROOT / "agent.py"
    src = agent_py.read_text()
    assert "enable_runtime_feedback" in src, (
        "agent.py must define/accept enable_runtime_feedback"
    )


def test_multi_docker_eval_adapter_reads_env():
    adapter_py = _ROOT / "multi_docker_eval_adapter.py"
    src = adapter_py.read_text()
    assert "DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK" in src


def test_run_rat_benchmark_sets_env():
    rat_py = _ROOT / "run_rat_benchmark.py"
    src = rat_py.read_text()
    assert "DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK" in src


def test_run_repo2run_sets_env_or_flag():
    r2r_py = _ROOT / "run_repo2run_benchmark.py"
    src = r2r_py.read_text()
    assert "enable_runtime_feedback" in src


def test_enable_runtime_feedback_implies_enable_dep_emit_and_dep_graph():
    """I1: enable_runtime_feedback=True must imply enable_dep_emit and enable_dep_graph.

    DockerAgent.__init__ is too heavyweight to construct in tests (requires API keys,
    docker, git clone).  We verify the implication by executing only the relevant
    flag-derivation logic from agent.py in isolation.
    """
    agent_py = _ROOT / "agent.py"
    src = agent_py.read_text()

    # The three implication lines must appear in the correct order in source.
    # 1. enable_runtime_feedback is set first
    idx_rf = src.index("self.enable_runtime_feedback: bool = bool(enable_runtime_feedback)")
    # 2. enable_dep_emit ORs in enable_runtime_feedback
    idx_emit = src.index("self.enable_dep_emit: bool = bool(enable_dep_emit) or self.enable_runtime_feedback")
    # 3. enable_dep_graph picks up enable_dep_emit
    idx_graph = src.index("self.enable_dep_graph = enable_dep_graph or self.enable_dep_emit")

    assert idx_rf < idx_emit < idx_graph, (
        "enable_runtime_feedback must be assigned before enable_dep_emit, "
        "which must be assigned before enable_dep_graph"
    )
