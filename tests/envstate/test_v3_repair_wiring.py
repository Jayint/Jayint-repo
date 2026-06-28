"""Test that a failed block_emit triggers run_structured_repair in the v3 path.

Mirrors the harness in tests/test_v3_block_emit_wiring.py.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.envstate.block_emit as be
import src.envstate.orchestrator as orch
from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.repair_loop import RepairOutcome
from src.envstate.world_model import TaskReport, initial_map, merge_map
from python_deps.depgraph.evidence_log import EvidenceBundle
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


# ---------------------------------------------------------------------------
# Fakes (mirrored from test_v3_block_emit_wiring.py with .client added)
# ---------------------------------------------------------------------------

class _FakeClient:
    """Non-None sentinel to pass the `getattr(build_agent, "client", None)` guard."""


class _RecordingBuildAgent:
    """Minimal build agent with a non-None .client so the repair guard fires."""

    def __init__(self):
        self.tasks = []
        self.client = _FakeClient()   # repair guard: getattr(build_agent, "client", None)
        self.model = "fake-model"

    def run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget=None):
        self.tasks.append(task)
        return TaskReport(task_goal="t", status="blocked", commands=(), learning="b")

    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        return TaskReport(task_goal="r", status="done", commands=(), learning="ok")

    def propose(self, scope, **kwargs):
        return None


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _syslib_map():
    """A WorldModelMap with one MISSING SystemLib (same as in test_v3_block_emit_wiring)."""
    node = Node(
        id="syslib:libpq.so",
        type=NodeType.SYSTEM_LIB,
        name="libpq.so",
        layer=Layer.SYSTEM,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        check_command="ldconfig -p | grep -q libpq",
        chosen_fix="apt:libpq-dev",
    )
    base = initial_map(
        base_image="python:3.11-slim",
        workdir="/repo",
        language="python",
        build_system="pip",
        repo_layout=(),
    )
    return merge_map(base, dep_graph=DepGraph().with_node(node))


def _build_inputs():
    """Construct all-callable fakes for run_v3."""
    led = ActionLedger()

    def sandbox(cmd):
        return (True, "ok")

    def ro(cmd):
        # Node stays MISSING through certify_refresh so block_emit is called.
        return (1, "")

    return dict(
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_syslib_map(),
        ledger=led,
        sandbox_execute=sandbox,
        max_cycles=1,
        exec_readonly=ro,
        enable_dep_emit=True,
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_failed_block_invokes_structured_repair(monkeypatch):
    """When block_emit returns a non-None failed_id and build_agent.client is
    not None, run_structured_repair must be called at least once.

    Confirm RED before wiring: with Slice-A code the stub is never called
    because the failed block is discarded.  After wiring (Slice B Task 10)
    the test turns GREEN.
    """
    calls = {"repair": 0}

    # --- mock block_emit: always return a failed block id -------------------
    def _fake_block_emit(graph, sandbox_execute, exec_readonly, ledger, cycle,
                         *, manual_blocks=()):
        return (graph, EvidenceBundle(), "system.libpq.so")

    monkeypatch.setattr(be, "block_emit", _fake_block_emit)

    # --- mock run_structured_repair: record the call and return success -----
    def _fake_repair(graph, failed_id, bundle, cycle, **kwargs):
        calls["repair"] += 1
        return RepairOutcome(
            graph=graph,
            still_failing_id=None,   # resolved — no budget exhaustion
            manual_blocks=(),
            known_invalid=frozenset(),
            turns_spent=1,
            budget_exhausted=False,
        )

    monkeypatch.setattr(orch, "run_structured_repair", _fake_repair)

    # --- run -------------------------------------------------------------------
    inputs = _build_inputs()
    orchestrator.run_v3(**inputs, enable_script_materialization=True)

    # --- assert ----------------------------------------------------------------
    assert calls["repair"] >= 1, (
        "run_structured_repair was never called; "
        "failed block is still being discarded (wiring not yet applied)"
    )
