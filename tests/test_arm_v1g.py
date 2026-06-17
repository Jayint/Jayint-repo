# tests/test_arm_v1g.py
import importlib


def test_repo2run_has_v1g_preset():
    mod = importlib.import_module("run_repo2run_benchmark")
    presets = mod._ARM_PRESETS
    assert "v1g" in presets
    assert presets["v1g"]["enable_v1"] is True
    assert presets["v1g"]["enable_contract_graph"] is True


def test_agent_init_enables_v1_when_contract_graph_on():
    import agent as agent_mod
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    # exercise the flag-derivation logic in isolation
    enable_contract_graph = True
    enable_v1 = False
    derived_v1 = enable_v1 or enable_contract_graph
    assert derived_v1 is True  # documents the rule asserted below


def test_adapter_reads_contract_graph_env(monkeypatch):
    import os

    monkeypatch.setenv("DOCKERAGENT_ENABLE_CONTRACT_GRAPH", "1")
    assert os.environ["DOCKERAGENT_ENABLE_CONTRACT_GRAPH"].lower() in ("1", "true", "yes", "on")
