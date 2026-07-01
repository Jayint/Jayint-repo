"""Scenario (Task 8c): the discover gate fails with ``ModuleNotFoundError:
requests``; the NEXT cycle's ``_runtime_ingest_phase`` (real, unmocked) adds a
``pkg:requests`` node from that failure; a later cycle's scheduler targets it as
an obligation and typed repair installs it; the host certifies it SATISFIED.

``run_structured_repair`` IS mocked here — see "Why mocked" below — everything
else (the discover gate, the runtime-ingest classifier/graph mutation, the
scheduler's target selection, the fresh-replay certify, and every tracer record
point) is real, unmocked ``run_v3`` code.

Why mocked
----------
A version-less, runtime-discovered PACKAGE node is never "reciped" (
``python_deps.depgraph.emit._is_reciped`` requires ``node.version`` for PACKAGE),
so it is excluded from the deterministic install script entirely — the ONLY way
to install it is a governed ``ScriptPatch`` (manual block), which
``validate_proposal`` requires to cite an ``evidence_ref`` present in
``known_evidence_ids``. The task branch's FIRST targeted-repair attempt on a
node with no existing manual block constructs the evidence bundle as a literal
``EvidenceBundle()`` (see ``orchestrator._repair_or_route``'s "no manual block
targets this node yet" call site) — i.e. ``known_evidence_ids`` is structurally
EMPTY on that first attempt, so no ``add_requirements``/``script_patches``
proposal can ever validate. A real ``propose``/``admit_proposal`` round trip is
therefore not reachable for THIS specific node shape without a bigger change to
``_repair_or_route``'s bundle plumbing, which is out of scope here (the brief
disallows changing the repair-routing logic). This is the SAME reason every
existing orchestrator-level test that reaches typed repair for a fresh
obligation (``tests/envstate/test_v3_repair_wiring.py``,
``tests/envstate/test_v3_task_branch.py``, ``tests/envstate/test_repair_routing.py``)
already mocks ``run_structured_repair`` at the orchestrator boundary — this
scenario follows that established, non-hollow convention rather than inventing
a new one. See the Task 8 Part-2 report for the fuller trace of this
constraint.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.envstate.orchestrator as orch
from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.repair_loop import RepairOutcome
from src.envstate.run_trace import RunTracer
from src.envstate.trace_verify import verify_canonical_trace
from src.envstate.world_model import initial_map
from src.sandbox import InstallResult
from python_deps.depgraph.block import Block
from python_deps.depgraph.schema import DepGraph, State


class _FakeClient:
    """Non-None sentinel for the ``getattr(build_agent, "client", None)`` guard."""


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _empty_dep_graph_map():
    return initial_map(base_image="python:3.11-slim", workdir="/repo", language="python",
                       build_system="pip", repo_layout=(), dep_graph=DepGraph())


def test_missing_external_pkg_discovered_and_installed():
    state = {"installed": False}
    fail_text = "E   ModuleNotFoundError: No module named 'requests'"

    def sandbox_execute(cmd):
        if cmd == orchestrator.VERIFY_TEST_CMD:
            return (True, "1 passed in 0.01s") if state["installed"] else (False, fail_text)
        return (True, "ok")

    def exec_readonly(cmd):
        if "import requests" in cmd:
            return (0, "") if state["installed"] else (1, "ModuleNotFoundError")
        return (1, "")

    def reset_to_base():
        pass

    def run_install_script(script):
        return InstallResult(rc=0, failing_command=None, lineno=None, stderr="")

    class _Agent:
        client = _FakeClient()
        model = "fake-model"

        def propose(self, scope, exec_readonly=None, **kwargs):
            raise AssertionError(
                "build_agent.propose must not be called — run_structured_repair "
                "is mocked in this scenario (see module docstring)"
            )

    repair_calls: list[str] = []

    def _fake_repair(graph, failed_id, bundle, cycle, *, manual_blocks=(), **kwargs):
        """Simulates a SUCCESSFUL typed repair: admits a manual pip-install block
        targeting the real discovered node id, and marks the package installed so
        the host's own exec_readonly/run_install_script fakes (not this mock)
        certify it SATISFIED on the next real certify pass — the mock does NOT
        set node.state directly (host stays the sole SATISFIED writer)."""
        repair_calls.append(failed_id)
        block = Block(
            block_id="pip.manual-requests", wave="pip",
            commands=("python3 -m pip install --break-system-packages requests",),
            target_node_ids=(failed_id,),
            check_commands=("python3 -c \"import requests\"",),
        )
        state["installed"] = True
        return RepairOutcome(
            graph=graph, still_failing_id=None,
            manual_blocks=manual_blocks + (block,),
            known_invalid=frozenset(), turns_spent=1, budget_exhausted=False,
        )

    tracer = RunTracer(repo="scenario/missing-external-pkg")
    captured: dict = {}

    def gate_observer(gates):
        installability, testability = gates
        captured["installability"] = dataclasses.asdict(installability)
        captured["testability"] = dataclasses.asdict(testability)

    with mock.patch.object(orch, "run_structured_repair", _fake_repair):
        final_map, stop = orchestrator.run_v3(
            build_agent=_Agent(),
            maintainer=_NoopMaintainer(),
            initial_world_map=_empty_dep_graph_map(),
            ledger=ActionLedger(),
            sandbox_execute=sandbox_execute,
            max_cycles=6,
            exec_readonly=exec_readonly,
            enable_dep_emit=True,
            reset_to_base=reset_to_base,
            run_install_script=run_install_script,
            enable_gate_observability=True,
            gate_observer=gate_observer,
            tracer=tracer,
        )
    trace = tracer.snapshot(stop_reason=stop, gates=captured)

    # --- the discover gate + real runtime-ingest actually added the node -----
    assert repair_calls == ["pkg:requests"], (
        "typed repair should have been invoked exactly once, targeting the "
        "REAL node id the runtime-ingest classifier discovered from the "
        "ModuleNotFoundError traceback"
    )
    node = final_map.dep_graph.get("pkg:requests")
    assert node is not None, "runtime ingest must have appended pkg:requests to the graph"
    assert node.state is State.SATISFIED, "host must certify pkg:requests SATISFIED"

    # --- trace: a real discover cycle recorded the node being added ----------
    discover_with_new_node = [d for d in trace.discover if "pkg:requests" in d.new_node_ids]
    assert len(discover_with_new_node) == 1, (
        f"expected exactly one discover cycle to report pkg:requests as newly "
        f"added; got {trace.discover}"
    )
    assert discover_with_new_node[0].used_llm_mutation is False
    assert discover_with_new_node[0].command == orchestrator.VERIFY_TEST_CMD

    # --- trace: the (mocked) typed repair was recorded, with the manual block id
    assert len(trace.patchgate) == 1
    assert trace.patchgate[0].accepted is True
    assert "pip.manual-requests" in trace.patchgate[0].accepted_block_ids

    assert stop == "planner_done"
    assert verify_canonical_trace(trace) == []
