# tests/test_run_v1_turn_budget.py — source-level guard
# (Filename kept for Phase 0's no-rename constraint; this now guards run_v3's turn
# budget. The legacy planner-driven loop it was named for was retired in Phase 0.)
from pathlib import Path
_SRC = Path(__file__).resolve().parents[1] / "src" / "envstate" / "orchestrator.py"


def test_turn_budget_present_and_not_in_deterministic_drain():
    src = _SRC.read_text()
    assert "_repair_turns" in src
    assert "_budget_exhausted" in src
    assert "LLM turn budget exhausted" in src
    # Phase 4 (fresh-replay-only run_v3): emit_drain no longer runs inside run_v3 at
    # all — the deterministic-drain-vs-turn-budget question is moot there (there is no
    # drain to check). The legacy planner-driven loop that drove emit_drain was retired
    # in Phase 0, so emit_drain must now be ABSENT from run_v3 (the sole loop).
    run_v3_start = src.index("def run_v3(")
    run_v3_body = src[run_v3_start:]
    assert "emit_drain(" not in run_v3_body, (
        "emit_drain must be ABSENT from run_v3 (Phase 4: fresh-replay is the sole executor)"
    )
