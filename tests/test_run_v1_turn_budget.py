# tests/test_run_v1_turn_budget.py — source-level guard
from pathlib import Path
_SRC = Path(__file__).resolve().parents[1] / "src" / "envstate" / "orchestrator.py"


def test_turn_budget_present_and_not_in_deterministic_drain():
    src = _SRC.read_text()
    assert "_repair_turns" in src
    assert "_budget_exhausted" in src
    assert "LLM turn budget exhausted" in src
    # The deterministic emit drain must not consume the turn budget. The slice ends at
    # the Task-4 repair block ("# Host-first repair"), so it captures exactly the
    # emit_drain call + its step accounting — the repair-turn decrement lives AFTER
    # the repair block and is correctly excluded.
    emit = src[src.index("graph, _reports, steps = emit_drain"):src.index("# Host-first repair")]
    assert "_repair_turns" not in emit
