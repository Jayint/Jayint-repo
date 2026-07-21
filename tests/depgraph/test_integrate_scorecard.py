from corpus_integrate import CASES
from test_integrate import _graph_for, _pkg_nodes, _CTX
from graph.python.enrich.exec_trace import ObservationOverlay
from graph.python.enrich.integrate import integrate
from graph.model import EdgeType


def test_scorecard_axes_all_perfect_on_corpus():
    n = len(CASES)
    right_target = one_node = edge_ok = false_add = 0
    for case in CASES:
        g0 = _graph_for(case)
        g1, _ = integrate(g0, ObservationOverlay(), case.parsed, _CTX)
        if not case.expect_add:
            false_add += 0 if (len(g1.nodes) == len(g0.nodes) and len(g1.edges) == len(g0.edges)) else 1
            right_target += 1; one_node += 1; edge_ok += 1
            continue
        right_target += 1 if g1.get(case.expect_node_id) is not None else 0
        one_node += 1 if (not case.expect_node_id.startswith("pkg:")
                          or len(_pkg_nodes(g1, "psycopg2")) == 1) else 0
        edge_ok += 1 if (case.expect_edge is None or any(
            e.src == case.expect_edge[0] and e.dst == case.expect_edge[1]
            and e.relation is EdgeType.REQUIRES for e in g1.edges)) else 0
    # Regression gate: the corpus is golden. Any drop is a regression.
    assert right_target == n, f"resolution {right_target}/{n}"
    assert one_node == n, f"no-fracture {one_node}/{n}"
    assert edge_ok == n, f"edge {edge_ok}/{n}"
    assert false_add == 0, f"false-add {false_add} (MUST be 0)"
