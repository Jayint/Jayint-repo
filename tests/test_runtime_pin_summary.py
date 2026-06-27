import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from agent import _runtime_pin_summary  # noqa: E402
from src.envstate.runtime_base import RuntimeBaseDecision  # noqa: E402
from python_deps.depgraph.ids import runtime_id  # noqa: E402
from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)


def _live_graph(state):
    n = Node(id=runtime_id("3.10"), type=NodeType.RUNTIME, name="python 3.10",
             layer=Layer.RUNTIME, discovered_by=DiscoveredBy.STATIC_SCAN, state=state,
             version="3.10", check_command="x")
    return DepGraph(nodes=(n,), edges=())


def _decision(base="python:3.10-slim"):
    return RuntimeBaseDecision(minor="3.10", base_image=base, reason="floor",
                              requires_python=">=3.10")


def test_none_decision_returns_none():
    assert _runtime_pin_summary(_live_graph(State.SATISFIED), None, None) is None


def test_reports_certified_from_live_graph_and_base_changed():
    s = _runtime_pin_summary(_live_graph(State.SATISFIED), _decision(), "python:3.11-slim")
    assert s == {
        "required": "3.10", "reason": "floor",
        "original_base": "python:3.11-slim", "pinned_base": "python:3.10-slim",
        "base_changed": True, "certified": "satisfied",
    }


def test_reports_missing_and_base_unchanged():
    s = _runtime_pin_summary(_live_graph(State.MISSING), _decision(base="python:3.11-slim"),
                             "python:3.11-slim")
    assert s["certified"] == "missing"
    assert s["base_changed"] is False


def test_no_live_graph_certified_none():
    s = _runtime_pin_summary(None, _decision(), "python:3.11-slim")
    assert s["certified"] is None
    assert s["base_changed"] is True
