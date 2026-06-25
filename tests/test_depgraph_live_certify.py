from __future__ import annotations

import sys
from pathlib import Path

# Put <repo>/src on the path so python_deps.depgraph.* resolves
# (mirrors the pattern in test_world_model_dep_graph.py and tests/depgraph/conftest.py).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.envstate.depgraph_live import certify_refresh, ensure_python_shim  # noqa: E402
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy  # noqa: E402


def _pkg(name):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="1.0",
                check_command=f'python -c "import {name}"')


def test_certify_refresh_flips_state_from_live_checks():
    g = DepGraph(nodes=(_pkg("flask"), _pkg("ghost")))

    def exec_readonly(cmd):
        # flask import succeeds (rc 0); ghost fails (rc 1)
        return (0, "") if "import flask" in cmd else (1, "ModuleNotFoundError: ghost")

    out = certify_refresh(g, exec_readonly, cycle=3)
    assert out.get("pkg:flask").state is State.SATISFIED
    assert out.get("pkg:flask").certified_cycle == 3
    assert out.get("pkg:ghost").state is State.MISSING


def test_certify_refresh_noop_when_disabled_or_empty():
    g = DepGraph(nodes=(_pkg("flask"),))
    assert certify_refresh(g, None, cycle=0) is g          # no executor
    assert certify_refresh(None, lambda c: (0, ""), 0) is None  # no graph


def test_ensure_python_shim_emits_idempotent_symlink():
    # Fix #5: symlink python->python3 so `python -m pip show` certifies on a
    # python3-only base (else installs never certify and the drain re-emits forever).
    calls = []

    def sandbox_execute(cmd):
        calls.append(cmd)
        return True, ""

    ensure_python_shim(sandbox_execute)
    assert len(calls) == 1
    cmd = calls[0]
    assert "command -v python" in cmd          # idempotent: no-op when python exists
    assert "ln -sf" in cmd and "python3" in cmd  # else symlink python -> python3


def test_ensure_python_shim_noop_and_safe_without_executor():
    ensure_python_shim(None)  # must not raise
