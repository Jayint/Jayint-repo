from graph.model import (
    DepGraph, DiscoveredBy, EdgeType, Layer, Node, NodeType, State,
    binary_id, package_id, project_id,
)
from graph.python.native.runtime_tools import seed_runtime_tools


def _pkg(name, version="1.0"):
    return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version=version)


def _graph(*nodes):
    g = DepGraph()
    for n in nodes:
        g = g.with_node(n)
    return g


def test_seeds_binary_tool_for_mapped_package():
    out = seed_runtime_tools(_graph(_pkg("GitPython", "3.1.43")))
    node = out.get(binary_id("git"))
    assert node is not None
    assert node.type is NodeType.TOOL
    assert node.layer is Layer.TOOLCHAIN
    assert node.discovered_by is DiscoveredBy.RESOLVER   # annotate-on-observe
    assert node.state is State.UNKNOWN
    assert node.check_command == "command -v git"
    assert node.chosen_fix == "apt:git"
    assert any(e.src == package_id("GitPython", "3.1.43")
               and e.dst == binary_id("git")
               and e.relation is EdgeType.REQUIRES for e in out.edges)


def test_noop_for_unmapped_package():
    g = _graph(_pkg("requests", "2.32.0"))
    assert seed_runtime_tools(g) is g


def test_idempotent_does_not_duplicate_existing_binary_node():
    # A repo-source subprocess_scan already minted binary:git (STATIC_SCAN).
    g = _graph(_pkg("GitPython", "3.1.43"))
    existing = Node(id=binary_id("git"), type=NodeType.TOOL, name="git",
                    layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.STATIC_SCAN,
                    state=State.UNKNOWN, check_command="command -v git",
                    fix_candidates=("apt:git",), chosen_fix="apt:git",
                    provenance="subprocess-scan")
    g = g.with_node(existing)
    out = seed_runtime_tools(g)
    git_nodes = [n for n in out.nodes if n.id == binary_id("git")]
    assert len(git_nodes) == 1
    assert git_nodes[0].provenance == "subprocess-scan"  # existing kept
    # edge still added
    assert any(e.dst == binary_id("git") for e in out.edges)
