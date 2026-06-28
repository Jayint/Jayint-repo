import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.envstate.block_emit as be
import src.envstate.depgraph_live as dl
from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import TaskReport, initial_map, merge_map
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


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
    block_emit (and emit_drain) install + certify it deterministically."""
    node = Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB, name="libpq.so",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev")
    base = initial_map(base_image="python:3.11-slim", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, dep_graph=DepGraph().with_node(node))


def build_run_v3_inputs():
    """Stateful check: ldconfig fails until the apt install runs, so certify_refresh
    (which runs before the emit phase) does NOT pre-satisfy the node."""
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


def _spy(mod, name, calls, key):
    real = getattr(mod, name)
    def wrapper(*a, **k):
        calls.append(key)
        return real(*a, **k)        # passthrough so the real phase still runs
    return wrapper


def test_toggle_on_uses_block_emit(monkeypatch):
    calls = []
    monkeypatch.setattr(be, "block_emit", _spy(be, "block_emit", calls, "block"))
    monkeypatch.setattr(dl, "emit_drain", _spy(dl, "emit_drain", calls, "drain"))
    inputs = build_run_v3_inputs()
    final_map, _ = orchestrator.run_v3(**inputs, enable_script_materialization=True)
    assert "block" in calls and "drain" not in calls
    assert final_map.dep_graph.get("syslib:libpq.so").state is State.SATISFIED
    assert any("libpq-dev" in e.cmd for e in inputs["ledger"].events())   # dual-write happened


def test_toggle_off_uses_emit_drain_and_repair(monkeypatch):
    calls = []
    monkeypatch.setattr(be, "block_emit", _spy(be, "block_emit", calls, "block"))
    monkeypatch.setattr(dl, "emit_drain", _spy(dl, "emit_drain", calls, "drain"))
    monkeypatch.setattr(dl, "repair_failed_nodes", _spy(dl, "repair_failed_nodes", calls, "repair"))
    inputs = build_run_v3_inputs()
    orchestrator.run_v3(**inputs, enable_script_materialization=False)
    assert "drain" in calls and "repair" in calls and "block" not in calls
