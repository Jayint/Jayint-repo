"""Task 4 ADMISSION RULE — the v3-arm loop admits a RuntimePlan's service
obligations into its working graph at loop start (same ids -> with_node
idempotency collapses duplicates). The GRAPH stays the sole runtime state store, so
certify's demote counter, the scheduler frontier, and the hollow-pass service guard
keep reading SERVICE nodes from the graph unchanged.

Harness modeled on tests/envstate/test_v3_no_progress_giveup.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.orchestrate.loop.scheduler as gs_module
import src.orchestrate.loop.run as orch
from src.orchestrate.loop import run as orchestrator
from src.orchestrate.loop.scheduler import unsatisfied_provisionable_services
from src.orchestrate.loop.ledger import ActionLedger
from src.orchestrate.loop.world_model import (
    PlannerDecision, Task, initial_map, merge_map,
)
from src.orchestrate.loop.sandbox import InstallResult
from src.agent.loop import RepairOutcome
from graph.model import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from graph.runtime_plan import RuntimePlan, EMPTY_PLAN


class _FakeClient:
    pass


class _RecordingBuildAgent:
    def __init__(self):
        self.client = _FakeClient()
        self.model = "fake-model"

    def run(self, *a, **k):
        raise AssertionError("build_agent.run must never be called by run_v3")

    def propose(self, *a, **k):
        return None


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _service_node(id_="service:redis", name="redis", setup=None):
    setup = setup if setup is not None else {
        "install": ["apt-get install -y redis-server"], "start": "",
        "probe": "redis-cli ping", "createdb": None, "post": [], "bind": [],
    }
    return Node(id=id_, type=NodeType.SERVICE, name=name, layer=Layer.SERVICES,
                discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING,
                check_command="for i in $(seq 1 15); do redis-cli ping && exit 0; sleep 2; done; exit 1",
                data={"setup": setup})


def _obligation_decision(*_a, **_k):
    task = Task(goal="install syslib:x", done_when="ldconfig -p | grep -q x",
                layer="system", facts=(), target_node_ids=("syslib:x",))
    return PlannerDecision(action="task", task=task), "syslib:x"


def _fake_repair_noop(graph, failed_id, bundle, cycle, **kwargs):
    return RepairOutcome(graph=graph, still_failing_id=None, manual_blocks=(),
                         known_invalid=frozenset(), turns_spent=1, budget_exhausted=False)


def _ok_install(script: str) -> InstallResult:
    return InstallResult(rc=0, failing_command=None, lineno=None, stderr="")


_STABLE_FAIL = "FAILED tests/t.py::test_x - RuntimeError\n=== 1 failed in 0.10s ==="


def _map_with_graph(graph):
    base = initial_map(base_image="python:3.11-slim", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, dep_graph=graph)


def _inputs(sandbox_execute, *, runtime_plan, dep_graph=None, max_cycles=1):
    return dict(
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_map_with_graph(dep_graph if dep_graph is not None else DepGraph()),
        ledger=ActionLedger(),
        sandbox_execute=sandbox_execute,
        max_cycles=max_cycles,
        exec_readonly=lambda cmd: (1, ""),
        enable_dep_emit=True,
        reset_to_base=lambda: None,
        run_install_script=_ok_install,
        runtime_plan=runtime_plan,
    )


def _failing_gate(cmd):
    if cmd == orchestrator.VERIFY_TEST_CMD:
        return (False, _STABLE_FAIL)
    return (True, "ok")


# ── loop-start admission ─────────────────────────────────────────────────────

def test_plan_service_admitted_at_loop_start(monkeypatch):
    """A plan's service obligation appears as a SERVICE node in the working graph."""
    monkeypatch.setattr(gs_module, "next_decision", _obligation_decision)
    monkeypatch.setattr(orch, "run_structured_repair", _fake_repair_noop)
    plan = RuntimePlan(service_obligations=(_service_node("service:redis"),))
    final_map, _stop = orchestrator.run_v3(**_inputs(_failing_gate, runtime_plan=plan))
    node = final_map.dep_graph.get("service:redis")
    assert node is not None
    assert node.type is NodeType.SERVICE
    assert node.data["setup"]["probe"] == "redis-cli ping"


def test_plan_service_collapses_with_runtime_discovered_duplicate(monkeypatch):
    """A runtime-discovered service already in the graph with the SAME id must
    collapse with the plan's copy (with_node idempotency) — exactly one node."""
    monkeypatch.setattr(gs_module, "next_decision", _obligation_decision)
    monkeypatch.setattr(orch, "run_structured_repair", _fake_repair_noop)
    # graph already carries a runtime-discovered service:redis (a different instance,
    # same id) before the loop admits the plan's copy.
    pre = DepGraph(nodes=(_service_node("service:redis"),))
    plan = RuntimePlan(service_obligations=(_service_node("service:redis"),))
    final_map, _stop = orchestrator.run_v3(
        **_inputs(_failing_gate, runtime_plan=plan, dep_graph=pre))
    reds = [n for n in final_map.dep_graph.nodes if n.id == "service:redis"]
    assert len(reds) == 1


def test_none_plan_admission_is_noop(monkeypatch):
    """runtime_plan=None (and EMPTY_PLAN) admits nothing — no SERVICE node appears."""
    monkeypatch.setattr(gs_module, "next_decision", _obligation_decision)
    monkeypatch.setattr(orch, "run_structured_repair", _fake_repair_noop)
    for plan in (None, EMPTY_PLAN):
        final_map, _stop = orchestrator.run_v3(**_inputs(_failing_gate, runtime_plan=plan))
        assert not any(n.type is NodeType.SERVICE for n in final_map.dep_graph.nodes)


# ── the admitted service reaches the untouched consumers (demote counter +
#    hollow-pass service guard) — the whole point of the ADMISSION RULE ────────

def test_admitted_service_is_seen_by_hollow_pass_guard():
    """A plan-admitted provisionable service gates 'done' via the (untouched)
    hollow-pass guard — proof the admission puts it where the guard reads it."""
    graph = RuntimePlan(service_obligations=(_service_node(),)).admit_services(DepGraph())
    blocking = unsatisfied_provisionable_services(graph, allow_services=True)
    assert len(blocking) == 1 and blocking[0].id == "service:redis"
    # off by default -> never blocks (byte-identical to pre-service behavior)
    assert unsatisfied_provisionable_services(graph, allow_services=False) == ()


def test_admitted_service_demote_counter_still_works():
    """certify's demote counter (certify_fail_count >= 3 -> out of the gate) reads the
    plan-admitted SERVICE node unchanged."""
    svc = _service_node()
    graph = RuntimePlan(service_obligations=(svc,)).admit_services(DepGraph())
    assert unsatisfied_provisionable_services(graph, allow_services=True)  # under 3 strikes
    demoted = svc.with_data(certify_fail_count=3)
    graph2 = graph.with_node(demoted)
    assert unsatisfied_provisionable_services(graph2, allow_services=True) == ()  # demoted out
