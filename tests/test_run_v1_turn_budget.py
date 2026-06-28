# tests/test_run_v1_turn_budget.py — source-level guard
from pathlib import Path
_SRC = Path(__file__).resolve().parents[1] / "src" / "envstate" / "orchestrator.py"


def test_turn_budget_present_and_not_in_deterministic_drain():
    src = _SRC.read_text()
    assert "_repair_turns" in src
    assert "_budget_exhausted" in src
    assert "LLM turn budget exhausted" in src
    # After the run_v1/run_v3 split both anchors live inside run_v3.
    # The deterministic emit drain must not consume the turn budget. The slice
    # ends at "# Host-first repair", capturing exactly the emit_drain call + its
    # step accounting — the repair-turn decrement lives AFTER the repair block
    # and is correctly excluded.
    run_v3_start = src.index("def run_v3(")
    run_v3_body = src[run_v3_start:]
    emit = run_v3_body[
        run_v3_body.index("graph, _reports, steps = emit_drain"):
        run_v3_body.index("# Host-first repair")
    ]
    assert "_repair_turns" not in emit
