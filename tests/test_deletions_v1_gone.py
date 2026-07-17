"""Deletion sentinel — the legacy planner-driven loop must stay GONE (Phase 0 shed, T6).

Guards the T6 shed: the ``run_v1`` symbol is absent from the orchestrator, ``run_v3`` (the sole
loop) remains, and no OTHER surviving source/test file names the ``run_v1`` token. This file is the
one justified home of the token (it exists to assert the token's absence) — mirroring the repo's
existing ``test_deletions_*_gone.py`` convention. Its sibling ``tests/envstate/test_v1_shed_shared_helpers.py``
pins what must SURVIVE; this pins what must be GONE. (Extended by T7 for the ContractGraph shed.)
"""
from __future__ import annotations

import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
for _p in (str(_ROOT), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SELF = pathlib.Path(__file__).resolve()
_V1_TOKEN = re.compile(r"\brun_v1\b")


def test_run_v1_symbol_absent_from_orchestrator():
    from src.envstate import orchestrator
    assert not hasattr(orchestrator, "run_v1"), "run_v1 must be deleted from the orchestrator"
    assert hasattr(orchestrator, "run_v3"), "run_v3 (the sole loop) must remain"


def test_no_surviving_source_or_test_names_run_v1():
    """No .py under src/ or tests/ names the run_v1 token — this sentinel excepted (it must name
    the token to assert its absence). A resurrected reference fails here."""
    offenders = []
    for base in (_ROOT / "src", _ROOT / "tests"):
        for py in base.rglob("*.py"):
            if py.resolve() == _SELF or "__pycache__" in py.parts:
                continue
            if _V1_TOKEN.search(py.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(py.relative_to(_ROOT)))
    assert offenders == [], f"surviving run_v1 references (shed incomplete): {offenders}"
