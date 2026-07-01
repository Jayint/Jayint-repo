"""Tests: obligation tasks route through run_structured_repair (typed path);
discover tasks stay on build_agent.run (free-text path).

Phase 4 (fresh-replay-only run_v3): enable_script_materialization=False is a
deprecated no-op-or-raise flag now — the B3 ablation (obligation task on the
free-text path) is no longer reachable through run_v3 with this flag; a
dedicated raise-test replaces the old B3-ablation behavioral test.

Harness mirrors tests/envstate/test_v3_repair_wiring.py.

Patching strategy
-----------------
- ``src.envstate.graph_scheduler.next_decision`` is patched on the SOURCE module
  because run_v3 imports it with a local ``from ... import`` statement inside the
  function body; patching ``orch.next_decision`` would not intercept that lookup.
- ``src.envstate.orchestrator.run_structured_repair`` is patched on the orchestrator
  module because it is bound there at import time (top-level ``from ... import``).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import pytest

import src.envstate.graph_scheduler as gs_module
import src.envstate.orchestrator as orch
from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.repair_loop import RepairOutcome
from src.envstate.world_model import (
    PlannerDecision,
    Task,
    TaskReport,
    initial_map,
)
from src.sandbox import InstallResult


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _FakeClient:
    """Non-None sentinel for the ``getattr(build_agent, "client", None)`` guard."""


class _CountingBuildAgent:
    """Minimal build agent with a non-None .client and a counting .run."""

    def __init__(self):
        self.run_calls = 0
        self.client = _FakeClient()
        self.model = "fake-model"

    def run(self, task, sandbox_execute, ledger,
            step_offset=0, check=None, budget=None):
        self.run_calls += 1
        return TaskReport(task.goal, "done", (), "free-text run")

    def propose(self, scope, exec_readonly=None, **kwargs):
        return None


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _base_map():
    """WorldModelMap with dep_graph=None — _dep_emit_phase is skipped."""
    return initial_map(
        base_image="python:3.11-slim",
        workdir="/repo",
        language="python",
        build_system="pip",
        repo_layout=(),
    )


def _obligation_task() -> Task:
    """Task with target_node_ids set — triggers the typed-repair path."""
    return Task(
        goal="install syslib:libpq",
        done_when="ldconfig -p | grep -q libpq",
        layer="system",
        facts=(),
        target_node_ids=("syslib:libpq",),
    )


def _discover_task() -> Task:
    """Task with no target_node_ids — stays on the free-text build_agent.run path."""
    return Task(
        goal="discover missing runtime deps",
        done_when="pytest --collect-only -q --disable-warnings",
        layer="tests",
        facts=(),
        target_node_ids=(),
    )


def _noop_reset_to_base() -> None:
    pass


def _noop_run_install_script(script: str) -> InstallResult:
    return InstallResult(rc=0, failing_command=None, lineno=None, stderr="")


def _make_run_v3_inputs(task: Task) -> dict:
    """Build a dict of kwargs for run_v3.

    dep_graph=None → _dep_emit_phase short-circuits immediately (before it
    would ever touch reset_to_base/run_install_script). enable_dep_emit=False
    → extra safeguard (belt-and-suspenders). max_cycles=1 → loop terminates
    after a single task cycle. reset_to_base/run_install_script are required
    unconditionally by run_v3 (Phase 4) even though this harness's dep-emit
    phase never calls them — no-op fakes just satisfy the guard.
    """
    led = ActionLedger()

    def sandbox(cmd: str):
        return (True, "ok")

    def ro(cmd: str):
        return (1, "")

    return dict(
        build_agent=_CountingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_base_map(),
        ledger=led,
        sandbox_execute=sandbox,
        max_cycles=1,
        exec_readonly=ro,
        enable_dep_emit=False,
        reset_to_base=_noop_reset_to_base,
        run_install_script=_noop_run_install_script,
    )


# ---------------------------------------------------------------------------
# Fixture-bundle class
# ---------------------------------------------------------------------------

class _FixtureBundle:
    """Wraps a single run_v3 invocation; exposes run_calls + repair_calls after .run()."""

    def __init__(
        self,
        inputs: dict,
        task: Task,
        monkeypatch,
    ) -> None:
        self._inputs = inputs
        self._task = task
        self._mp = monkeypatch
        self.run_calls: int = 0
        self.repair_calls: int = 0

    def run(self) -> None:
        _repair_ref: dict[str, int] = {"n": 0}
        task = self._task

        # ── patch next_decision on the SOURCE module ──────────────────────────
        def _fake_next_decision(
            graph, run_tests, handed=None, attempt_cap=3, **kwargs
        ):
            return (PlannerDecision(action="task", task=task), None)

        self._mp.setattr(gs_module, "next_decision", _fake_next_decision)

        # ── patch run_structured_repair on the orchestrator module ────────────
        def _fake_repair(graph, failed_id, bundle, cycle, **kwargs):
            _repair_ref["n"] += 1
            return RepairOutcome(
                graph=graph,
                still_failing_id=None,
                manual_blocks=(),
                known_invalid=frozenset(),
                turns_spent=1,
                budget_exhausted=True,   # forces termination after one cycle
            )

        self._mp.setattr(orch, "run_structured_repair", _fake_repair)

        # ── run ───────────────────────────────────────────────────────────────
        orchestrator.run_v3(**self._inputs)

        self.run_calls = self._inputs["build_agent"].run_calls
        self.repair_calls = _repair_ref["n"]


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def _v3_task_fixture(monkeypatch):
    """Obligation task (target_node_ids set)."""
    inputs = _make_run_v3_inputs(task=_obligation_task())
    return _FixtureBundle(inputs, _obligation_task(), monkeypatch)


@pytest.fixture
def _v3_discover_fixture(monkeypatch):
    """Discover task (target_node_ids empty)."""
    inputs = _make_run_v3_inputs(task=_discover_task())
    return _FixtureBundle(inputs, _discover_task(), monkeypatch)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_obligation_task_uses_propose_not_run(_v3_task_fixture):
    """Obligation task must call run_structured_repair (typed path) and must
    NOT call build_agent.run.
    """
    _v3_task_fixture.run()
    assert _v3_task_fixture.run_calls == 0, (
        f"build_agent.run was called {_v3_task_fixture.run_calls} time(s); "
        "obligation task should route through typed repair path"
    )
    assert _v3_task_fixture.repair_calls >= 1, (
        "run_structured_repair was never called for an obligation task"
    )


def test_discover_task_uses_run(_v3_discover_fixture):
    """Discover task (no target_node_ids) must use the free-text build_agent.run path."""
    _v3_discover_fixture.run()
    assert _v3_discover_fixture.run_calls >= 1, (
        "build_agent.run was never called; discover task must stay on free-text path"
    )


def test_b3_ablation_now_raises():
    """Phase 4: enable_script_materialization=False is a deprecated no-op-or-raise
    flag — run_v3 has exactly one executor (fresh full-script replay), so the old
    B3 ablation (obligation task forced onto the free-text path) is no longer
    reachable through run_v3 at all. Pin the deprecation contract instead.
    """
    inputs = _make_run_v3_inputs(task=_obligation_task())
    with pytest.raises(ValueError, match="deprecated"):
        orchestrator.run_v3(**inputs, enable_script_materialization=False)


def test_obligation_task_without_exec_readonly_falls_to_freetext(monkeypatch):
    """Obligation task with exec_readonly=None must NOT enter the typed-repair
    path (which would crash at certify_refresh). Instead it must fall through
    to build_agent.run (free-text path).

    Regression guard for I-1: missing ``exec_readonly is not None`` guard.
    """
    inputs = _make_run_v3_inputs(task=_obligation_task())
    # Override exec_readonly to None to reproduce the crash scenario
    inputs["exec_readonly"] = None

    bundle = _FixtureBundle(inputs, _obligation_task(), monkeypatch)
    bundle.run()

    assert bundle.repair_calls == 0, (
        "run_structured_repair was called with exec_readonly=None; "
        "this would crash at certify_refresh(graph, None, cycle)"
    )
    assert bundle.run_calls >= 1, (
        "build_agent.run was never called; obligation task with exec_readonly=None "
        "must fall through to the free-text path"
    )
