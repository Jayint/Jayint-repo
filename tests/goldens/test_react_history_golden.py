"""T2 — react-arm HISTORY render goldens (spec §9 Regime 1; plan Phase 0 / T2).

Snapshots the transcript render across BOTH styles named in the plan — message-list
(`message_view.build_messages`) and grouped (`history_view.render_history`) — and, for a
complete oracle, every render branch of the four source files the future `agent/history.py`
merge folds together (<- history + history_view + message_view + style):

    render_history : flat (default) + grouped
    build_messages : classic (default) + agentic

Two transcripts drive them: `resolve` (blocker resolves — exercises block-split, observation
dedup, current-turn withhold, message-list elision) and `stuck` (blocker stays open —
exercises the do-not-retry ledger + STUCK escalation). Byte-diff == 0 is the proof of "same
as the react arm". To refresh after an intentional change, delete `tests/goldens/history/`
and regenerate.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import golden_kit as gk  # noqa: E402
import src.react_repair.history_view as history_view  # noqa: E402
from src.react_repair.history_view import render_history  # noqa: E402

HIST_DIR = gk.GOLDEN_DIR / "history"


def _assert_golden(rel_path: str, got: str) -> None:
    fixture = HIST_DIR / rel_path
    assert fixture.exists(), f"missing golden fixture {fixture} — regenerate goldens/history/"
    expected = fixture.read_text(encoding="utf-8")
    assert got == expected, (
        f"history render drift vs golden {rel_path}: the react arm's transcript bytes changed. "
        f"If intentional, regenerate the fixture; otherwise this is a caught regression.")


def test_render_history_flat_and_grouped_match_goldens(monkeypatch):
    gk.pin_defaults(monkeypatch)
    for name, transcript in gk.history_transcripts().items():
        steps = transcript.steps
        monkeypatch.setattr(history_view, "_HISTORY_MODE", "flat")
        _assert_golden(f"{name}.render_flat.txt", render_history(steps) + "\n")
        monkeypatch.setattr(history_view, "_HISTORY_MODE", "grouped")
        _assert_golden(f"{name}.render_grouped.txt", render_history(steps) + "\n")


def test_message_list_classic_and_agentic_match_goldens(monkeypatch):
    gk.pin_defaults(monkeypatch)
    for name, transcript in gk.history_transcripts().items():
        steps = transcript.steps
        monkeypatch.setenv("REACT_MSG_STYLE", "classic")
        _assert_golden(f"{name}.messages_classic.txt",
                       gk.serialize_messages(gk.build_history_message_list(steps)))
        monkeypatch.setenv("REACT_MSG_STYLE", "agentic")
        _assert_golden(f"{name}.messages_agentic.txt",
                       gk.serialize_messages(gk.build_history_message_list(steps)))


def test_every_history_fixture_is_covered():
    """No orphaned fixture: each committed history/*.txt is rebuilt by a case above."""
    styles = ("render_flat", "render_grouped", "messages_classic", "messages_agentic")
    covered = {f"{name}.{style}.txt" for name in gk.history_transcripts() for style in styles}
    on_disk = {p.name for p in HIST_DIR.glob("*.txt")}
    assert on_disk == covered, f"fixture/coverage mismatch: only-on-disk={on_disk - covered}, only-in-test={covered - on_disk}"
