# tests/eval/graph_repair_ablation/test_ground_gate.py
from corpus_grounding import GCASES
from graph.model import DepGraph
from src.eval.graph_repair_ablation.ground import grounding_scorecard


def test_grounding_gate_delta_on_corpus():
    agg, md = grounding_scorecard(GCASES)
    # G must strictly beat B on the syslib delta class; every G cell must be perfect.
    assert agg[("SYSLIB_MISSING", "G")]["grounded_at_1"] == 1.0
    assert agg[("SYSLIB_MISSING", "B")]["grounded_at_1"] == 0.0
    for (cls, arm), cell in agg.items():
        if arm == "G":
            assert cell["grounded_at_1"] == 1.0, f"G regressed on {cls}: {cell}"
    assert "grounded@1" in md
