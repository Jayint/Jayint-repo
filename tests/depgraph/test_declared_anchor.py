from graph.advise import declared_anchor
from graph.model import (DepGraph, DiscoveredBy, Layer, Node, NodeType,
                         TEST_NODE_ID, package_id, project_id)


def _base():
    test = Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL)
    proj = Node(id=project_id("myproj"), type=NodeType.PROJECT, name="myproj",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN)
    return DepGraph().with_node(test).with_node(proj)


def test_direct_anchors_to_project():
    g = _base()
    pkg = Node(id=package_id("certifi", "1"), type=NodeType.PACKAGE, name="certifi",
               layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER).with_data(declared="direct")
    assert declared_anchor(g.with_node(pkg), pkg).type is NodeType.PROJECT


def test_optional_anchors_to_test_goal():
    g = _base()
    pkg = Node(id=package_id("pytest", "1"), type=NodeType.PACKAGE, name="pytest",
               layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER).with_data(declared="optional")
    assert declared_anchor(g.with_node(pkg), pkg).type is NodeType.TEST


def test_undeclared_returns_none():
    g = _base()
    pkg = Node(id=package_id("numpy", "1"), type=NodeType.PACKAGE, name="numpy",
               layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER)
    assert declared_anchor(g.with_node(pkg), pkg) is None
