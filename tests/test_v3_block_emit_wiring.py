"""Phase 4 (fresh-replay-only run_v3): block_emit/emit_drain no longer run inside
_dep_emit_phase under ANY flag combination — the fresh full-script replay body
(orchestrator._binding_emit) is the sole executor. enable_script_materialization
and enable_binding_install are now deprecated no-op-or-raise flags; the toggle
tests this file used to carry (block vs. drain vs. binding-install) are gone —
what remains is the deprecation contract: passing False for either raises.

The behavioral replacement for "does run_v3 use the fresh-replay executor" now
lives in tests/test_v3_replay_executor.py. block_emit/emit_drain themselves are
untouched and still directly unit-tested in their own modules for run_v1 /
future ablation use (Phase 9).
"""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import TaskReport, initial_map, merge_map
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)
from src.sandbox import InstallResult


# --- minimal in-process fakes (copied from test_graph_scheduler_wiring.py) ---
class _RecordingBuildAgent:
    def __init__(self): self.tasks = []
    def run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget=None):
        self.tasks.append(task)
        return TaskReport(task_goal="t", status="blocked", commands=(), learning="b")
    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        return TaskReport(task_goal="r", status="done", commands=(), learning="ok")


class _NoopMaintainer:
    def update(self, world_map, report): return world_map


def _syslib_map():
    """A WorldModelMap with one MISSING SystemLib whose apt fix + ldconfig check let
    the fresh-replay executor install + certify it deterministically."""
    node = Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB, name="libpq.so",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev")
    base = initial_map(base_image="python:3.11-slim", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, dep_graph=DepGraph().with_node(node))


def build_run_v3_inputs():
    """Stateful check: ldconfig fails until the apt install runs, so certify_refresh
    (which runs before the emit phase) does NOT pre-satisfy the node. Includes
    reset_to_base/run_install_script fakes (now mandatory — Phase 4) so a raise
    triggers for the DEPRECATED-FLAG reason under test, not the missing-executor
    guard."""
    state = {"installed": False}
    led = ActionLedger()
    def sandbox(cmd):
        if "libpq-dev" in cmd:
            state["installed"] = True
        return (True, "installed")
    def ro(cmd):
        if "ldconfig" in cmd:
            return (0, "libpq") if state["installed"] else (1, "")
        return (1, "")
    def reset_to_base():
        state["installed"] = False
    def run_install_script(script):
        state["installed"] = True
        return InstallResult(rc=0, failing_command=None, lineno=None, stderr="")
    return dict(
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_syslib_map(),
        ledger=led,
        sandbox_execute=sandbox,
        max_cycles=1,
        exec_readonly=ro,
        enable_dep_emit=True,
        reset_to_base=reset_to_base,
        run_install_script=run_install_script,
    )


def test_toggle_off_now_raises():
    """enable_script_materialization=False is deprecated (Phase 4): run_v3 has
    exactly one executor (fresh full-script replay), so a value that used to
    select the emit_drain+repair_failed_nodes branch now raises instead of
    silently running the canonical executor under a name that implies it was
    skipped."""
    inputs = build_run_v3_inputs()
    with pytest.raises(ValueError, match="deprecated"):
        orchestrator.run_v3(**inputs, enable_script_materialization=False)


def test_binding_install_toggle_off_also_raises():
    """Companion: enable_binding_install=False is the other deprecated flag —
    same contract, same guard."""
    inputs = build_run_v3_inputs()
    with pytest.raises(ValueError, match="deprecated"):
        orchestrator.run_v3(**inputs, enable_binding_install=False)
