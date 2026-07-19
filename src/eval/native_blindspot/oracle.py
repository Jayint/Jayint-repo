from __future__ import annotations

import json
import os
from dataclasses import dataclass

_HERE = os.path.dirname(__file__)
_ORACLE_PATH = os.path.join(_HERE, "oracle.json")
_TRIGGERS_PATH = os.path.join(_HERE, "triggers.json")


@dataclass(frozen=True)
class RepoExpectation:
    repo: str
    cli: tuple[str, ...]
    dlopen: tuple[str, ...]
    culprit: str
    in_scope: bool
    part: str          # "A" | "B" | "A+B" | "-"
    note: str = ""

    @property
    def expected_apt(self) -> tuple[str, ...]:
        return self.cli + self.dlopen


def load_oracle(path: str = _ORACLE_PATH) -> dict[str, RepoExpectation]:
    with open(path) as fh:
        raw = json.load(fh)
    out: dict[str, RepoExpectation] = {}
    for row in raw["affected"]:
        exp = RepoExpectation(
            repo=row["repo"],
            cli=tuple(row.get("cli", [])),
            dlopen=tuple(row.get("dlopen", [])),
            culprit=row.get("culprit", ""),
            in_scope=bool(row.get("in_scope", True)),
            part=row.get("part", "-"),
            note=row.get("note", ""),
        )
        out[exp.repo] = exp
    return out


def load_triggers(path: str = _TRIGGERS_PATH) -> dict[str, str]:
    with open(path) as fh:
        return json.load(fh)
