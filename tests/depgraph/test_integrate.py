# tests/depgraph/test_integrate.py
import pytest

from corpus_integrate import CASES
from graph.python.enrich.exec_trace import ObservationOverlay, ParsedFailure
from graph.python.enrich.integrate import integrate
from graph.python.enrich.diagnose import RepoContext
from graph.model import DepGraph, Node, NodeType, Layer, State, DiscoveredBy, EdgeType
from graph.model import TEST_NODE_ID, project_id

_TEST_NODE = Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo tests",
                  layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.UNKNOWN)
# local_names carries the sys.path-accurate repo top-levels (see diagnose.RepoContext).
_CTX = RepoContext(local_names=frozenset({"myapp"}))


def _graph_for(case):
    g = DepGraph(nodes=(_TEST_NODE,) + case.starting_nodes)
    return g


def _pkg_nodes(graph, name):
    from graph.python.util.import_mapping import normalize_package_name
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


# --------------------------------------------------------------------------- #
# owner anchoring — the provider requires-edge owner                            #
# --------------------------------------------------------------------------- #

_SYSLIB_FAIL = ParsedFailure(
    phase="runtime", failure_type="native_library_missing",
    terminal="syslib:libGL.so.1", causal="syslib:libGL.so.1",
    chain=(("target:tests/test_render.py", "loads", "syslib:libGL.so.1"),),
    raw_span="ImportError: libGL.so.1: cannot open shared object file",
)


def _project_node():
    return Node(id=project_id("myapp"), type=NodeType.PROJECT, name="myapp",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN)


def test_provider_edge_falls_back_to_project_when_test_absent():
    # A hand-built graph may omit the Test goal (the graph is normally spine-rooted
    # on it, but integrate() has no live pipeline caller). The resolved provider
    # must still be OWNED — anchor the requires edge on the Project hub instead of
    # silently orphaning it.
    g0 = DepGraph(nodes=(_project_node(),))            # Project, no Test
    g1, _ = integrate(g0, ObservationOverlay(), _SYSLIB_FAIL, _CTX)
    assert g1.get("syslib:libGL.so.1") is not None      # provider node landed
    assert any(e.src == project_id("myapp") and e.dst == "syslib:libGL.so.1"
               and e.relation is EdgeType.REQUIRES for e in g1.edges), \
        "provider must be owned by the Project when Test is absent"


def test_provider_edge_prefers_test_over_project():
    # When BOTH exist, the Test goal remains the owner (byte-identical to the
    # spine-rooted production graph).
    g0 = DepGraph(nodes=(_TEST_NODE, _project_node()))
    g1, _ = integrate(g0, ObservationOverlay(), _SYSLIB_FAIL, _CTX)
    assert any(e.src == TEST_NODE_ID and e.dst == "syslib:libGL.so.1"
               for e in g1.edges)
    assert not any(e.src == project_id("myapp") and e.dst == "syslib:libGL.so.1"
                   for e in g1.edges)


def test_provider_node_added_but_unowned_when_no_anchor():
    # Neither Test nor Project: the provider node is still recorded, but NO owner
    # is invented — the requires edge is simply absent (never fabricate an anchor).
    g0 = DepGraph(nodes=())
    g1, _ = integrate(g0, ObservationOverlay(), _SYSLIB_FAIL, _CTX)
    assert g1.get("syslib:libGL.so.1") is not None
    assert not any(e.dst == "syslib:libGL.so.1" for e in g1.edges)
