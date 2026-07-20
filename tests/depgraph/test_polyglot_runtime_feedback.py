import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from python_deps.depgraph.ids import TEST_NODE_ID, dependency_set_id
from python_deps.depgraph.runtime_classify import classify_observation
from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Ecosystem,
    Layer,
    Node,
    NodeType,
    State,
)


@pytest.mark.parametrize(
    ("command", "output", "ecosystem", "manager", "workspace"),
    (
        (
            "cd frontend && npm test",
            "Error: Cannot find module 'react-dom'",
            Ecosystem.NPM,
            "npm",
            "frontend",
        ),
        (
            "cd native && cargo test",
            "error: no matching package named `openssl-sys` found",
            Ecosystem.CARGO,
            "cargo",
            "native",
        ),
        (
            "cd backend && go test ./...",
            "missing go.sum entry for module providing package example.com/lib",
            Ecosystem.GO_MODULE,
            "go",
            "backend",
        ),
        (
            "cd server && mvn -B test",
            "Could not find artifact com.example:demo:jar:1.0",
            Ecosystem.MAVEN,
            "maven",
            "server",
        ),
        (
            "cd server && ./gradlew test",
            "Could not find com.example:demo:1.0",
            Ecosystem.GRADLE,
            "gradle",
            "server",
        ),
    ),
)
def test_polyglot_failure_locates_workspace_dependency_set(
    command,
    output,
    ecosystem,
    manager,
    workspace,
):
    discovery = classify_observation(command, output)

    assert discovery is not None
    assert discovery.node_type is NodeType.DEPENDENCY_SET
    assert discovery.ecosystem is ecosystem
    assert discovery.package_manager == manager
    assert discovery.workspace == workspace


def test_runtime_dependency_failure_reopens_satisfied_transaction():
    deps_id = dependency_set_id(Ecosystem.NPM, "frontend")
    graph = DepGraph().with_node(Node(
        id=TEST_NODE_ID,
        type=NodeType.TEST,
        name="tests",
        layer=Layer.TESTS,
        discovered_by=DiscoveredBy.GOAL,
    )).with_node(Node(
        id=deps_id,
        type=NodeType.DEPENDENCY_SET,
        name="npm dependencies (frontend)",
        layer=Layer.DEPENDENCIES,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.SATISFIED,
        certified_cycle=3,
        ecosystem=Ecosystem.NPM,
        workspace="frontend",
        package_manager="npm",
    ))

    updated, discoveries = ingest_runtime_failures(
        graph,
        [(
            "cd frontend && npm test",
            "Error: Cannot find module 'react-dom'",
        )],
    )

    assert len(discoveries) == 1
    assert updated.get(deps_id).state is State.MISSING
    assert updated.get(deps_id).certified_cycle is None
    assert updated.get(deps_id).data["missing_dependency"] == "react-dom"
    assert len([
        node for node in updated.nodes
        if node.type is NodeType.DEPENDENCY_SET
    ]) == 1
