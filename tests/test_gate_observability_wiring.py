import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate import orchestrator
from src.envstate.gates import GateResult
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import TaskReport, initial_map, merge_map
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


def _minimal_build_agent():
    class _Agent:
        def run(self, task, sandbox_execute, exec_readonly, ledger, *, cycle, **kw):
            return TaskReport("t", "blocked", (), "")
        def propose(self, *a, **kw):
            return None
        def run_recipe(self, *a, **kw):
            return TaskReport("r", "done", (), "")
    return _Agent()


def _minimal_maintainer():
    class _M:
        def update(self, world_map, task_report, *a, **kw):
            return world_map
    return _M()


def _syslib_map():
    syslib = Node(
        id="syslib:libfoo.so", type=NodeType.SYSTEM_LIB, name="libfoo.so",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED,
        check_command="ldconfig -p | grep -q libfoo", chosen_fix="apt:libfoo-dev",
    )
    base = initial_map(
        base_image="python:3.11",
        workdir="/repo",
        language="python",
        build_system="pip",
        repo_layout=(),
    )
    return merge_map(base, dep_graph=DepGraph().with_node(syslib))


def _noop_sandbox(cmd):
    return (False, "")


def _noop_ro(cmd):
    # keep the seed syslib SATISFIED on re-certify; else rc=1 (avoids emit/repair noise)
    if "libfoo" in cmd:
        return (0, "libfoo")
    return (1, "")


def _run(**kw):
    return orchestrator.run_v3(
        _minimal_build_agent(), _minimal_maintainer(), _syslib_map(),
        ActionLedger(), _noop_sandbox, max_cycles=1, exec_readonly=_noop_ro,
        enable_dep_emit=True, enable_script_materialization=True, **kw,
    )


def test_flag_off_does_not_call_observer():
    seen = []
    _run(gate_observer=lambda gates: seen.append(gates))  # flag defaults off
    assert seen == []


def test_flag_off_byte_identical_result():
    base_map, base_reason = _run()
    off_map, off_reason = _run(enable_gate_observability=False,
                               gate_observer=lambda g: None)
    assert base_reason == off_reason
    assert base_map.dep_graph == off_map.dep_graph


def test_flag_on_calls_observer_with_two_gates():
    seen = []
    _run(enable_gate_observability=True, gate_observer=lambda gates: seen.append(gates))
    assert len(seen) == 1
    gates = seen[0]
    assert len(gates) == 2
    assert all(isinstance(g, GateResult) for g in gates)
    assert gates[0].name == "installability"
    assert gates[1].name == "testability"
    # seed syslib SATISFIED -> installability provisional pass; sandbox fails -> testability fail
    assert gates[0].passed is True
    assert gates[1].passed is False


def test_flag_on_without_observer_does_not_crash():
    _run(enable_gate_observability=True)
