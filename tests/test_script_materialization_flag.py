"""Slice A: enable_script_materialization plumbing. DockerAgent.__init__ builds an
OpenAI client + Docker sandbox, so (exactly like tests/test_graph_scheduler_flag.py)
we verify the cascade via source text + the run_v3 param via signature inspection."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_constructor_accepts_param_default_none():
    src = (_ROOT / "agent.py").read_text()
    assert "enable_script_materialization=None" in src, (
        "DockerAgent.__init__ must accept enable_script_materialization (default None = inherit graph-scheduler)"
    )


def test_cascade_defaults_to_graph_scheduler_when_none():
    """When the param is None, the flag inherits enable_graph_scheduler (B5 ON with v3);
    when explicitly set, bool() of the value wins (B3 ablation can force it OFF)."""
    src = (_ROOT / "agent.py").read_text()
    assert (
        "self.enable_script_materialization = (" in src
        and "self.enable_graph_scheduler if enable_script_materialization is None" in src
        and "else bool(enable_script_materialization)" in src
    ), "cascade must inherit enable_graph_scheduler when None, else bool(value)"
    # the cascade must come AFTER enable_graph_scheduler is assigned
    assert src.index("self.enable_graph_scheduler: bool = bool(enable_graph_scheduler)") < \
        src.index("self.enable_script_materialization = ("), \
        "enable_graph_scheduler must be assigned before the script-materialization cascade reads it"


def test_run_v3_call_passes_the_flag():
    src = (_ROOT / "agent.py").read_text()
    assert "enable_script_materialization=self.enable_script_materialization" in src, (
        "the run_v3 invocation must forward the flag"
    )


def test_run_v3_accepts_the_param():
    from src.envstate import orchestrator
    sig = inspect.signature(orchestrator.run_v3)
    assert "enable_script_materialization" in sig.parameters
    assert sig.parameters["enable_script_materialization"].default is True
