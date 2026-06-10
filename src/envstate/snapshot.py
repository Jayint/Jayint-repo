# src/envstate/snapshot.py
"""Read-only env probe -> EnvSnapshot(installed, env). Never raises.

Wraps extractor.run_extractor via sandbox.exec_readonly (no ledger / no
Dockerfile leak). env is empty ONLY on total probe failure (arch reads on
any healthy container), which apply_deterministic uses as the degrade signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.envstate.extractor import run_extractor, LIGHTWEIGHT_FIELDS
from src.envstate.world_model import Fact

_SNAPSHOT_FIELDS = LIGHTWEIGHT_FIELDS + ("which_python", "venv")


@dataclass(frozen=True)
class EnvSnapshot:
    installed: tuple[Fact, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


def _parse_installed(freeze_text: str) -> tuple[Fact, ...]:
    facts: list[Fact] = []
    for raw in freeze_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if "==" in line:
            name, _, ver = line.partition("==")
            name = name.strip()
            if name:
                facts.append(Fact(name=name, detail=ver.strip()))
    return tuple(facts)


def probe_env(exec_readonly: Callable[[str], tuple[int, str]]) -> EnvSnapshot:
    try:
        result = run_extractor(exec_readonly, _SNAPSHOT_FIELDS)
    except Exception:
        return EnvSnapshot()
    fields = result.fields  # only rc==0, non-empty entries
    installed = _parse_installed(fields.get("installed_pip", ""))
    env = {k: v for k, v in fields.items() if k != "installed_pip"}
    return EnvSnapshot(installed=installed, env=env)
