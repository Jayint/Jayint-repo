"""Deletion sentinel — the legacy planner loop AND the ContractGraph layer must stay GONE
(Phase 0 shed, T6 + T7).

Guards both sheds: the ``run_v1`` symbol is absent from the orchestrator, the ``envstate/contracts/``
ContractGraph layer is deleted, ``WorldModelMap`` has no ``contract_graph`` field, the
``DeterministicMaintainer`` (folded into ``done_gate``, 3b-5) keeps only its done-gate (no ``maintain``/``build_blocker_patch``), and
no OTHER surviving source/test file names the legacy tokens (``run_v1``/``ContractGraph``/
``contract_graph``/``derive_open_problems``). This file is the one justified home of those tokens (it
exists to assert their absence) — mirroring the repo's ``test_deletions_*_gone.py`` convention. Its
sibling ``tests/envstate/test_v1_shed_shared_helpers.py`` pins what must SURVIVE; this pins GONE.
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
_LEGACY_TOKEN = re.compile(r"\brun_v1\b|ContractGraph|contract_graph|derive_open_problems")


def test_run_v1_symbol_absent_from_orchestrator():
    from src.orchestrate.loop import orchestrator
    assert not hasattr(orchestrator, "run_v1"), "run_v1 must be deleted from the orchestrator"
    assert hasattr(orchestrator, "run_v3"), "run_v3 (the sole loop) must remain"


def test_contract_graph_layer_and_field_are_excised():
    """The v1 ContractGraph layer is gone: the package dir, the world-model field, the
    maintainer's blocker path, and the derived view."""
    from src.orchestrate.loop import world_model
    from src.orchestrate.loop.done_gate import DeterministicMaintainer
    import src.orchestrate.loop.done_gate as dm

    assert not (_SRC / "envstate" / "contracts").exists(), "envstate/contracts/ layer must be deleted"
    m = world_model.initial_map(base_image="b", workdir="/r", language="py",
                                build_system="pip", repo_layout=())
    assert not hasattr(m, "contract_graph"), "WorldModelMap must have no contract_graph field"
    assert not hasattr(world_model, "derive_open_problems"), "derive_open_problems must be gone"
    assert not hasattr(dm, "maintain") and not hasattr(dm, "build_blocker_patch"), (
        "the ContractGraph blocker path (maintain/build_blocker_patch) must be excised")
    assert hasattr(DeterministicMaintainer, "update"), "the done-gate maintainer shell must remain"


def test_no_surviving_source_or_test_names_the_legacy_tokens():
    """No .py under src/ or tests/ names run_v1/ContractGraph/contract_graph/derive_open_problems —
    this sentinel excepted (it must name them to assert their absence). A resurrected reference
    fails here. (The benchmark-arm env flag `DOCKERAGENT_ENABLE_CONTRACT_GRAPH` was also shed, so the
    uppercase form no longer appears either.)"""
    offenders = []
    for base in (_ROOT / "src", _ROOT / "tests"):
        for py in base.rglob("*.py"):
            if py.resolve() == _SELF or "__pycache__" in py.parts:
                continue
            if _LEGACY_TOKEN.search(py.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(py.relative_to(_ROOT)))
    assert offenders == [], f"surviving legacy references (shed incomplete): {offenders}"
