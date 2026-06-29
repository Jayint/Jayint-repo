"""Check-quality predicate (Stage 2): reject checks structurally incapable of detecting
a missing dependency. Pure; no envstate import; no NodeType needed."""
from __future__ import annotations

_TRIVIAL_HEADS = {"true", ":", "echo", "ls", "pwd", "cd", "printf"}
# NOTE: `test` is intentionally excluded — `test -f <path>` / `test -e <path>` etc.
# ARE capable of detecting file/dep absence (exit 1 when absent), so they pass the gate.


def check_can_detect_absence(check_command: str) -> bool:
    """False when the check is structurally trivial (would pass without the install)."""
    cmd = (check_command or "").strip()
    if not cmd:
        return False
    head = cmd.split()[0]
    if head in _TRIVIAL_HEADS:
        return False
    return True
