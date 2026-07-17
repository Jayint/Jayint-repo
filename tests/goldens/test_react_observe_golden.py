"""T3 (observe) — react-arm OBSERVATION goldens (spec §9 Regime 1; plan Phase 0 / T3).

Snapshots "what the agent sees": the pure observe cluster the future `agent/observe.py` merge
folds together (<- observation + envelope + pytest_blocks + pytest_summary [+ graph's diagnose,
merged later]). A table of raw pytest/build outcomes → the rendered text:
  * `run_envelope` / `edit_result` — the `$ cmd → result` envelope + edit tool-results,
  * `summarize` + `format_breakdown` — the ranked pytest cause histogram,
  * `compact_pytest_blocks` — same-cause block dedup,
  * `strip_pip_progress` / `safety_compress_observation` — the noise strip.

These transforms carry NO env lever, so the table is hermetic without pinning. Byte-diff == 0
is the proof of "same as the react arm". To refresh after an intentional change, delete
`tests/goldens/observe/` and regenerate.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import golden_kit as gk  # noqa: E402

FIXTURE = gk.GOLDEN_DIR / "observe" / "observe_table.txt"


def test_observe_table_matches_golden():
    got = gk.serialize_table(gk.observe_cases())
    assert FIXTURE.exists(), f"missing golden fixture {FIXTURE} — regenerate goldens/observe/"
    expected = FIXTURE.read_text(encoding="utf-8")
    assert got == expected, (
        "observation render drift vs golden observe_table.txt: the react arm's 'what the agent "
        "sees' bytes changed. If intentional, regenerate the fixture; else this is a regression.")


def test_observe_table_is_nonempty_and_covers_each_family():
    """Guard against a silently-truncated oracle: every observe family must have >=1 case."""
    names = set(gk.observe_cases())
    families = {n.split("/", 1)[0] for n in names}
    assert families == {"envelope", "edit_result", "pytest_summary", "pytest_blocks", "compression"}
    assert len(names) >= 14
