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
