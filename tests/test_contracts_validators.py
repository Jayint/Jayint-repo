# tests/test_contracts_validators.py
from src.envstate.contracts import validators
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node


def _exec_factory(results):
    def _exec(cmd):
        return results.get(cmd, (1, "not found"))
    return _exec


def test_runs_import_validator_and_marks_satisfied():
    g = ContractGraph(nodes=(
        Node("contract:python_package_importable:torch", "Contract",
             {"level": "atomic", "kind": "python_package_importable", "subject": "torch"}),
    ))
    ex = _exec_factory({'python -c "import torch"': (0, "")})
    nodes, edges, events = validators.run_confirmed_validators(g, ex, revision=4)
    assert any(n.type == "Validator" for n in nodes)
    assert any(n.type == "CommandExecution" and n.data["exit_code"] == 0 for n in nodes)
    assert any(e.type == "verified_by" for e in edges)
    ev = next(e for e in events if e.contract_id == "contract:python_package_importable:torch")
    assert ev.status == "satisfied"
    assert any(n.id in ev.evidence_ids for n in nodes if n.type == "CommandExecution")


def test_failing_import_marks_violated():
    g = ContractGraph(nodes=(
        Node("contract:python_package_importable:missing", "Contract",
             {"level": "atomic", "kind": "python_package_importable", "subject": "missing"}),
    ))
    ex = _exec_factory({})  # everything returns rc=1
    _n, _e, events = validators.run_confirmed_validators(g, ex, revision=4)
    assert events[0].status == "violated"


def test_unknown_kind_is_skipped():
    g = ContractGraph(nodes=(Node("contract:x", "Contract", {"level": "atomic", "kind": "mystery"}),))
    nodes, edges, events = validators.run_confirmed_validators(g, _exec_factory({}), revision=4)
    assert nodes == [] and edges == [] and events == []
