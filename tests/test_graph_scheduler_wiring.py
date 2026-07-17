"""Wiring test: verify the graph scheduler drives run_v3's decision loop.

Uses synthetic build_agent/maintainer stubs (no Docker/LLM). Confirms:
  - run_v3  -> the scheduler drives; there is no planner param at all; a targeted
    obligation task with no build_agent.client gives up honestly (GIVEUP_CONFIG)
    rather than silently falling back to build_agent.run — Task 5a removed run_v3's
    free-text fallback entirely (see tests/envstate/test_v3_task_branch.py for the
    full 3-way-dispatch coverage)
  - run_v3  -> a sufficiency-stuck run gives up (does not run to max_cycles)

(The legacy planner-driven loop and its emit-drain-prefix wiring were retired in
Phase 0 of the src/ stage-refactor; only the run_v3 scheduler pins remain here.)
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
from src.envstate.orchestrator import run_v3
from src.envstate.world_model import (
    TaskReport,
    WorldModelMap,
    initial_map,
    merge_map,
)
from src.sandbox import InstallResult
from graph.model import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


# run_v3 is fresh-replay-only (Phase 4): reset_to_base/run_install_script are
# mandatory. These fixtures build no-op fakes so tests unrelated to the
# install-executor mechanics don't need a real sandbox — the run_v3 fakes in
# this file target the graph-scheduler decision loop, not the emit executor.
def _noop_reset_to_base():
    pass


def _noop_run_install_script(script):
    return InstallResult(rc=0, failing_command=None, lineno=None, stderr="")


# ── Stubs ────────────────────────────────────────────────────────────────────

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


# ── scheduler drives, no free-text fallback ──────────────────────────────────
#
# Historically this test asserted that a targeted obligation task with no
# build_agent.client fell back to build_agent.run (free-text path) — the old
# run_v3 dispatch condition silently downgraded when `.client` was absent.
# Task 5a (orchestrator.py task-dispatch consolidation) removed that fallback
# entirely: run_v3 now has ZERO free-text mutation. A target-bearing task
# with no client (or no exec_readonly) gives up honestly via GIVEUP_CONFIG
# instead. build_agent.run is asserted NEVER called — this is the correct
# updated pin for "scheduler drives" (there is no planner param on run_v3 to
# begin with, so "planner untouched" was always structurally guaranteed).

def test_flag_on_scheduler_gives_up_without_client():
    build_agent = _RecordingBuildAgent()
    final_map, stop = run_v3(
        build_agent=build_agent,
        maintainer=_NoopMaintainer(),
        initial_world_map=_missing_node_map(),
        ledger=ActionLedger(),
        sandbox_execute=_sandbox_ok,
        max_cycles=1,
        exec_readonly=lambda cmd: (1, "missing"),   # keep node MISSING -> frontier non-empty
        enable_dep_emit=True,
        reset_to_base=_noop_reset_to_base,
        run_install_script=_noop_run_install_script,
    )
    assert stop == "planner_giveup", (
        f"targeted obligation task with no build_agent.client must give up "
        f"honestly (GIVEUP_CONFIG), got {stop!r}"
    )
    assert build_agent.tasks == [], (
        "build_agent.run must never be called from run_v3 (Task 5a removed the "
        "free-text fallback entirely)"
    )


# ── stuck -> giveup ───────────────────────────────────────────────────────────

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
        reset_to_base=_noop_reset_to_base,
        run_install_script=_noop_run_install_script,
    )
    assert stop == "planner_giveup", f"expected stuck->giveup, got {stop!r}"


def test_stuck_path_fires_on_cycle_callback():
    """GIVEUP_STUCK must invoke on_cycle for the final cycle (telemetry parity).

    The pre-split monolith fired on_cycle on this path; the v3 arm was missing
    the callback, so the stuck cycle was absent from the trace.  This test pins
    the fix: on_cycle is called at least once when run_v3 gives up due to
    two consecutive discover rounds with no new obligations.
    """
    calls: list[tuple] = []

    final_map, stop = run_v3(
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_no_check_node_map(),
        ledger=ActionLedger(),
        sandbox_execute=_sandbox_fail,
        max_cycles=4,
        exec_readonly=lambda cmd: (1, ""),
        enable_dep_emit=True,
        on_cycle=lambda *a: calls.append(a),
        reset_to_base=_noop_reset_to_base,
        run_install_script=_noop_run_install_script,
    )
    assert stop == "planner_giveup"
    assert calls, "on_cycle must be called on the GIVEUP_STUCK path"
    # The final entry must correspond to a 'task' decision (discover task = stuck sentinel)
    final_cycle_num, _map, final_decision, _report = calls[-1]
    assert final_decision.action == "task", (
        f"stuck path decision must be 'task', got {final_decision.action!r}"
    )
