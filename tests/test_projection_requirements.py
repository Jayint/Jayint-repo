from src.envstate.contracts import projection
from src.envstate.world_model import Fact


def test_repo_artifacts_only_for_known_manifest_files():
    nodes = projection.project_repo_artifacts(("src/", "tests/", "requirements.txt", "pyproject.toml", "README.md"))
    paths = {n.data["path"] for n in nodes}
    assert paths == {"requirements.txt", "pyproject.toml"}
    assert all(n.type == "RepoArtifact" for n in nodes)


def test_requirements_get_declares_edge_from_manifest_artifact():
    artifacts = projection.project_repo_artifacts(("requirements.txt",))
    nodes, edges = projection.project_requirements((Fact("torch", ">=2.0"), Fact("flask", "")), artifacts)
    rid = "requirement:python_dependency:torch"
    assert any(n.id == rid and n.data["subject"] == "torch" and n.data["spec"] == ">=2.0" for n in nodes)
    assert any(e.source == "artifact:requirements.txt" and e.type == "declares" and e.target == rid for e in edges)


def test_requirements_with_no_manifest_artifact_emit_no_edges():
    nodes, edges = projection.project_requirements((Fact("torch", ""),), ())
    assert nodes == [] and edges == []  # ungrounded requirement is dropped (spec forbids it)
