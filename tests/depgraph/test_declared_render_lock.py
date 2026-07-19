"""Lock the render paths the whole-branch review found the goldens never covered.

Declared deps are re-homed to ``Package.data['declared']`` (no Project->Package edge),
so these agent-facing / setup.sh render sites must reconstruct them from the flag.
Snapshots the ACCEPTED post-demotion output (2026-07-19; plan review-fix, spec §4F) so
these paths stop being vacuous under the goldens byte-diff gate.
"""
from graph.model import (DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node,
                         NodeType, TEST_NODE_ID, import_id, package_id, project_id)


def _graph():
    test = Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL)
    proj = Node(id=project_id("myproj"), type=NodeType.PROJECT, name="myproj",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                data={"installable": True})
    # flask: declared AND imported (carries a relink Import->Package edge).
    flask = Node(id=package_id("flask", "3.0.0"), type=NodeType.PACKAGE, name="flask",
                 version="3.0.0", layer=Layer.PIP,
                 discovered_by=DiscoveredBy.RESOLVER).with_data(declared="direct")
    flask_imp = Node(id=import_id("flask"), type=NodeType.IMPORT, name="flask",
                     layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN)
    # certifi: declared-only (never imported) — the case whose render fully depends
    # on the flag reconstruction now that the Project->Package edge is gone.
    certifi = Node(id=package_id("certifi", "2026.1"), type=NodeType.PACKAGE, name="certifi",
                   version="2026.1", layer=Layer.PIP,
                   discovered_by=DiscoveredBy.RESOLVER).with_data(declared="direct")
    g = (DepGraph().with_node(test).with_node(proj).with_node(flask)
         .with_node(flask_imp).with_node(certifi))
    g = g.with_edge(Edge(src=TEST_NODE_ID, dst=project_id("myproj"),
                         relation=EdgeType.REQUIRES, origin="project"))
    g = g.with_edge(Edge(src=TEST_NODE_ID, dst=import_id("flask"),
                         relation=EdgeType.REQUIRES, origin="scan"))
    g = g.with_edge(Edge(src=import_id("flask"), dst=package_id("flask", "3.0.0"),
                         relation=EdgeType.REQUIRES, origin="certified"))
    return g


def test_project_deps_reconstructs_declared_from_flag():
    from graph.advise import _project_deps
    assert _project_deps(_graph(), project_id("myproj")) == ["certifi", "flask"]


def test_chain_to_goal_declared_only_reaches_goal_via_anchor():
    from graph.advise import _chain_to_goal
    g = _graph()
    assert (_chain_to_goal(g, g.get(package_id("certifi", "2026.1")))
            == "certifi <- myproj <- repo_tests_pass")


def test_up_edges_renders_declared_anchor_line():
    from graph.view.graph_context import _up_edges
    g = _graph()
    lines = _up_edges(g, g.get(package_id("certifi", "2026.1")), 0, False)
    assert any("project:myproj" in ln and "--requires-->" in ln
               and "pkg:certifi==2026.1" in ln for ln in lines)


def test_build_script_annotation_lists_declared_deps_on_project():
    """The setup.sh capstone `requires=` comment must still list declared deps
    (the review's IMPORTANT-1: `_annotation` reads requires_of(project))."""
    from graph.compile.build_script import _annotation
    ann = _annotation(_graph(), _graph().get(project_id("myproj")))[0]
    assert "pkg:certifi==2026.1" in ann and "pkg:flask==3.0.0" in ann
