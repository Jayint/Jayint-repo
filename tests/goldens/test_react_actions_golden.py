"""P0-a — react-arm ACTIONS goldens (spec §9 Regime 1; plan Phase 3 P0 / R9).

Snapshots the agent's MOVE parse+apply — the pure functions the future `agent/actions/` SPLIT
(actions + script_prep + v3_build_agent → actions/{base,script,graph}.py) must preserve
byte-for-byte. A table of raw model outputs / native tool calls → the parsed Action, plus a
table of EditOps → the spliced script:
  * `action_from_tool_call` — the PRIMARY native tool-calling path (JSON args → Action),
  * `parse_action` — the text FALLBACK, incl. the mis-wrapped-explore recovery + edit-only reject,
  * `apply_edit` — the pure line-splice (insert/replace/delete + out-of-range → None),
  * `extract_thought` / `extract_reasoning`.

Parsing carries NO env lever, so the table is hermetic without pinning. Byte-diff == 0 is the
proof the split kept the parser identical. The imports point at the PUBLIC `src.agent.actions`
surface (which the split's package `__init__` re-exports), so the byte-golden — not the import —
is what proves behaviour unchanged. To refresh after an intentional change, delete
`tests/goldens/actions/` and regenerate.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import golden_kit as gk  # noqa: E402

FIXTURE = gk.GOLDEN_DIR / "actions" / "actions_table.txt"


def test_actions_table_matches_golden():
    got = gk.serialize_table(gk.actions_cases())
    assert FIXTURE.exists(), f"missing golden fixture {FIXTURE} — regenerate goldens/actions/"
    expected = FIXTURE.read_text(encoding="utf-8")
    assert got == expected, (
        "actions parse/apply drift vs golden actions_table.txt: the react arm's move-parsing bytes "
        "changed. If intentional, regenerate the fixture; otherwise this is a caught regression.")


def test_actions_table_is_nonempty_and_covers_each_family():
    """Guard against a silently-truncated oracle: every move family must have >=1 case."""
    names = set(gk.actions_cases())
    families = {n.split("/", 1)[0] for n in names}
    assert families == {"parse", "tool", "apply", "thought", "reasoning"}
    assert len(names) >= 25
