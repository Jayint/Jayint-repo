"""Phase-0: the dep-graph advisory is spliced into the planner prompt."""

from src.envstate.planner import render_planning_view
from src.envstate.world_model import initial_map, merge_map

ADVISORY = (
    "[DEPENDENCY GRAPH - advisory * host-certified in scratch container]\n"
    "FRONTIER (unsatisfied - act here):\n"
    "  SYSTEM    libgl1   [SystemLib]  MISSING\n"
    "            fix-candidate: apt:libgl1"
)


def test_advisory_appears_when_set():
    m = merge_map(initial_map("img", "/r", "py", "pip", ()), dep_advisory=ADVISORY)
    view = render_planning_view(m, {"cycles_remaining": 5})
    assert "## dependency_graph (advisory" in view
    assert "libgl1" in view
    assert "fix-candidate: apt:libgl1" in view


def test_off_state_is_byte_identical():
    """Empty dep_advisory must not change the prompt at all (default off)."""
    m = initial_map("img", "/r", "py", "pip", ())
    assert m.dep_advisory == ""
    view = render_planning_view(m, {"cycles_remaining": 5})
    assert "dependency_graph (advisory" not in view
    # explicitly: a map with "" advisory renders the same as the default map
    m_empty = merge_map(m, dep_advisory="")
    assert render_planning_view(m_empty, {"cycles_remaining": 5}) == view
