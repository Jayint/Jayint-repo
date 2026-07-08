"""The agent's move (spec §4): one read-only EXPLORE command or a full-script PATCH.
Parsing is pure; the loop enforces read-only on explore and applies the patch."""
from __future__ import annotations

import re
from dataclasses import dataclass

_SCRIPT_BLOCK = re.compile(r"Script:\s*```(?:bash|sh)?\s*\n(.*?)```", re.DOTALL)
_ACTION_LINE = re.compile(r"^Action:\s*(.+)$", re.MULTILINE)
_THOUGHT = re.compile(r"Thought:\s*(.+?)(?=\n(?:Action|Script):|$)", re.DOTALL)


@dataclass(frozen=True)
class Action:
    kind: str                       # "explore" | "patch" | "invalid"
    command: str | None = None      # explore
    new_script: str | None = None   # patch


def parse_action(text: str) -> Action:
    t = text or ""
    m = _SCRIPT_BLOCK.search(t)             # patch wins if both are present
    if m:
        return Action("patch", new_script=m.group(1).strip() + "\n")
    m = _ACTION_LINE.search(t)
    if m:
        return Action("explore", command=m.group(1).strip())
    return Action("invalid")


def extract_thought(text: str) -> str:
    m = _THOUGHT.search(text or "")
    return m.group(1).strip() if m else ""
