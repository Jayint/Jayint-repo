from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Ecosystem,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
)


def _node(node_id, node_type, ecosystem=Ecosystem.NPM):
    return Node(
        id=node_id,
        type=node_type,
        name=node_id,
        layer=Layer.DEPENDENCIES,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        ecosystem=ecosystem,
        workspace="frontend",
        package_manager="npm",
    )


def test_polyglot_schema_supports_four_level_relations_and_serialization():
    imp = _node("import:npm:frontend:react", NodeType.IMPORT)
    req = _node("req:npm:frontend:react", NodeType.REQUIREMENT)
    pkg = _node("pkg:npm:react-18.3.1", NodeType.PACKAGE)
    deps = _node("deps:npm:frontend", NodeType.DEPENDENCY_SET)
    graph = DepGraph()
    for node in (imp, req, pkg, deps):
        graph = graph.with_node(node)
    graph = graph.with_edge(Edge(imp.id, pkg.id, EdgeType.MAPS_TO))
    graph = graph.with_edge(Edge(req.id, pkg.id, EdgeType.RESOLVES_TO))
    graph = graph.with_edge(Edge(deps.id, pkg.id, EdgeType.DESCRIBES))

    payload = graph.to_dict()

    assert {edge["relation"] for edge in payload["edges"]} == {
        "maps_to", "resolves_to", "describes",
    }
    package = next(node for node in payload["nodes"] if node["id"] == pkg.id)
    assert package["ecosystem"] == "npm"
    assert package["workspace"] == "frontend"
    assert package["package_manager"] == "npm"
