"""T3 (gate) — react-arm GATE goldens (spec §9 Regime 1; plan Phase 0 / T3).

Snapshots the gate verdict the future `agent/gate.py` merge folds together (<- gate + anti_cheat):
  * `test_verdict` across the >=80% pass-rate boundary and the anti-hollow guards
    (all-collection-errors, zero-collected, all-skipped, ANSI-in-output, collected>executed),
  * the anti-gaming detectors — `narrowing_reason` / `detect_test_narrowing` (test-collection
    narrowing) and `self_install_reason` / `added_self_install_reason` (installing the project
    under test from an index) — INCLUDING the legitimate edits that must NOT trip.

Env-independent, so hermetic without pinning. Byte-diff == 0 is the proof of "same as the react
arm". To refresh after an intentional change, delete `tests/goldens/gate/` and regenerate.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import golden_kit as gk  # noqa: E402

FIXTURE = gk.GOLDEN_DIR / "gate" / "gate_table.txt"


def test_gate_table_matches_golden():
    got = gk.serialize_table(gk.gate_cases())
    assert FIXTURE.exists(), f"missing golden fixture {FIXTURE} — regenerate goldens/gate/"
    expected = FIXTURE.read_text(encoding="utf-8")
    assert got == expected, (
        "gate verdict drift vs golden gate_table.txt: the react arm's pass-rate/anti-gaming "
        "verdicts changed. If intentional, regenerate the fixture; else this is a regression.")


def test_gate_table_covers_boundary_and_anti_cheat():
    """The plan requires the >=80% boundary AND >=1 anti-cheat trip be pinned — assert both are
    present so a future edit can't quietly drop them from the table."""
    names = set(gk.gate_cases())
    assert "verdict/boundary_exactly_80" in names and "verdict/just_below_80" in names
    assert "verdict/hollow_zero_collected" in names            # anti-hollow guard
    assert "narrowing/add_ignore_tests" in names               # a test-narrowing trip
    assert "self_install/index_project" in names               # a self-install trip
    assert "narrowing/legit_pip_ignore_installed" in names     # a must-NOT-trip control
