"""Smoke-tests that DOCKERAGENT_ENABLE_GRAPH_SCHEDULER plumbing is present.

DockerAgent.__init__ is too heavyweight to construct in tests (it builds an
OpenAI client and a Docker sandbox, requiring an API key + Docker). So, exactly
like tests/test_runtime_feedback_flag.py, we verify the plumbing via signature
inspection + source text rather than constructing the agent.
"""
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


def test_run_v1_accepts_enable_graph_scheduler():
    from src.envstate.orchestrator import run_v1
    sig = inspect.signature(run_v1)
    assert "enable_graph_scheduler" in sig.parameters, (
        "run_v1 must accept enable_graph_scheduler kwarg"
    )
    assert sig.parameters["enable_graph_scheduler"].default is False


def test_docker_agent_has_enable_graph_scheduler_param():
    """DockerAgent.__init__ must accept enable_graph_scheduler (default False)."""
    agent_py = _ROOT / "agent.py"
    src = agent_py.read_text()
    assert "enable_graph_scheduler=False" in src, (
        "agent.py must define/accept enable_graph_scheduler default False"
    )
    # The ctor must forward it down to run_v1 (the orchestrator loop).
    assert 'enable_graph_scheduler=getattr(self, "enable_graph_scheduler", False)' in src


def test_enable_graph_scheduler_implies_dep_emit_and_dep_graph():
    """enable_graph_scheduler=True must imply enable_dep_emit and enable_dep_graph.

    DockerAgent.__init__ is too heavyweight to construct here, so we verify the
    implication by asserting the flag-derivation lines appear in the correct order
    in agent.py source.
    """
    agent_py = _ROOT / "agent.py"
    src = agent_py.read_text()

    # 1. enable_graph_scheduler is set first
    idx_gs = src.index("self.enable_graph_scheduler: bool = bool(enable_graph_scheduler)")
    # 2. enable_dep_emit ORs in enable_graph_scheduler
    idx_emit = src.index("or self.enable_graph_scheduler")
    # 3. enable_dep_graph picks up enable_dep_emit
    idx_graph = src.index("self.enable_dep_graph = enable_dep_graph or self.enable_dep_emit")

    assert idx_gs < idx_emit < idx_graph, (
        "enable_graph_scheduler must be assigned before enable_dep_emit ORs it in, "
        "which must be assigned before enable_dep_graph"
    )
    # enable_dep_emit must OR in graph_scheduler on the same logical line.
    assert "self.enable_dep_emit: bool = bool(enable_dep_emit) or self.enable_runtime_feedback or self.enable_graph_scheduler" in src
    # enable_graph_scheduler must ALSO imply enable_runtime_feedback, so the full
    # four-role loop (incl. OBSERVE / _runtime_ingest_phase) is active under v1gs.
    assert "self.enable_runtime_feedback: bool = bool(enable_runtime_feedback) or self.enable_graph_scheduler" in src


def test_graph_scheduler_implies_deterministic_maintainer_without_contract_graph():
    """v1gs keeps OBSERVE deterministic: graph_scheduler implies the deterministic
    maintainer (dropping the LLM 'reflection' maintainer), but must NOT drag
    contract_graph back on — done stays the pure two-oracle.
    """
    agent_py = _ROOT / "agent.py"
    src = agent_py.read_text()

    impl = "self.enable_deterministic_maintainer = bool(enable_deterministic_maintainer) or self.enable_graph_scheduler"
    cg = "self.enable_contract_graph = enable_contract_graph or enable_deterministic_maintainer"

    # 1. graph_scheduler implies the deterministic maintainer.
    assert impl in src
    # 2. the contract_graph line couples to the RAW det-maintainer param ONLY — it must
    #    NOT OR in graph_scheduler (else v1gs would re-enable the contract graph).
    assert cg in src
    assert (cg + " or self.enable_graph_scheduler") not in src
    # 3. ordering is what keeps contract_graph off: the contract_graph line (raw-param
    #    read) MUST come before the deterministic-maintainer implication.
    assert src.index(cg) < src.index(impl), (
        "contract_graph line must precede the det-maintainer implication so the "
        "implied maintainer never forces contract_graph on"
    )


def test_argparse_exposes_enable_graph_scheduler():
    agent_py = _ROOT / "agent.py"
    src = agent_py.read_text()
    assert '"--enable-graph-scheduler"' in src
    assert "enable_graph_scheduler=args.enable_graph_scheduler" in src


def test_multi_docker_eval_adapter_reads_env():
    adapter_py = _ROOT / "multi_docker_eval_adapter.py"
    src = adapter_py.read_text()
    assert "DOCKERAGENT_ENABLE_GRAPH_SCHEDULER" in src
    assert "enable_graph_scheduler=_enable_graph_scheduler" in src


def test_run_rat_benchmark_sets_env_and_arm():
    rat_py = _ROOT / "run_rat_benchmark.py"
    src = rat_py.read_text()
    assert "DOCKERAGENT_ENABLE_GRAPH_SCHEDULER" in src
    assert '"v3"' in src  # renamed v1gs→v3 in Phase 1 Task 4


def test_run_repo2run_has_v3_preset_and_forward():
    r2r_py = _ROOT / "run_repo2run_benchmark.py"
    src = r2r_py.read_text()
    assert '"v3"' in src
    assert '"enable_graph_scheduler": True' in src
    assert "--enable-graph-scheduler" in src


def test_planner_deprecation_marker_is_comment_only():
    planner_py = _ROOT / "src" / "envstate" / "planner.py"
    src = planner_py.read_text()
    assert "DEPRECATED (2026-06-26)" in src
    assert "enable_graph_scheduler" in src


def test_repair_is_gated_under_graph_scheduler():
    src = (_ROOT / "src" / "envstate" / "orchestrator.py").read_text()
    # repair_failed_nodes is only reachable under the enable_graph_scheduler guard
    idx_guard = src.index(
        "if enable_graph_scheduler:\n"
        "            from src.envstate.depgraph_live import repair_failed_nodes"
    )
    assert idx_guard > 0
