from src.envstate.contracts import projection
from src.envstate.ledger import ActionEvent
from src.envstate.world_model import Fact


def _ev(step, cmd, rc, before, after):
    return ActionEvent(step=step, cmd=cmd, rc=rc, stdout="ok", env_revision_before=before, env_revision_after=after)


def test_command_execution_nodes():
    nodes = projection.project_command_executions([_ev(5, "pip install torch", 0, 3, 4)])
    n = nodes[0]
    assert n.id == "cmd:005" and n.type == "CommandExecution"
    assert n.data["exit_code"] == 0 and n.data["command"] == "pip install torch"
    assert n.data["revision_before"] == "envrev:003" and n.data["revision_after"] == "envrev:004"


def test_environment_revisions_and_creates_edge():
    nodes, edges = projection.project_environment_revisions([_ev(5, "pip install torch", 0, 3, 4)])
    assert any(n.id == "envrev:004" and n.type == "EnvironmentRevision" for n in nodes)
    assert any(e.source == "cmd:005" and e.type == "creates_revision" and e.target == "envrev:004" for e in edges)


def test_no_revision_node_when_revision_unchanged():
    nodes, edges = projection.project_environment_revisions([_ev(5, "pytest -q", 0, 4, 4)])
    assert nodes == [] and edges == []


def test_capabilities_from_installed_facts_at_current_revision():
    nodes = projection.project_capabilities((Fact("torch", "2.1.0"),), (Fact("libpq-dev", ""),), current_revision=4)
    ids_ = {n.id for n in nodes}
    assert "capability:python_package_importable:torch@envrev:004" in ids_
    assert any(n.type == "Capability" and n.data["subject"] == "torch" for n in nodes)
