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


def test_certified_import_links_drops_superseded_ghost(fake_executor, make_result_fixture):
    # The identity-fallback ghost `pkg:dateutil` (unresolved placeholder: version
    # None, MISSING) is superseded once the relink certifies the real provider
    # `pkg:python-dateutil`. The ghost (and its dangling edge) must be removed.
    imp = _imp("dateutil")
    ghost = Node(
        id=package_id("dateutil", None),
        type=NodeType.PACKAGE,
        name="dateutil",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        version=None,
    )
    real = _pkg("python-dateutil", "2.9.0.post0")
    graph = (
        DepGraph()
        .with_node(imp)
        .with_node(ghost)
        .with_node(real)
        .with_edge(Edge(src=imp.id, dst=ghost.id, relation=EdgeType.REQUIRES, origin="reconcile"))
    )
    fake_executor.responses = {
        "packages_distributions": make_result_fixture(
            stdout='{"dateutil": ["python-dateutil"]}'
        )
    }

    out = certified_import_links(graph, fake_executor)

    deps = {d.id for d in out.requires_of(imp.id)}
    assert package_id("python-dateutil", "2.9.0.post0") in deps  # real provider linked
    assert out.get(package_id("dateutil", None)) is None  # ghost removed
    assert all(e.dst != package_id("dateutil", None) for e in out.edges)  # dangling edge gone


def test_certified_import_links_drops_superseded_versioned_ghost(fake_executor, make_result_fixture):
    # P1: a low-trust identity-fallback root whose sdist FAILED TO BUILD (or hit a
    # `no version of X==Y`) is surfaced as a MISSING placeholder carrying a VERSION
    # (e.g. `factory==1.2`), unlike the registry-miss case (version None). It is
    # still a superseded duplicate once the relink certifies the real provider
    # (`import factory` -> `factory-boy`), and must be dropped too — the old
    # `version is None` guard left these naming-poison ghosts polluting the graph.
    imp = _imp("factory")
    ghost = Node(
        id=package_id("factory", "1.2"),
        type=NodeType.PACKAGE,
        name="factory",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        version="1.2",
    )
    real = _pkg("factory-boy", "3.3.3")
    graph = (
        DepGraph()
        .with_node(imp)
        .with_node(ghost)
        .with_node(real)
        .with_edge(Edge(src=imp.id, dst=ghost.id, relation=EdgeType.REQUIRES, origin="reconcile"))
    )
    fake_executor.responses = {
        "packages_distributions": make_result_fixture(stdout='{"factory": ["factory-boy"]}')
    }

    out = certified_import_links(graph, fake_executor)

    deps = {d.id for d in out.requires_of(imp.id)}
    assert package_id("factory-boy", "3.3.3") in deps  # real provider linked
    assert out.get(package_id("factory", "1.2")) is None  # versioned ghost removed
    assert all(e.dst != package_id("factory", "1.2") for e in out.edges)  # dangling edge gone


def test_certified_import_links_keeps_versioned_missing_without_replacement(
    fake_executor, make_result_fixture
):
    # Safety: a VERSIONED missing package whose import has NO certified provider
    # must be KEPT (it is a genuine unresolved/undeclared signal, not a superseded
    # duplicate). Guards against the relaxed guard over-dropping.
    imp = _imp("strawberry")
    miss = Node(
        id=package_id("strawberry", "3.0"),
        type=NodeType.PACKAGE,
        name="strawberry",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        version="3.0",
    )
    graph = DepGraph().with_node(imp).with_node(miss).with_edge(
        Edge(src=imp.id, dst=miss.id, relation=EdgeType.REQUIRES, origin="reconcile")
    )
    fake_executor.responses = {
        "packages_distributions": make_result_fixture(stdout='{"other": ["something"]}')
    }

    out = certified_import_links(graph, fake_executor)

    assert out.get(package_id("strawberry", "3.0")) is not None  # kept: no certified replacement


def test_certified_import_links_keeps_ghost_without_replacement(fake_executor, make_result_fixture):
    # A ghost whose import has NO certified provider must be KEPT (its misleading
    # fix is cleared upstream in resolve, but the node is not dropped here).
    imp = _imp("widget")
    ghost = Node(
        id=package_id("widget", None), type=NodeType.PACKAGE, name="widget",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=None,
    )
    graph = DepGraph().with_node(imp).with_node(ghost).with_edge(
        Edge(src=imp.id, dst=ghost.id, relation=EdgeType.REQUIRES, origin="reconcile")
    )
    fake_executor.responses = {
        "packages_distributions": make_result_fixture(stdout='{"other": ["something"]}')
    }

    out = certified_import_links(graph, fake_executor)

    assert out.get(package_id("widget", None)) is not None  # kept: no certified replacement


from python_deps.depgraph.relink import flag_unresolved_imports


def test_unlinked_import_is_flagged_unresolved():
    # `box` was linked by relink (import->package edge present); `mystery` has
    # no distribution at all and must be flagged as an honest under-declaration
    # signal, not a fabricated root.
    linked = _imp("box")
    unlinked = _imp("mystery")
    pkg = _pkg("python-box", "7.3.2")
    edge = Edge(src=linked.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="certified")
    graph = DepGraph(nodes=(linked, unlinked, pkg), edges=(edge,))

    out = flag_unresolved_imports(graph)

    assert out.get(unlinked.id).data.get("unresolved") is True
    assert "mystery" in out.get(unlinked.id).evidence
    assert out.get(linked.id).data.get("unresolved") is not True


def test_stale_unresolved_flag_cleared_when_now_provided():
    # `box` was flagged unresolved on a PRIOR pass (stale data carried over),
    # but NOW has a certified REQUIRES->Package edge -- the relink must clear
    # both the stale flag and the evidence it set, not just leave it stuck.
    imp = Node(
        id=import_id("box"), type=NodeType.IMPORT, name="box",
        layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
        data={"unresolved": True},
        evidence="unresolved: no distribution provides import box",
    )
    pkg = _pkg("python-box", "7.3.2")
    edge = Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="certified")
    graph = DepGraph(nodes=(imp, pkg), edges=(edge,))

    out = flag_unresolved_imports(graph)

    node = out.get(imp.id)
    assert node.data.get("unresolved") is not True
    assert "unresolved" not in dict(node.data)
    assert node.evidence is None


def test_stale_unresolved_flag_clear_preserves_other_data_keys():
    # Clearing the stale flag must not disturb unrelated data keys on the node.
    imp = Node(
        id=import_id("box"), type=NodeType.IMPORT, name="box",
        layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
        data={"unresolved": True, "some_other_key": "keep-me"},
        evidence="unresolved: no distribution provides import box",
    )
    pkg = _pkg("python-box", "7.3.2")
    edge = Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="certified")
    graph = DepGraph(nodes=(imp, pkg), edges=(edge,))

    out = flag_unresolved_imports(graph)

    node = out.get(imp.id)
    assert node.data.get("some_other_key") == "keep-me"
    assert "unresolved" not in dict(node.data)


def test_linked_never_flagged_import_not_rewritten():
    # A provided import that was never flagged unresolved must be left byte-
    # for-byte untouched (no needless node rewrite on the common/first-run path).
    linked = _imp("box")
    pkg = _pkg("python-box", "7.3.2")
    edge = Edge(src=linked.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="certified")
    graph = DepGraph(nodes=(linked, pkg), edges=(edge,))

    out = flag_unresolved_imports(graph)

    assert out.get(linked.id) is linked


def test_flag_unresolved_imports_is_idempotent():
    # Running flag_unresolved_imports twice must yield the same node data as
    # running it once, regardless of the prior flag state on the input graph.
    linked = _imp("box")
    unlinked = _imp("mystery")
    stale = Node(
        id=import_id("stale_but_provided"), type=NodeType.IMPORT, name="stale_but_provided",
        layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
        data={"unresolved": True},
        evidence="unresolved: no distribution provides import stale_but_provided",
    )
    pkg = _pkg("python-box", "7.3.2")
    graph = (
        DepGraph(nodes=(linked, unlinked, stale, pkg))
        .with_edge(Edge(src=linked.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="certified"))
        .with_edge(Edge(src=stale.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="certified"))
    )

    once = flag_unresolved_imports(graph)
    twice = flag_unresolved_imports(once)

    for node_id in (linked.id, unlinked.id, stale.id):
        n1 = once.get(node_id)
        n2 = twice.get(node_id)
        assert dict(n1.data) == dict(n2.data)
        assert n1.evidence == n2.evidence


def test_drop_ghost_never_removes_a_certified_target(fake_executor, make_result_fixture):
    # Pathological: pkg:foo is ghost-shaped (version None, MISSING) for import:foo,
    # but is ALSO the certified provider of import:bar. Dropping import:foo's ghost
    # must NOT remove pkg:foo (it is a protected certified target).
    imp_foo = _imp("foo")
    imp_bar = _imp("bar")
    weird = Node(
        id=package_id("foo", None), type=NodeType.PACKAGE, name="foo",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=None,
    )
    realfoo = _pkg("realfoo", "1.0")
    graph = DepGraph().with_node(imp_foo).with_node(imp_bar).with_node(weird).with_node(realfoo)
    fake_executor.responses = {
        # import:foo -> pkg:realfoo (certified) triggers the foo-ghost drop attempt;
        # import:bar -> pkg:foo (certified) makes pkg:foo a protected target.
        "packages_distributions": make_result_fixture(stdout='{"foo": ["realfoo"], "bar": ["foo"]}')
    }

    out = certified_import_links(graph, fake_executor)

    assert out.get(package_id("foo", None)) is not None  # protected, not dropped
    assert any(d.id == package_id("foo", None) for d in out.requires_of(imp_bar.id))
