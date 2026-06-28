# tests/test_deterministic_maintainer.py
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))  # shim: import python_deps/src.envstate

from src.envstate.deterministic_maintainer import build_blocker_patch, maintain, DeterministicMaintainer
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.apply import apply_patch
from src.envstate.contracts.patch import GraphPatch
from src.envstate.contracts.ids import contract_id, blocker_id
from src.envstate.world_model import TaskReport, CommandRecord, derive_open_problems, initial_map
from src.envstate.contracts.projection import _auto_resolve_blockers


def _report(cmd, rc, output, learning=""):
    return TaskReport("t", "blocked", (CommandRecord(cmd, rc, output),), learning)


def test_pg_config_failure_builds_system_layer_blocker_and_contract():
    report = _report("pip install psycopg2", 1, "Error: pg_config: command not found")
    patch = build_blocker_patch(ContractGraph.empty(), report)
    g = apply_patch(ContractGraph.empty(), patch)

    c = g.node(contract_id("binary", "pg_config"))
    assert c is not None
    assert c.data["subject"] == "pg_config"      # verbatim, not paraphrased
    assert c.data["layer"] == "system"
    assert c.data["level"] == "atomic"

    b = g.node(blocker_id("pg_config: command not found"))
    assert b is not None
    assert b.data["layer"] == "system"           # explicit — the bug was "deps"
    assert b.data["active"] is True
    assert "command not found" in b.data["signature"]   # verbatim


def test_emitted_blocker_retires_via_existing_auto_resolve():
    # THE correctness test: after apt install lands pg_config in `present`,
    # the existing _auto_resolve_blockers must retire the blocker.
    report = _report("pip install psycopg2", 1, "Error: pg_config: command not found")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    updated, satisfied = _auto_resolve_blockers(g, present={"pg-config"}, collection_ok=False)
    assert contract_id("binary", "pg_config") in satisfied   # contract now satisfied
    assert any(not n.data.get("active", True) for n in updated)  # blocker retired


def test_emitted_blocker_populates_open_problems_with_system_layer():
    report = _report("pip install psycopg2", 1, "Error: pg_config: command not found")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    problems = derive_open_problems(g)
    assert any(p.layer == "system" and "command not found" in p.signature for p in problems)


def test_soname_failure_is_system_layer():
    report = _report("python -c 'import cv2'", 1,
                     "ImportError: libGL.so.1: cannot open shared object file")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    c = g.node(contract_id("system_library", "libGL.so.1"))
    assert c is not None and c.data["layer"] == "system"


def test_module_not_found_is_deps_layer():
    report = _report("pytest", 1, "ModuleNotFoundError: No module named 'requests'")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    c = g.node(contract_id("python_import", "requests"))
    assert c is not None and c.data["layer"] == "deps"   # deps is correct for pip imports


def test_idempotent_existing_nodes_skipped():
    report = _report("x", 1, "pg_config: command not found")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    patch2 = build_blocker_patch(g, report)   # same failure, graph already has the nodes
    assert patch2.add_contracts == () and patch2.add_blockers == ()


def test_learning_preserved_as_diagnostic_note():
    report = _report("x", 1, "pg_config: command not found", learning="psycopg2 needs libpq-dev")
    patch = build_blocker_patch(ContractGraph.empty(), report)
    assert any("psycopg2 needs libpq-dev" in n for n in patch.diagnostic_notes)


def test_no_signature_no_blockers():
    report = _report("echo ok", 0, "all good")
    patch = build_blocker_patch(ContractGraph.empty(), report)
    assert patch.add_blockers == () and patch.add_contracts == ()


def test_partial_state_emits_blocker_for_existing_contract():
    report = _report("x", 1, "pg_config: command not found")
    # graph already has the contract but no blocker
    only_contract = build_blocker_patch(ContractGraph.empty(), report)
    contract_node = next(n for n in only_contract.add_contracts)
    g = apply_patch(ContractGraph.empty(), GraphPatch(add_contracts=(contract_node,)))
    patch2 = build_blocker_patch(g, report)
    assert patch2.add_contracts == ()                # contract already present -> skipped
    assert len(patch2.add_blockers) == 1             # blocker still emitted
    assert len(patch2.add_edges) == 1                # violates edge emitted
    assert patch2.add_edges[0].type == "violates"


# ---------------------------------------------------------------------------
# Task 2: maintain() + DeterministicMaintainer
# ---------------------------------------------------------------------------


def _base_map():
    return initial_map(base_image="python:3.11", workdir="/repo", language="python 3.11",
                       build_system="pip", repo_layout=("tests/",))


def test_maintain_adds_blocker_to_contract_graph():
    m = _base_map()
    out = maintain(m, _report("x", 1, "pg_config: command not found"))
    assert out.contract_graph.node(contract_id("binary", "pg_config")) is not None


def test_maintain_passes_through_done_gate():
    # A real passing pytest run flips done_flag via _verified_test_run_passed.
    m = _base_map()
    passing = TaskReport("t", "done",
        (CommandRecord("python -m pytest -q", 0, "5 passed in 0.1s"),), "")
    out = maintain(m, passing)
    assert out.done_flag is True


def test_maintain_does_not_touch_owned_fields():
    m = _base_map()
    out = maintain(m, _report("x", 1, "pg_config: command not found"))
    for f in ("installed", "required", "env", "system_installed", "base_image",
              "workdir", "language", "build_system", "repo_layout", "dep_advisory", "dep_graph"):
        assert getattr(out, f) == getattr(m, f)


def test_adapter_update_is_drop_in():
    m = _base_map()
    out = DeterministicMaintainer().update(m, _report("x", 1, "pg_config: command not found"))
    assert out.contract_graph.node(contract_id("binary", "pg_config")) is not None


def test_env_flag_recognized():
    from src.envstate.deterministic_maintainer import DeterministicMaintainer
    # The wiring contract: when the flag is on, the Maintainer object exposes the
    # deterministic .update. We assert the adapter is usable as a Maintainer stand-in.
    assert hasattr(DeterministicMaintainer(), "update")


# ---------------------------------------------------------------------------
# Finding 1: rc filter — rc=0 commands must not produce blockers
# ---------------------------------------------------------------------------


def test_passing_command_output_does_not_produce_blockers():
    # An rc=0 command whose output merely *mentions* a failure-shaped line must
    # NOT create a blocker (parity with projection._failure_signatures rc!=0 filter).
    report = _report("pytest -q", 0, "test_help PASSED: 'pg_config: command not found' handled")
    patch = build_blocker_patch(ContractGraph.empty(), report)
    assert patch.add_blockers == () and patch.add_contracts == ()


def test_only_failing_command_in_mixed_report_yields_blocker():
    # Mixed report: one passing command + one failing command. Only the failing
    # command's signature becomes a blocker.
    report = TaskReport("t", "blocked", (
        CommandRecord("pip install foo", 0, "Successfully installed foo"),
        CommandRecord("pip install psycopg2", 1, "Error: pg_config: command not found"),
    ), "")
    patch = build_blocker_patch(ContractGraph.empty(), report)
    assert len(patch.add_blockers) == 1
    assert len(patch.add_contracts) == 1
    assert patch.add_blockers[0].data["layer"] == "system"


# ---------------------------------------------------------------------------
# Task 2 (C1): v3_only param + _v3_done_gate
# ---------------------------------------------------------------------------


def _world_map_with_blockers():
    """A WorldModelMap with a non-empty contract_graph (built by maintain())."""
    return maintain(
        _base_map(),
        _report("pip install psycopg2", 1, "Error: pg_config: command not found"),
    )


def test_v3_only_maintainer_does_not_write_contract_graph():
    m = DeterministicMaintainer(v3_only=True)
    base = _world_map_with_blockers()  # map with a non-empty contract_graph baseline
    report = _report("pytest", 1, "ModuleNotFoundError: No module named 'foo'")
    out = m.update(base, report)
    assert out.contract_graph == base.contract_graph  # unchanged — no blocker write in v3


def test_v3_only_maintainer_sets_done_flag_on_verified_pass():
    m = DeterministicMaintainer(v3_only=True)
    base = _base_map()
    report = TaskReport("t", "done", (CommandRecord("python -m pytest -q", 0, "3 passed"),), "")
    out = m.update(base, report)
    assert out.done_flag is True


def test_default_maintainer_still_writes_blockers():
    m = DeterministicMaintainer()  # v3_only defaults False → v1 behavior preserved
    base = _base_map()
    report = _report("pytest", 1, "ModuleNotFoundError: No module named 'bar'")
    out = m.update(base, report)
    assert out.contract_graph.nodes  # blocker written (v1 path unchanged)
