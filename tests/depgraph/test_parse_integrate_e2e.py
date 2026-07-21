# tests/depgraph/test_parse_integrate_e2e.py
from graph.python.enrich.exec_trace import parse, ObservationOverlay
from graph.python.enrich.integrate import integrate
from graph.python.enrich.diagnose import RepoContext
from graph.model import DepGraph, Node, NodeType, Layer, State, DiscoveredBy, EdgeType
from graph.model import TEST_NODE_ID, package_id

_LOG = '''\
tests/test_x.py:2: in <module>
    from myapp import thing
myapp/db.py:1: in <module>
    import psycopg2
E   ModuleNotFoundError: No module named 'psycopg2'
'''


def test_raw_log_grounds_to_pkg_node_with_causal_edge():
    g = DepGraph(nodes=(Node(id=TEST_NODE_ID, type=NodeType.TEST, name="t",
                             layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL,
                             state=State.UNKNOWN),))
    ctx = RepoContext(local_names=frozenset({"myapp"}))
    pf = parse("pytest", _LOG, "collection", ctx)
    g2, overlay = integrate(g, ObservationOverlay(), pf, ctx)

    assert g2.get(package_id("psycopg2", None)) is not None
    edge = next(e for e in g2.edges if e.dst == package_id("psycopg2", None)
                and e.relation is EdgeType.REQUIRES)
    assert edge.src == TEST_NODE_ID
    assert "module:myapp.db" in edge.data.get("via", []) or edge.data.get("importer")
    assert overlay.observations[0].blast_radius  # non-empty
