"""Wiring test: verify the graph scheduler gates run_v1's decision.

Uses synthetic planner/build_agent/maintainer stubs (no Docker/LLM). Confirms:
  - flag OFF -> planner.decide drives the cycle (byte-identical to today)
  - flag ON  -> the scheduler drives; planner.decide is never called; the
    build_agent receives a graph-derived task with a host `check`
  - flag ON  -> the deterministic emit_drain is suppressed; flag OFF runs it
  - flag ON  -> a sufficiency-stuck run gives up (does not run to max_cycles)
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.envstate.ledger import ActionLedger
from src.envstate.orchestrator import run_v1, run_v3
from src.envstate.world_model import (
    PlannerDecision,
    Task,
    TaskReport,
    WorldModelMap,
    initial_map,
    merge_map,
)
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


# ── Stubs ────────────────────────────────────────────────────────────────────

class _QueuePlanner:
    def __init__(self, decisions):
        self._queue = list(decisions)
        self.called = False

    def decide(self, world_map):
        self.called = True
        assert self._queue, "_QueuePlanner.decide called more times than expected"
        return self._queue.pop(0)


class _RecordingBuildAgent:
    """Records the task/check it receives; returns a blocked report."""
    def __init__(self):
        self.tasks = []
        self.checks = []

    def run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget=None):
        self.tasks.append(task)
        self.checks.append(check)
        return TaskReport(task_goal="task", status="blocked", commands=(), learning="blocked")

    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        return TaskReport(task_goal="recipe", status="done", commands=(), learning="ok")


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _task() -> Task:
    return Task(goal="install deps", done_when="pip exits 0", layer="deps", facts=())


def _sandbox_ok(cmd):
    return True, "ok"


def _sandbox_fail(cmd):
    return False, "boom"


def _missing_node_map() -> WorldModelMap:
    """A WorldModelMap carrying a single MISSING package node with a check_command."""
    node = Node(
        id="pkg:requests",
        type=NodeType.PACKAGE,
        name="requests",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.MISSING,
        check_command="python -c 'import requests'",
    )
    base = initial_map(
        base_image="python:3.11",
        workdir="/repo",
        language="python",
        build_system="pip",
        repo_layout=(),
    )
    return merge_map(base, dep_graph=DepGraph().with_node(node))


def _no_check_node_map() -> WorldModelMap:
    """A WorldModelMap whose node has NO check_command (frontier always empty)."""
    node = Node(
        id="pkg:mystery",
        type=NodeType.PACKAGE,
        name="mystery",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.MISSING,
        check_command=None,
    )
    base = initial_map(
        base_image="python:3.11",
        workdir="/repo",
        language="python",
        build_system="pip",
        repo_layout=(),
    )
    return merge_map(base, dep_graph=DepGraph().with_node(node))


# ── 1. flag OFF: planner drives ──────────────────────────────────────────────

def test_flag_off_planner_drives():
    planner = _QueuePlanner([PlannerDecision(action="done")])
    final_map, stop = run_v1(
        planner=planner,
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_missing_node_map(),
        ledger=ActionLedger(),
        sandbox_execute=_sandbox_ok,
        max_cycles=1,
    )
    assert planner.called, "run_v1 must consult planner.decide"
    assert stop == "planner_done"


# ── 2. flag ON: scheduler drives, planner untouched ──────────────────────────

def test_flag_on_scheduler_drives_planner_untouched():
    build_agent = _RecordingBuildAgent()
    run_v3(
        build_agent=build_agent,
        maintainer=_NoopMaintainer(),
        initial_world_map=_missing_node_map(),
        ledger=ActionLedger(),
        sandbox_execute=_sandbox_ok,
        max_cycles=1,
        exec_readonly=lambda cmd: (1, "missing"),   # keep node MISSING -> frontier non-empty
        enable_dep_emit=True,
    )
    assert build_agent.tasks, "build_agent.run must be invoked with the frontier task"
    task = build_agent.tasks[0]
    assert task.target_node_ids == ("pkg:requests",)
    assert build_agent.checks[0] == task.done_when


# ── 3. drain runs as deterministic prefix under the flag (emit-prefix decision) ─
#
# Spec: docs/superpowers/specs/2026-06-26-unified-executor-loop-delta.md §0
#
# INVERTED from the old "drain suppressed under scheduler" test.  emit_drain now
# runs as a deterministic prefix regardless of enable_graph_scheduler so the LLM
# only ever sees the irreducible non-emittable residual.  This reverses the
# original "no deterministic tier under the scheduler" decision — intentional.

def test_drain_runs_under_flag_as_prefix(monkeypatch):
    """emit_drain MUST run under the graph scheduler (emit-prefix path).

    Rationale: the batch drain handles all reciped/emittable nodes deterministically
    before the scheduler's LLM turn; the LLM only receives the non-emittable residual.
    See emit-prefix-plan.md Edit A and spec §0.
    """
    calls = {"n": 0}
    import src.envstate.depgraph_live as live
    real_drain = live.emit_drain

    def _spy(*args, **kwargs):
        calls["n"] += 1
        return real_drain(*args, **kwargs)

    monkeypatch.setattr(live, "emit_drain", _spy)

    # V3 arm (scheduler): drain MUST run (emit-prefix decision).
    run_v3(
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_missing_node_map(),
        ledger=ActionLedger(),
        sandbox_execute=_sandbox_ok,
        max_cycles=1,
        exec_readonly=lambda cmd: (1, "missing"),
        enable_dep_emit=True,
    )
    assert calls["n"] >= 1, "emit_drain MUST run in run_v3 (emit-prefix)"

    # Contrast: V1 arm (dep_emit on) also runs the drain — unchanged.
    before = calls["n"]
    run_v1(
        planner=_QueuePlanner([PlannerDecision(action="done")]),
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_missing_node_map(),
        ledger=ActionLedger(),
        sandbox_execute=_sandbox_ok,
        max_cycles=1,
        exec_readonly=lambda cmd: (1, "missing"),
        enable_dep_emit=True,
    )
    assert calls["n"] > before, "emit_drain MUST run with dep_emit on in run_v1"


# ── 4. stuck -> giveup ───────────────────────────────────────────────────────

def test_stuck_yields_giveup_before_max_cycles():
    final_map, stop = run_v3(
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_no_check_node_map(),
        ledger=ActionLedger(),
        sandbox_execute=_sandbox_fail,         # run_tests() stays red
        max_cycles=4,
        exec_readonly=lambda cmd: (1, ""),     # certify reveals nothing new
        enable_dep_emit=True,
    )
    assert stop == "planner_giveup", f"expected stuck->giveup, got {stop!r}"
