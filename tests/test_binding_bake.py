import os, sys
from pathlib import Path
# Repo root + src/ must both be importable (brief's parents[0] is the tests/
# dir, which has neither agent.py nor python_deps; use parents[1]).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _RecSynth:
    def __init__(self): self.calls = []
    def add_env_instruction(self, name, value): self.calls.append((name, value))


def test_binding_value_baked_last(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    import agent as agent_mod
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.ids import config_id
    binding = Node(id=config_id("DB_STRING"), type=NodeType.CONFIG, name="DB_STRING",
                   layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
                   check_command="psql", fix_candidates=("env:DB_STRING=URL",), chosen_fix="env:DB_STRING=URL",
                   evidence="x", provenance="service binding",
                   data={"binding": True, "bind_recipe": {"var": "DB_STRING", "url": "URL_BOUND"}})
    da = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    da.synthesizer = _RecSynth(); da.action_ledger = None
    da._final_dep_graph = DepGraph(nodes=(binding,), edges=())
    da._bake_test_env_vars()
    assert ("DB_STRING", "URL_BOUND") in da.synthesizer.calls
    # binding is the LAST writer for DB_STRING
    db_calls = [v for (n, v) in da.synthesizer.calls if n == "DB_STRING"]
    assert db_calls[-1] == "URL_BOUND"


def test_binding_off_arm_bakes_nothing(monkeypatch):
    """Off-arm: the binding pass must not fire (no URL baked)."""
    monkeypatch.delenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", raising=False)
    import agent as agent_mod
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.ids import config_id
    binding = Node(id=config_id("DB_STRING"), type=NodeType.CONFIG, name="DB_STRING",
                   layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
                   check_command="psql", fix_candidates=("env:DB_STRING=URL",), chosen_fix="env:DB_STRING=URL",
                   evidence="x", provenance="service binding",
                   data={"binding": True, "bind_recipe": {"var": "DB_STRING", "url": "URL_BOUND"}})
    da = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    da.synthesizer = _RecSynth(); da.action_ledger = None
    da._final_dep_graph = DepGraph(nodes=(binding,), edges=())
    da._bake_test_env_vars()
    assert ("DB_STRING", "URL_BOUND") not in da.synthesizer.calls


def test_unsatisfied_binding_bakes_nothing(monkeypatch):
    """Anti-hollow: an UNSATISFIED binding (host didn't certify) bakes no URL."""
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    import agent as agent_mod
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.ids import config_id
    binding = Node(id=config_id("DB_STRING"), type=NodeType.CONFIG, name="DB_STRING",
                   layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
                   check_command="psql", fix_candidates=("env:DB_STRING=URL",), chosen_fix="env:DB_STRING=URL",
                   evidence="x", provenance="service binding",
                   data={"binding": True, "bind_recipe": {"var": "DB_STRING", "url": "URL_BOUND"}})
    da = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    da.synthesizer = _RecSynth(); da.action_ledger = None
    da._final_dep_graph = DepGraph(nodes=(binding,), edges=())
    da._bake_test_env_vars()
    assert ("DB_STRING", "URL_BOUND") not in da.synthesizer.calls
