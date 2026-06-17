from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Edge, Node
from src.envstate.contracts.patch import GraphPatch
from src.envstate.contracts.validation import validate_patch


def _base():
    return ContractGraph(
        nodes=(
            Node("contract:a", "Contract", {"level": "atomic"}),
            Node("cmd:005", "CommandExecution", {"command": "pip install torch", "exit_code": 0}),
            Node("failure:1", "Failure", {"kind": "module_not_found"}),
        )
    )


def test_valid_maintainer_patch_passes():
    patch = GraphPatch(
        add_nodes=(Node("transition:install:torch", "Transition", {"kind": "install_python_package"}),),
        add_edges=(Edge("contract:a", "repaired_by", "transition:install:torch"),),
        add_status_events=(),
    )
    assert validate_patch(_base(), patch, scope="maintainer") == []


def test_duplicate_node_id_rejected():
    patch = GraphPatch(add_nodes=(Node("contract:a", "Contract", {"level": "atomic"}),))
    errs = validate_patch(_base(), patch, scope="maintainer")
    assert any("duplicate" in e.lower() for e in errs)


def test_edge_endpoint_must_exist():
    patch = GraphPatch(add_edges=(Edge("contract:a", "repaired_by", "transition:ghost"),))
    errs = validate_patch(_base(), patch, scope="maintainer")
    assert any("endpoint" in e.lower() for e in errs)


def test_edge_type_must_be_valid_for_endpoints():
    patch = GraphPatch(
        add_nodes=(Node("validator:v", "Validator", {}),),
        add_edges=(Edge("contract:a", "declares", "validator:v"),),  # declares is artifact->requirement
    )
    errs = validate_patch(_base(), patch, scope="maintainer")
    assert any("not allowed" in e.lower() for e in errs)


def test_maintainer_may_not_create_host_owned_node():
    patch = GraphPatch(add_nodes=(Node("capability:x", "Capability", {}),))
    errs = validate_patch(_base(), patch, scope="maintainer")
    assert any("host-owned" in e.lower() or "capability" in e.lower() for e in errs)
    # host scope is allowed to:
    assert validate_patch(_base(), patch, scope="host") == []


def test_status_must_be_in_enum():
    patch = GraphPatch(add_status_events=(_event("contract:a", "bogus"),))
    errs = validate_patch(_base(), patch, scope="maintainer")
    assert any("status" in e.lower() for e in errs)


def test_satisfied_requires_passing_evidence():
    # cite a non-passing / missing command -> rejected
    bad = GraphPatch(add_status_events=(_event("contract:a", "satisfied", ("failure:1",)),))
    assert validate_patch(_base(), bad, scope="maintainer")
    # cite a passing CommandExecution -> ok
    good = GraphPatch(add_status_events=(_event("contract:a", "satisfied", ("cmd:005",)),))
    assert validate_patch(_base(), good, scope="maintainer") == []


def test_requirement_needs_declares_edge():
    patch = GraphPatch(add_nodes=(Node("requirement:x", "Requirement", {"subject": "x"}),))
    errs = validate_patch(_base(), patch, scope="host")
    assert any("requirement" in e.lower() and "declares" in e.lower() for e in errs)


def test_transition_must_target_something():
    patch = GraphPatch(add_nodes=(Node("transition:t", "Transition", {}),))
    errs = validate_patch(_base(), patch, scope="maintainer")
    assert any("transition" in e.lower() and "target" in e.lower() for e in errs)


def _event(cid, status, evidence=()):
    from src.envstate.contracts.nodes import ContractStatusEvent

    return ContractStatusEvent(contract_id=cid, status=status, revision_id="envrev:001", evidence_ids=evidence)
