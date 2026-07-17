# tests/depgraph/test_integrate.py
import pytest

from corpus_integrate import CASES
from graph.exec_trace import ObservationOverlay
from graph.integrate import integrate
from graph.diagnose import RepoContext
from graph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy, EdgeType
from graph.ids import TEST_NODE_ID

_TEST_NODE = Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo tests",
                  layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.UNKNOWN)
# local_names carries the sys.path-accurate repo top-levels (see diagnose.RepoContext).
_CTX = RepoContext(local_names=frozenset({"myapp"}))


def _graph_for(case):
    g = DepGraph(nodes=(_TEST_NODE,) + case.starting_nodes)
    return g


def _pkg_nodes(graph, name):
    from python_deps.import_mapping import normalize_package_name
    want = normalize_package_name(name)
    return [n for n in graph.nodes
            if n.type is NodeType.PACKAGE and normalize_package_name(n.name) == want]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_integrate_case(case):
    g0 = _graph_for(case)
    g1, overlay = integrate(g0, ObservationOverlay(), case.parsed, _CTX)

    if not case.expect_add:
        # REFUSE: no graph node/edge added (false-add guard). Overlay MAY record it.
        assert len(g1.nodes) == len(g0.nodes)
        assert len(g1.edges) == len(g0.edges)
        return

    # node landed
    assert g1.get(case.expect_node_id) is not None, f"missing node {case.expect_node_id}"

    # no fracture: exactly one node for the capability
    if case.expect_node_id.startswith("pkg:"):
        assert len(_pkg_nodes(g1, "psycopg2")) == 1

    # match vs append
    if case.match_existing:
        assert len(g1.nodes) == len(g0.nodes)   # annotated, no twin
    if case.expect_unbound:
        # demand-only: no requires edge OUT of the import node, no provider edge added
        assert case.expect_edge is None
        assert not any(e.src == case.expect_node_id and e.relation is EdgeType.REQUIRES
                       for e in g1.edges)

    # edge
    if case.expect_edge is not None:
        src, dst = case.expect_edge
        edge = next((e for e in g1.edges if e.src == src and e.dst == dst
                     and e.relation is EdgeType.REQUIRES), None)
        assert edge is not None, f"missing edge {src}->{dst}"
        if case.expect_via:
            assert tuple(edge.data.get("via", ())) == case.expect_via

    # causality on the overlay
    obs = overlay.get(case.parsed.stable_id)
    assert obs is not None
    if case.expect_blast:
        assert obs.blast_radius == case.expect_blast
