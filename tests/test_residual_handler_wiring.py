from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "envstate" / "orchestrator.py"


def test_residual_giveup_is_gated_and_never_sets_done_flag():
    src = _SRC.read_text()
    assert "_residual_giveup" in src
    body = src[src.index("def _runtime_ingest_phase"):]
    assert "diverged_node_ids" in body          # divergence detector consumed
    assert "note_out_of_scope" in body          # non-env diagnoses captured
    assert "emittable" in body                  # frontier-clean guard present
    # the divergence give-up is gated INSIDE the ingest body (not just the run_v1 signature)
    assert body.index("enable_graph_scheduler") < body.index("_residual_giveup")
    # assert the SPECIFIC new loop line, not the bare 'planner_giveup' literal
    # (which already appears 3x in the file and would pass trivially).
    assert "if enable_graph_scheduler and _residual_giveup is not None:" in src
    # the residual give-up block must NOT write done_flag
    blk = body[body.index("_residual_giveup"):]
    assert "done_flag" not in blk[:600]


def test_llm_classifier_injected_only_under_graph_scheduler():
    src = _SRC.read_text()
    body = src[src.index("def _runtime_ingest_phase"):]
    # the classifier tier is referenced, and gated by the scheduler flag
    assert "make_llm_classifier" in body
    gate = body.index("enable_graph_scheduler")
    inject = body.index("make_llm_classifier")
    assert gate < inject                       # flag check precedes the LLM wiring
    # the deterministic classifier is always present in the default tuple
    assert "classify_observation" in body
    # temperature 0 on the wrapped completion
    assert "temperature=0" in body
