from src.bench_emit.provisional import provisional_installs


def test_extracts_provisional_package_nodes_sorted():
    graph = {
        "nodes": [
            {"type": "Package", "name": "util", "data": {"provisional": {
                "name": "util", "reason": "fallthrough u", "cure_rung": "isolated"}}},
            {"type": "Package", "name": "azure", "data": {"provisional": {
                "name": "azure", "reason": "fallthrough a", "cure_rung": "no_build_isolation"}}},
            {"type": "Package", "name": "requests", "data": {}},        # ordinary dep
            {"type": "Import", "name": "azure", "data": {}},            # not a Package
        ],
        "edges": [],
    }
    assert provisional_installs(graph) == [
        {"name": "azure", "reason": "fallthrough a", "cure_rung": "no_build_isolation"},
        {"name": "util", "reason": "fallthrough u", "cure_rung": "isolated"},
    ]


def test_empty_or_malformed_graph_yields_empty():
    assert provisional_installs({}) == []
    assert provisional_installs({"nodes": None}) == []
    assert provisional_installs({"nodes": ["not-a-dict"]}) == []
    # A Package with no provisional payload contributes nothing.
    assert provisional_installs({"nodes": [{"type": "Package", "name": "x", "data": {}}]}) == []
