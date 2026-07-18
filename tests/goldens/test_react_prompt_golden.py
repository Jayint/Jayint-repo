"""T1 — react-arm PROMPT goldens (spec §9 Regime 1; plan Phase 0 / T1).

Snapshots the exact prompt the react arm sends — the literal `messages` list captured at
the `complete_with_tools` seam (`ReactPlanner._messages`), in BOTH prompt styles
(`messages` default + `blob`), plus the typed-patch arm's `render_repair_scope`. Together
these pin both inputs to the future `agent/prompt.py` merge (<- react/planner + repair_scope),
so that merge can be proven byte-identical (diff == 0 is the proof of "same as the react arm").

The check points at the PUBLIC build seam, never an internal patched symbol, so a re-export
shim in a later merge cannot silently no-op it (spec §9 Trap). To refresh after an
intentional prompt change, delete `tests/goldens/prompt/` and regenerate.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import golden_kit as gk  # noqa: E402
from src.agent.prompt import render_repair_scope  # noqa: E402

PROMPT_DIR = gk.GOLDEN_DIR / "prompt"
_STYLES = ("messages", "blob")


def _assert_golden(rel_path: str, got: str) -> None:
    fixture = PROMPT_DIR / rel_path
    assert fixture.exists(), (
        f"missing golden fixture {fixture} — regenerate the goldens/prompt/ directory")
    expected = fixture.read_text(encoding="utf-8")
    assert got == expected, (
        f"prompt drift vs golden {rel_path}: the react arm's prompt bytes changed. If the "
        f"change is intentional, regenerate the fixture; otherwise this is a caught regression.")


def test_prompt_messages_match_goldens(monkeypatch):
    gk.pin_defaults(monkeypatch)
    for style in _STYLES:
        monkeypatch.setenv("REACT_PROMPT_STYLE", style)
        for key in gk.prompt_scenarios():
            got = gk.serialize_messages(gk.build_prompt_messages(key))
            _assert_golden(f"{key}.{style}.txt", got)


def test_repair_scope_renders_match_goldens(monkeypatch):
    gk.pin_defaults(monkeypatch)
    for key, scope in gk.repair_scope_cases().items():
        got = render_repair_scope(scope) + "\n"
        _assert_golden(f"{key}.txt", got)


def test_prompt_extra_scenarios_match_goldens(monkeypatch):
    """P0-b: the GRAPH CONTEXT block + the rejection footer, in both prompt styles — the two
    branches the base scenarios leave dark (they all pass graph=None, rejection=None)."""
    gk.pin_defaults(monkeypatch)
    for style in _STYLES:
        monkeypatch.setenv("REACT_PROMPT_STYLE", style)
        for key in gk.prompt_extra_scenarios():
            got = gk.serialize_messages(gk.build_prompt_extra(key))
            _assert_golden(f"{key}.{style}.txt", got)


def test_every_prompt_fixture_is_covered():
    """No orphaned fixture: every committed prompt/*.txt is asserted by a case above — a
    stale golden that nothing rebuilds is a silent hole in the oracle."""
    covered = {f"{k}.{s}.txt" for s in _STYLES for k in gk.prompt_scenarios()}
    covered |= {f"{k}.{s}.txt" for s in _STYLES for k in gk.prompt_extra_scenarios()}
    covered |= {f"{k}.txt" for k in gk.repair_scope_cases()}
    on_disk = {p.name for p in PROMPT_DIR.glob("*.txt")}
    assert on_disk == covered, f"fixture/coverage mismatch: only-on-disk={on_disk - covered}, only-in-test={covered - on_disk}"
