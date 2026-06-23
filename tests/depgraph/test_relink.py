"""Unit tests for certified import->package relink (no Docker/network)."""

from __future__ import annotations

from python_deps.depgraph.relink import (
    PACKAGES_DIST_CMD,
    parse_packages_distributions,
)


def test_parse_valid_map():
    stdout = '{"cv2": ["opencv-python"], "yaml": ["PyYAML"], "google": ["google-auth", "protobuf"]}'
    out = parse_packages_distributions(stdout)
    assert out["cv2"] == ["opencv-python"]
    assert out["google"] == ["google-auth", "protobuf"]


def test_parse_malformed_returns_empty():
    assert parse_packages_distributions("not json") == {}
    assert parse_packages_distributions("") == {}
    assert parse_packages_distributions("[1, 2, 3]") == {}


def test_command_is_stdlib_only():
    assert "packages_distributions" in PACKAGES_DIST_CMD
    assert "importlib.metadata" in PACKAGES_DIST_CMD


from python_deps.depgraph.ids import import_id, package_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from python_deps.depgraph.relink import import_to_package_edges


def _imp(name):
    return Node(
        id=import_id(name), type=NodeType.IMPORT, name=name,
        layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
    )


def _pkg(name, version="1.0"):
    return Node(
        id=package_id(name, version), type=NodeType.PACKAGE, name=name,
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version=version,
    )


def test_edge_builder_links_unmapped_import():
    # Heuristic identity guess would say dateutil->dateutil and find no package;
    # packages_distributions says dateutil is provided by python-dateutil.
    graph = DepGraph().with_node(_imp("dateutil")).with_node(_pkg("python-dateutil", "2.9.0"))
    edges = import_to_package_edges(graph, {"dateutil": ["python-dateutil"]})
    assert len(edges) == 1
    e = edges[0]
    assert e.src == import_id("dateutil")
    assert e.dst == package_id("python-dateutil", "2.9.0")
    assert e.relation is EdgeType.REQUIRES
    assert e.origin == "certified"


def test_edge_builder_case_insensitive_module_key():
    # packages_distributions key is the real module name "PIL"; Import node too.
    graph = DepGraph().with_node(_imp("PIL")).with_node(_pkg("pillow", "10.3.0"))
    edges = import_to_package_edges(graph, {"PIL": ["pillow"]})
    assert len(edges) == 1
    assert edges[0].dst == package_id("pillow", "10.3.0")


def test_edge_builder_namespace_links_all_present_dists():
    graph = (
        DepGraph()
        .with_node(_imp("google"))
        .with_node(_pkg("google-auth", "2.0"))
        .with_node(_pkg("protobuf", "4.0"))
    )
    edges = import_to_package_edges(graph, {"google": ["google-auth", "protobuf", "google-api-core"]})
    dsts = {e.dst for e in edges}
    assert package_id("google-auth", "2.0") in dsts
    assert package_id("protobuf", "4.0") in dsts
    # google-api-core has no Package node in the closure -> no edge.
    assert len(edges) == 2


def test_edge_builder_skips_existing_edge():
    graph = (
        DepGraph()
        .with_node(_imp("yaml"))
        .with_node(_pkg("PyYAML", "6.0"))
    )
    graph = graph.with_edge(
        Edge(src=import_id("yaml"), dst=package_id("PyYAML", "6.0"),
             relation=EdgeType.REQUIRES, origin="reconcile")
    )
    edges = import_to_package_edges(graph, {"yaml": ["PyYAML"]})
    assert edges == []


from python_deps.depgraph.relink import certified_import_links


def test_certified_import_links_adds_edge(fake_executor, make_result_fixture):
    graph = DepGraph().with_node(_imp("dateutil")).with_node(_pkg("python-dateutil", "2.9.0"))
    fake_executor.responses = {
        "packages_distributions": make_result_fixture(
            stdout='{"dateutil": ["python-dateutil"]}'
        )
    }

    out = certified_import_links(graph, fake_executor)

    deps = out.requires_of(import_id("dateutil"))
    assert any(d.id == package_id("python-dateutil", "2.9.0") for d in deps)


def test_certified_import_links_graceful_on_command_failure(fake_executor):
    # Empty FakeExecutor -> command returns rc 127 (not ok) -> graph unchanged.
    graph = DepGraph().with_node(_imp("dateutil")).with_node(_pkg("python-dateutil", "2.9.0"))
    out = certified_import_links(graph, fake_executor)
    assert out.edges == ()
