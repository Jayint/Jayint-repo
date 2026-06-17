# tests/test_maintainer_bug8_patch_error.py
"""BUG-8: _log_patch_error arg-shape mismatch.

on_patch_error must be called ONCE with the full list[str] of validation
errors, not per-scalar (one call per error string).
"""
from src.envstate.maintainer import parse_v1_maintainer_reply
from src.envstate.world_model import initial_map, merge_map
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node


def _report():
    from src.envstate.world_model import TaskReport, CommandRecord
    return TaskReport(
        task_goal="g",
        status="blocked",
        commands=(CommandRecord(cmd="python -c 'import cv2'", rc=1, output="ImportError: libGL.so.1"),),
        learning="blocked",
    )


def _map():
    g = ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),))
    return merge_map(initial_map("b", "/r", "python 3.11", "pip", ("tests/",)), contract_graph=g)


# An invalid blocker patch (empty evidence_refs violates the grounded-blocker rule)
_INVALID_PATCH_REPLY = (
    '```json\n{"graph_patch":{'
    '"add_blockers":[{"id":"blocker:libgl","type":"Blocker",'
    '"signature":"ImportError: libGL.so.1",'
    '"active":true,"layer":"system","summary":"libGL","evidence_refs":[]}],'
    '"add_edges":[{"source":"blocker:libgl","type":"violates",'
    '"target":"contract:python_import:cv2"}]}}\n```'
)


def test_patch_error_callback_receives_list_not_scalar() -> None:
    """on_patch_error must be invoked exactly once with the full list[str],
    not iterated per error string (BUG-8 regression guard)."""
    captured: list = []
    parse_v1_maintainer_reply(
        _INVALID_PATCH_REPLY, _map(), _report(),
        on_patch_error=captured.append,
    )
    # Must be called exactly once (one log entry per rejected patch)
    assert len(captured) == 1, (
        f"Expected on_patch_error called once with full list, got {len(captured)} calls"
    )
    # The single argument must be a list (not a bare string)
    arg = captured[0]
    assert isinstance(arg, list), (
        f"Expected list[str], got {type(arg).__name__!r}: {arg!r}"
    )
    # Every element in the list must be a string
    assert all(isinstance(e, str) for e in arg), (
        f"All error entries must be str, got: {[type(e).__name__ for e in arg]}"
    )
    # There must be at least one validation error (the patch was rejected)
    assert len(arg) >= 1, "Expected at least one validation error in the list"
