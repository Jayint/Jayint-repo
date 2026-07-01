"""Phase 7: installability gate binding by construction (from the per-cycle replay).

Under Model B, run_v3's sole executor is a fresh full-script replay from base
every cycle (no memoization) — there is no separate terminal-replay step, so
the latest cycle's InstallResult IS the installability proof. These tests
cover:

  1. ``evaluate_installability_gate(graph, replay=...)`` is BINDING (not
     provisional) whenever a real replay result is supplied — both the
     rc=0/passed and rc!=0/failed cases.
  2. A full ``run_v3`` run to "done" (via a fake sandbox that always returns
     rc=0) reports ``provisional is False`` for the installability gate —
     i.e. the canonical path never falls back to the graph-frontier heuristic.

The existing provisional-path tests (``replay=None``, used only by the
block_emit ablation) live in test_gates_installability.py and are untouched.
"""
import dataclasses
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate.gates import evaluate_installability_gate
from src.envstate.ledger import ActionLedger
from src.envstate.orchestrator import VERIFY_TEST_CMD, run_v3
from src.envstate.world_model import initial_map, merge_map
from src.sandbox import InstallResult
from python_deps.depgraph.schema import DepGraph, DiscoveredBy, Layer, Node, NodeType, State


# ---------------------------------------------------------------------------
# 1. evaluate_installability_gate(graph, replay=...) — pure unit tests
# ---------------------------------------------------------------------------

def test_installability_gate_binding_on_real_replay():
    class _R:
        rc = 0
        failing_command = None

    g = evaluate_installability_gate(None, replay=_R())
    assert g.passed is True
    assert g.provisional is False
    assert "fresh replay rc=0" in g.evidence
    assert g.command == "fresh-from-base setup.sh replay"


def test_installability_gate_binding_fail():
    class _R:
        rc = 1
        failing_command = "apt-get install -y libpq-dev"

    g = evaluate_installability_gate(None, replay=_R())
    assert g.passed is False
    assert g.provisional is False
    assert "libpq-dev" in g.evidence


# ---------------------------------------------------------------------------
# 2. run_v3 to "done" via a fake sandbox — the gate reported must be binding
# ---------------------------------------------------------------------------

class _NoClientBuildAgent:
    """No `.client` -> discover tasks would route through the deterministic
    gate, and typed repair is never reachable. Unused here (the harness never
    fails), kept only so run_v3's call sites resolve `getattr(..., "client")`
    safely."""


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _pkg_map():
    node = Node(
        id="pkg:requests", type=NodeType.PACKAGE, name="requests", version="2.31.0",
        layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
        check_command="python3 -c 'import requests'",
    )
    base = initial_map(base_image="python:3.11-slim", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, dep_graph=DepGraph().with_node(node))


def _run_v3_to_done_with_fake_sandbox():
    """Fake sandbox that installs the one MISSING package node on the first
    replay and reports success from then on — the scheduler reaches "done" on
    cycle 1 (frontier empties + tests pass) with a real rc=0 InstallResult
    already recorded as ``_last_replay_result``."""
    state = {"installed": False}

    def sandbox_execute(cmd):
        if cmd == VERIFY_TEST_CMD:
            # The anti-hollow done-gate (_verified_test_run_passed) requires a
            # genuine execution summary ("N passed"), not just rc=0/ok=True.
            return (
                (state["installed"], "1 passed in 0.01s")
                if state["installed"]
                else (False, "no tests ran")
            )
        return (True, "ok")

    def exec_readonly(cmd):
        if "import requests" in cmd:
            return (0, "") if state["installed"] else (1, "ModuleNotFoundError")
        return (1, "")

    def reset_to_base():
        pass

    def run_install_script(script):
        state["installed"] = True
        return InstallResult(rc=0, failing_command=None, lineno=None, stderr="")

    captured: dict = {}

    def gate_observer(gates):
        installability, testability = gates
        captured["gates"] = {
            "installability": dataclasses.asdict(installability),
            "testability": dataclasses.asdict(testability),
        }

    final_map, stop = run_v3(
        build_agent=_NoClientBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_pkg_map(),
        ledger=ActionLedger(),
        sandbox_execute=sandbox_execute,
        max_cycles=3,
        exec_readonly=exec_readonly,
        enable_dep_emit=True,
        reset_to_base=reset_to_base,
        run_install_script=run_install_script,
        enable_gate_observability=True,
        gate_observer=gate_observer,
    )
    return SimpleNamespace(final_map=final_map, stop=stop, gates=captured["gates"])


def test_done_reports_binding_gate_not_provisional():
    trace = _run_v3_to_done_with_fake_sandbox()
    assert trace.stop == "planner_done"
    assert trace.gates["installability"]["provisional"] is False
    assert trace.gates["installability"]["passed"] is True
    assert "fresh replay rc=0" in trace.gates["installability"]["evidence"]
