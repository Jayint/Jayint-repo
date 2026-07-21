import pytest

from corpus_grounding import GCASES
from graph.model import DepGraph
from src.eval.graph_repair_ablation.ground import run_grounding, grade_grounding


def _graph_for(case):
    return DepGraph(nodes=case.starting_nodes)


@pytest.mark.parametrize("case", GCASES, ids=lambda c: c.name)
def test_grounding_arm(case):
    g = _graph_for(case)
    res = run_grounding(g, case.cause_text, case.command, case.failure_output, case.ctx)
    g_score = grade_grounding(res["grounded_anchor"], res["grounded_added_node"], case.correct_anchor)
    b_score = grade_grounding(res["baseline_anchor"], res["baseline_anchor"] is not None, case.correct_anchor)
    assert g_score.grounded is case.expect_grounded_hit, f"G {case.name}: {res}"
    assert b_score.grounded is case.expect_baseline_hit, f"B {case.name}: {res}"


def test_grade_refuse_flags_a_false_add():
    # a node added on a REFUSE case is a mislocalization
    s = grade_grounding("pkg:wrong", added_node=True, correct_anchor="")
    assert s.grounded is False and s.mislocalized is True


def test_cause_line_extracts_error_not_summary():
    from src.eval.graph_repair_ablation.ground import cause_line
    collect_out = (
        "ImportError while importing test module 'tests/test_x.py'.\n"
        "tests/test_x.py:1: in <module>\n"
        "    import urllib3\n"
        "E   ModuleNotFoundError: No module named 'urllib3'\n"
        "!!!!!!!! Interrupted: 1 error during collection !!!!!!!!\n"
        "=== 1 error in 0.12s ===\n"
    )
    cl = cause_line(collect_out)
    assert "urllib3" in cl and "Interrupted" not in cl and "error in" not in cl
    # a runtime ImportError (no pytest summary) is returned as-is:
    assert cause_line("ImportError: libGL.so.1: cannot open shared object file") \
        == "ImportError: libGL.so.1: cannot open shared object file"
