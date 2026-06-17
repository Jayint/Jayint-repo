# tests/test_contracts_validators.py
from src.envstate.contracts.validators import (
    build_import_sweep_command, resolve_import_name, host_satisfied_set, derive_attempt_outcome,
)
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge

def test_resolve_import_name_kept():
    assert resolve_import_name("opencv-python") == "cv2"
    assert resolve_import_name("PyYAML") == "yaml"

def test_import_sweep_command_is_posix_sh_safe_single_call():
    cmd = build_import_sweep_command(["opencv-python", "pyyaml"])
    assert cmd.count("<<") == 1           # single heredoc -> single exec_readonly call
    assert "[[" not in cmd and "pipefail" not in cmd   # no bashisms

def test_host_satisfied_from_import_results():
    g = ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract",
        {"level": "atomic", "kind": "python_import", "subject": "cv2"}),))
    world = type("W", (), {"import_results": (("cv2", True),), "done_flag": False})()
    sat = host_satisfied_set(g, world, ledger_events=[])
    assert "contract:python_import:cv2" in sat

def test_derive_outcome_ok_when_target_satisfied():
    g = ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),
                             Node("attempt:x", "Attempt",
                                  {"created_from_target_node_ids": ["contract:python_import:cv2"]}),))
    out = derive_attempt_outcome(g, "attempt:x", frozenset({"contract:python_import:cv2"}), step_failed=False)
    assert out == "ok"

def test_derive_outcome_ok_but_still_blocked():
    g = ContractGraph(
        nodes=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),
               Node("blocker:b", "Blocker", {"active": True, "signature": "x"}),
               Node("attempt:x", "Attempt",
                    {"created_from_target_node_ids": ["contract:python_import:cv2"]})),
        edges=(Edge("blocker:b", "violates", "contract:python_import:cv2"),))
    out = derive_attempt_outcome(g, "attempt:x", frozenset(), step_failed=False)
    assert out == "ok_but_still_blocked"

def test_derive_outcome_failed_on_step_failure():
    g = ContractGraph(nodes=(Node("attempt:x", "Attempt", {"created_from_target_node_ids": []}),))
    assert derive_attempt_outcome(g, "attempt:x", frozenset(), step_failed=True) == "failed"
