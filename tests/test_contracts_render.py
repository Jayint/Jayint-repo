from src.envstate.contracts.render import render_graph_for_planner, serialize_graph_for_maintainer
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge


def _g() -> ContractGraph:
    return ContractGraph(
        nodes=(Node("contract:goal:repo_tests_pass", "Contract",
                    {"level": "goal", "required": True, "layer": "tests", "kind": "tests_pass"}),
               Node("contract:python_import:cv2", "Contract",
                    {"level": "atomic", "layer": "deps", "kind": "python_import", "subject": "cv2"}),
               Node("blocker:libgl", "Blocker", {"signature": "ImportError: libGL.so.1",
                    "active": True, "root_or_downstream": "root", "summary": "libGL missing", "layer": "system"})),
        edges=(Edge("contract:goal:repo_tests_pass", "depends_on", "contract:python_import:cv2"),
               Edge("blocker:libgl", "violates", "contract:python_import:cv2")))


def test_planner_render_has_three_sections_and_root_blocker():
    out = render_graph_for_planner(_g(), frozenset())
    assert "Repair Map" in out and "Repair Frontier" in out
    assert "blocker:libgl" in out and "violated" in out


def test_maintainer_serializer_has_no_status_events():
    d = serialize_graph_for_maintainer(_g())
    assert set(d) == {"contracts", "blockers", "attempts", "edges"}


def test_planner_render_surfaces_diagnostic_notes():
    """Maintainer diagnostic_notes must reach the planner (not be a dead-end)."""
    import dataclasses
    g = dataclasses.replace(
        _g(),
        diagnostic_notes=("pg_config missing blocks psycopg2 build",
                          "pip install opencv succeeded but cv2 still fails"),
    )
    out = render_graph_for_planner(g, frozenset())
    assert "Recent Diagnoses" in out
    assert "pg_config missing blocks psycopg2 build" in out
    assert "pip install opencv succeeded but cv2 still fails" in out


def test_planner_render_omits_diagnoses_section_when_empty():
    out = render_graph_for_planner(_g(), frozenset())
    assert "Recent Diagnoses" not in out


def test_planner_render_next_target_block():
    # _g(): repo_tests_pass -> cv2, blocker:libgl violates cv2 (cv2 violated, leaf).
    out = render_graph_for_planner(_g(), frozenset())
    assert "## Next Target" in out
    assert "contract:python_import:cv2 [violated]" in out
    assert "violated by blocker:libgl" in out
    assert "tried: none (untried)" in out


def test_planner_render_next_target_flags_ineffective_attempt():
    import dataclasses
    g = _g()
    att = Node("attempt:install-cv2", "Attempt",
               {"kind": "python_install", "commands": ["pip install opencv"],
                "outcome": "ok_but_still_blocked"})
    g = dataclasses.replace(
        g,
        nodes=g.nodes + (att,),
        edges=g.edges + (Edge("attempt:install-cv2", "addresses", "contract:python_import:cv2"),),
    )
    out = render_graph_for_planner(g, frozenset())
    assert "last outcome ok_but_still_blocked" in out
    assert "try a different fix" in out
