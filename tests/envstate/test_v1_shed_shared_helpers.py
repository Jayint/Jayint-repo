"""T5 guard — pin the SHARED orchestrator helpers before the legacy-loop shed (plan Phase 0 / T5).

The legacy three-role planner-driven loop and ``run_v3`` co-lived in ``orchestrator.py`` and shared
two pure helpers — ``host_refresh_facts`` (``_loop_common``) and ``merge_map`` (``world_model``). T6
deletes the legacy loop; this file pins the invariant the shed must preserve: **the shared helpers
stay, and ``run_v3`` keeps using them.** If a T6/T7 edit accidentally strips a shared helper from
``run_v3``, the source-guard below fails.

Behavioral coverage of ``host_refresh_facts`` already lives in ``tests/test_loop_common.py`` and of
``merge_map``/``apply_deterministic`` in ``tests/test_world_model*`` / ``tests/test_apply_deterministic*``;
this guard is the shed-specific complement, not a duplicate. Its sibling ``test_deletions_v1_gone.py``
(added in T6) pins what must be GONE; this pins what must SURVIVE.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for _p in (str(_ROOT), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_shared_helpers_are_importable():
    """The shared helpers survive the shed as first-class, importable functions."""
    from src.envstate._loop_common import host_refresh_facts
    from src.envstate.world_model import apply_deterministic, merge_map
    assert callable(host_refresh_facts) and callable(merge_map) and callable(apply_deterministic)


def test_run_v3_still_uses_the_shared_helpers():
    """Source-guard: ``run_v3`` must keep calling BOTH shared helpers. This is the tripwire the
    shed protects — deleting the legacy loop must not strip ``host_refresh_facts``/``merge_map``
    from the surviving loop (a whole-file-delete of a mixed helper would do exactly that)."""
    from src.envstate import orchestrator
    v3_src = inspect.getsource(orchestrator.run_v3)
    assert "host_refresh_facts(" in v3_src, "run_v3 must keep calling host_refresh_facts"
    assert "merge_map(" in v3_src, "run_v3 must keep calling merge_map"


def test_host_refresh_facts_noop_contract_is_stable():
    """A compact co-located re-pin of the no-op contract both loops relied on (probe/manifest None
    -> same object unchanged). Full behavioral coverage is in tests/test_loop_common.py."""
    from src.envstate._loop_common import host_refresh_facts
    from src.envstate.world_model import initial_map
    m = initial_map(base_image="python:3.11", workdir="/repo", language="python",
                    build_system="pip", repo_layout=())
    assert host_refresh_facts(m, None, None) is m
