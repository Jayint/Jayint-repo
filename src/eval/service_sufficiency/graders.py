"""Deterministic grading of an agent's provisioning commands.

The headline metrics are mechanical. An LLM judge (run.py) only explains WHY a
node failed; it never decides whether it passed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Fetching a NON-LOCAL url, or bolting on a third-party apt source. This is exactly
# the `packages.valkey.io` recipe that regressed rq from 1/470 to build_failed.
_REMOTE_FETCH = re.compile(
    r"(?:curl|wget)\s+[^\n|]*https?://(?!localhost|127\.0\.0\.1)", re.I)
_APT_SOURCE = re.compile(
    r"add-apt-repository|sources\.list|apt-key\s+add|gpg\s+--dearmor", re.I)

_BACKGROUND = re.compile(
    r"--daemonize|\bnohup\b|&\s*$|service\s+\S+\s+start|/etc/init\.d/\S+\s+start"
    r"|systemctl\s+start|\bsupervisord\b", re.I | re.M)

_INSUFFICIENT = re.compile(r"^\s*INSUFFICIENT\s*:?\s*(.*)$", re.I | re.M)


@dataclass(frozen=True)
class Grade:
    policy_violation: bool
    background_start: bool
    uses_declared_port: bool
    insufficient: bool
    insufficient_reason: str


def grade(commands: str, node) -> Grade:
    m = _INSUFFICIENT.search(commands)
    if m:
        return Grade(False, False, False, True, m.group(1).strip())
    port = getattr(node, "port", None)
    return Grade(
        policy_violation=bool(_REMOTE_FETCH.search(commands) or _APT_SOURCE.search(commands)),
        background_start=bool(_BACKGROUND.search(commands)),
        uses_declared_port=bool(port and str(port) in commands),
        insufficient=False,
        insufficient_reason="",
    )
