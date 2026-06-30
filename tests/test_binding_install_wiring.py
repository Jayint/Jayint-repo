import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import TaskReport, initial_map, merge_map
from src.sandbox import InstallResult
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


def test_install_evidence_carries_block_id_for_block_failure():
    # A #@block install failure: localize_install_failure returns a BLOCK id. The evidence must
    # set block_id (not only node_id) so build_repair_scope — which matches install stderr by
    # ev.block_id == failed_block.block_id — still surfaces the stderr on the follow-up repair.
    from src.envstate.repair_scope import build_repair_scope
    from python_deps.depgraph.block import Block

    bundle = orchestrator._build_install_evidence(
        InstallResult(1, "pip install badpkg", 7, "ERROR: no matching distribution"),
        "pip.badpkg", 2)
    ev = bundle.items[0]
    assert ev.node_id == "pip.badpkg" and ev.block_id == "pip.badpkg"
    assert "no matching distribution" in ev.output_excerpt
    assert ev.evidence_id == "install.2.pip.badpkg"

    # End-to-end: when the failure maps to a block, the scope must carry the stderr + evidence.
    fb = Block(block_id="pip.badpkg", wave="pip", commands=("pip install badpkg",),
               target_node_ids=("pkg:badpkg",))
    scope = build_repair_scope(object(), target_node_id=None, failed_block=fb, bundle=bundle)
    assert "no matching distribution" in scope.failed_output
    assert "install.2.pip.badpkg" in scope.known_evidence_ids


def _agent():
    class _A:
        client = None
        def run(self, *a, **k): return TaskReport("t", "blocked", (), "")
        def propose(self, *a, **k): return None
        def run_recipe(self, *a, **k): return TaskReport("r", "done", (), "")
    return _A()


def _maint():
    class _M:
        def update(self, wm, *a, **k): return wm
    return _M()


def _map():
    syslib = Node(id="syslib:libgl1", type=NodeType.SYSTEM_LIB, name="libgl1",
                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED,
                  check_command="dpkg -s libgl1", chosen_fix="apt:libgl1")
    base = initial_map(base_image="python:3.11", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, dep_graph=DepGraph().with_node(syslib))


def _ro(cmd):
    return (0, "ok") if "libgl1" in cmd else (1, "")


def _run(**kw):
    return orchestrator.run_v3(
        _agent(), _maint(), _map(), ActionLedger(), lambda c: (False, ""),
        max_cycles=1, exec_readonly=_ro, enable_dep_emit=True,
        enable_script_materialization=True, **kw,
    )


def test_flag_off_byte_identical():
    base_map, base_reason = _run()
    off_map, off_reason = _run(enable_binding_install=False,
                               reset_to_base=lambda: None,
                               run_install_script=lambda s: InstallResult(0, None, None, ""))
    assert base_reason == off_reason
    assert base_map.dep_graph == off_map.dep_graph


def test_flag_on_runs_install_then_certifies():
    calls = []
    def reset(): calls.append("reset")
    def install(script):
        calls.append("install")
        assert "#@node" in script  # render_build_script output was passed
        return InstallResult(0, None, None, "")
    _run(enable_binding_install=True, reset_to_base=reset, run_install_script=install)
    assert calls == ["reset", "install"]   # binding path used, block_emit not


def test_flag_on_install_failure_does_not_crash():
    def install(script):
        return InstallResult(1, "apt-get install -y libgl1", 5, "E: not found")
    # build_agent.client is None → no repair; the phase must complete without raising
    _run(enable_binding_install=True, reset_to_base=lambda: None, run_install_script=install)


def test_flag_on_requires_callables():
    import pytest
    # reset_to_base=None should raise
    with pytest.raises(ValueError):
        _run(enable_binding_install=True, reset_to_base=None,
             run_install_script=lambda s: InstallResult(0, None, None, ""))
    # run_install_script=None should raise
    with pytest.raises(ValueError):
        _run(enable_binding_install=True, reset_to_base=lambda: None,
             run_install_script=None)


def test_flag_on_missing_check_command_raises():
    import pytest
    bad_pkg = Node(id="pkg:foo==1.0", type=NodeType.PACKAGE, name="foo", version="1.0",
                   layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                   chosen_fix="pip:foo")  # no check_command → _is_reciped but uncertifiable
    base = initial_map(base_image="python:3.11", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    wm = merge_map(base, dep_graph=DepGraph().with_node(bad_pkg))
    with pytest.raises(ValueError):
        orchestrator.run_v3(
            _agent(), _maint(), wm, ActionLedger(), lambda c: (False, ""),
            max_cycles=1, exec_readonly=_ro, enable_dep_emit=True,
            enable_script_materialization=True, enable_binding_install=True,
            reset_to_base=lambda: None,
            run_install_script=lambda s: InstallResult(0, None, None, ""),
        )


def test_flag_on_install_failure_with_client_enters_repair():
    """Repair must fire even when localize() returns node_id=None (Fix 3), AND the REAL
    repair path must not crash: run_structured_repair → build_repair_scope is called with
    bundle=None on the binding path, which build_repair_scope now tolerates.
    """
    counter = [0]
    scopes = []

    class _AgentWithClient:
        client = object()  # non-None → repair branch activates

        def run(self, *a, **k):
            return TaskReport("t", "blocked", (), "")

        def propose(self, *a, **k):
            counter[0] += 1
            scopes.append(a[0] if a else None)   # capture the RepairScope passed to propose
            return None      # admit nothing → real repair loop ends after one propose

        def run_recipe(self, *a, **k):
            return TaskReport("r", "done", (), "")

    # Syslib node: reciped (chosen_fix starts with "apt:"), has check_command, starts MISSING
    syslib = Node(id="syslib:libgl1", type=NodeType.SYSTEM_LIB, name="libgl1",
                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                  check_command="dpkg -s libgl1", chosen_fix="apt:libgl1")
    base = initial_map(base_image="python:3.11", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    wm = merge_map(base, dep_graph=DepGraph().with_node(syslib))

    # exec_readonly always fails → node stays MISSING after certify → appears in _unsat.
    # Install fails with a command NOT in the rendered script → localize returns node_id=None;
    # the _unsat fallback still yields a failed_node, so the REAL repair loop engages and
    # build_repair_scope is exercised with bundle=None (no patching of run_structured_repair).
    orchestrator.run_v3(
        _AgentWithClient(), _maint(), wm, ActionLedger(), lambda c: (False, ""),
        max_cycles=1, exec_readonly=lambda cmd: (1, ""),
        enable_dep_emit=True, enable_script_materialization=True,
        enable_binding_install=True, reset_to_base=lambda: None,
        run_install_script=lambda s: InstallResult(1, "totally-unmapped-command-xyz", 3, "E: boom"),
    )
    # Fix 3: localize returned None but _unsat fallback provided failed_node → repair engaged.
    assert counter[0] >= 1
    # Fix (re-review): the node id is passed as target_hint so the scope resolves the
    # unsatisfied node even though it is not a block id — scope must carry the node context.
    assert scopes and getattr(scopes[0], "target_node_id", None) == "syslib:libgl1"
    # Final-review fix: the install stderr must reach the proposer as failure output, and a
    # citable evidence id must exist (PatchGate requires every proposal to cite known evidence).
    assert "boom" in scopes[0].failed_output
    assert scopes[0].known_evidence_ids


def test_flag_on_requires_script_materialization():
    import pytest
    with pytest.raises(ValueError):
        orchestrator.run_v3(
            _agent(), _maint(), _map(), ActionLedger(), lambda c: (False, ""),
            max_cycles=1, exec_readonly=_ro, enable_dep_emit=True,
            enable_script_materialization=False,   # contradictory with binding-install
            enable_binding_install=True,
            reset_to_base=lambda: None,
            run_install_script=lambda s: InstallResult(0, None, None, ""),
        )
