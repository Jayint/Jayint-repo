"""Provider action-class taxonomy (design §10): a provider command must match the
shell action class declared by its `kind`. Pure: no Docker/network/LLM."""
from __future__ import annotations

import re

# kind -> regex the provider command must match (searched against the stripped command).
ACTION_CLASSES: dict[str, str] = {
    "apt":   r"^(?:apt-get|apt)(?:\s+update\s*&&\s*(?:apt-get|apt))?\s+install\b",
    "pip":   r"^(python3?\s+-m\s+)?pip\d?\s+install\b",
    "npm":   r"^npm\s+(install|ci)\b",
    "shell": r".",
}
_ENV_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=[^\s;&|<>`$()]+\s+)+")


def _strip_safe_env_prefix(command: str) -> str:
    return _ENV_PREFIX.sub("", command, count=1)


def matches_action_class(kind: str, command: str) -> bool:
    pattern = ACTION_CLASSES.get(kind)
    if pattern is None:
        return False
    stripped = (command or "").strip()
    if not stripped:
        return False
    return re.search(pattern, _strip_safe_env_prefix(stripped)) is not None
