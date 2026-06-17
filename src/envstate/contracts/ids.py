"""Deterministic graph-id grammar shared by host projection and Maintainer."""
from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _UNSAFE.sub("-", str(text).lower()).strip("-")


def artifact_id(path: str) -> str:
    return f"artifact:{path}"


def requirement_id(kind: str, subject: str) -> str:
    return f"requirement:{kind}:{subject}"


def contract_id(kind: str, subject: str) -> str:
    return f"contract:{kind}:{subject}"


def goal_contract_id(name: str) -> str:
    return f"contract:goal:{name}"


def capability_id(kind: str, subject: str, revision: int) -> str:
    return f"capability:{kind}:{subject}@envrev:{revision:03d}"


def command_id(step: int) -> str:
    return f"cmd:{step:03d}"


def revision_id(rev: int) -> str:
    return f"envrev:{rev:03d}"


def transition_id(kind: str, target: str) -> str:
    return f"transition:{kind}:{target}"


def validator_id(kind: str, subject: str) -> str:
    return f"validator:{kind}:{subject}"


def verification_target_id(kind: str) -> str:
    return f"verify:{kind}"


def open_problem_id(signature: str) -> str:
    return f"openproblem:{slug(signature)}"


def failure_id(step: int, kind: str, subject: str) -> str:
    return f"failure:cmd{step:03d}:{kind}:{subject}"
