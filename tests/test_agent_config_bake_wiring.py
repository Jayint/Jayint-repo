# tests/test_agent_config_bake_wiring.py
from pathlib import Path

_AGENT = (Path(__file__).resolve().parents[1] / "agent.py").read_text()


def test_final_dep_graph_is_stored_from_final_map():
    # The agent must capture the live graph off final_map so the bake can read it.
    assert "self._final_dep_graph = getattr(final_map, \"dep_graph\", None)" in _AGENT
    # And default it so it is always defined (exception path), like _final_installed.
    assert "self._final_dep_graph = None" in _AGENT


def test_bake_uses_bakeable_config_env_with_ledger_precedence():
    src = _AGENT
    # The CONFIG bake must be reachable from _bake_test_env_vars and pass exclude=
    # (the names already baked from the ledger) so the ledger source wins.
    bake = src[src.index("def _bake_test_env_vars"):src.index("def _verify_cleanroom_or_fail")]
    assert "bakeable_config_env(" in bake
    assert "exclude=" in bake
    assert "_final_dep_graph" in bake
