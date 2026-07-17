import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.envstate.repair_scope as rs
from python_deps.depgraph.block import Block
from python_deps.depgraph.evidence_log import Evidence, EvidenceBundle

def _bundle():
    return EvidenceBundle().with_item(Evidence(
        evidence_id="ev.1.0", container_kind="canonical",
        command="apt-get install -y libplacebodev", rc=1,
        output_excerpt="Unable to locate package libplacebodev", cycle=1,
        block_id="system.libplacebo", node_id="syslib:libplacebo"))

def test_build_scope_carries_failure_and_evidence(monkeypatch):
    monkeypatch.setattr(rs, "build_requirement_slice", lambda g, n: object())
    monkeypatch.setattr(rs, "render_requirement_slice",
                        lambda s: ("target: syslib:libplacebo",))
    fb = Block(block_id="system.libplacebo", wave="system",
               commands=("apt-get install -y libplacebodev",),
               target_node_ids=("syslib:libplacebo",))
    scope = rs.build_repair_scope(
        object(), target_node_id="syslib:libplacebo", failed_block=fb, bundle=_bundle(),
        known_invalid=("apt:libplacebodev",), constraints={"package_manager": "apt"})
    assert scope.failed_command == "apt-get install -y libplacebodev"
    assert "libplacebodev" in scope.failed_output
    assert "ev.1.0" in scope.known_evidence_ids
    assert scope.constraints == (("package_manager", "apt"),)
    assert scope.failed_block_id == "system.libplacebo"
    assert scope.failed_block_wave == "system"
    assert scope.failed_block_commands == ("apt-get install -y libplacebodev",)
    assert scope.failed_block_targets == ("syslib:libplacebo",)
    rendered = rs.render_repair_scope(scope)
    assert "Failed execution block:" in rendered
    assert "id=system.libplacebo" in rendered

def test_build_scope_tolerates_none_bundle():
    # Binding-install repair passes bundle=None (no obligation packet — failure evidence is the
    # install stderr, surfaced separately). build_repair_scope must not crash on bundle.items.
    scope = rs.build_repair_scope(
        object(), target_node_id=None, failed_block=None, bundle=None)
    assert scope.known_evidence_ids == frozenset()
    assert scope.failed_command is None


def test_render_surfaces_avoidlist_and_schema(monkeypatch):
    monkeypatch.setattr(rs, "build_requirement_slice", lambda g, n: object())
    monkeypatch.setattr(rs, "render_requirement_slice", lambda s: ("CHECK: pkg-config",))
    scope = rs.build_repair_scope(
        object(), target_node_id="syslib:libplacebo", failed_block=None, bundle=_bundle(),
        known_invalid=("apt:libplacebodev",), constraints=None)
    text = rs.render_repair_scope(scope)
    assert "apt:libplacebodev" in text          # avoid-list
    assert "CHECK: pkg-config" in text           # slice lines
    assert "add_providers" in text               # schema hint
    assert "Failed execution block:" not in text  # no failed block was supplied


def test_scope_carries_adjacent_manifest_constraint_and_target_python():
    from python_deps.depgraph.schema import (
        DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType,
    )

    imp = Node(
        id="import:fakeredis", type=NodeType.IMPORT, name="fakeredis",
        layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
    )
    pkg = Node(
        id="pkg:fakeredis", type=NodeType.PACKAGE, name="fakeredis",
        layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
        resolved_python="3.11", declared_specifier=">=2.26",
        manifest_source="pyproject.toml:project.dependencies",
        resolution_status="failed", resolution_error="cutoff excluded candidates",
    )
    graph = DepGraph().with_node(imp).with_node(pkg).with_edge(
        Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES)
    )
    scope = rs.build_repair_scope(
        graph, target_node_id=imp.id, failed_block=None, bundle=None
    )
    assert scope.target_python == "3.11"
    assert scope.manifest_requirements == ((
        "pkg:fakeredis", ">=2.26", "", "pyproject.toml:project.dependencies",
    ),)
    rendered = rs.render_repair_scope(scope)
    assert "Target Python: 3.11" in rendered
    assert "pkg:fakeredis >=2.26" in rendered
    assert "Resolution status: failed" in rendered


def test_scope_exposes_exact_curated_service_recipe():
    from python_deps.depgraph.schema import (
        DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType,
    )

    redis = Node(
        id="service:redis", type=NodeType.SERVICE, name="redis",
        layer=Layer.SERVICES, discovered_by=DiscoveredBy.RUNTIME,
        check_command="redis-cli ping",
        data={
            "start_recipe": {
                "system_package": "redis-server",
                "start": "redis-server --daemonize yes",
            },
        },
    )
    system_package = Node(
        id="syslib:redis-server", type=NodeType.SYSTEM_LIB, name="redis-server",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RUNTIME,
        check_command="command -v redis-server",
    )
    graph = DepGraph().with_node(redis).with_node(system_package).with_edge(
        Edge(src=redis.id, dst=system_package.id, relation=EdgeType.REQUIRES)
    )

    scope = rs.build_repair_scope(
        graph, target_node_id=redis.id, failed_block=None, bundle=None
    )
    rendered = rs.render_repair_scope(scope, structured_actions=True)

    assert "curated service package: redis-server" in rendered
    assert "curated service start (use exactly): redis-server --daemonize yes" in rendered
    assert "services-wave script block" in rendered
