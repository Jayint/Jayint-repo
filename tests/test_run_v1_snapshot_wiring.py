# tests/test_run_v1_snapshot_wiring.py
"""Verify _run_v1 parses the manifest from self.workplace and passes a probe
+ manifest into run_v1. We monkeypatch run_v1 to capture kwargs, so no Docker.
"""
import types
import agent as agent_mod


def test_run_v1_passes_probe_and_manifest(monkeypatch, tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")

    captured = {}

    def fake_run_v1(*args, **kwargs):
        captured["probe"] = kwargs.get("probe")
        captured["manifest"] = kwargs.get("manifest")
        from src.envstate.world_model import initial_map
        m = initial_map(base_image="python:3.12", workdir="/app", language="python",
                        build_system="unknown", repo_layout=())
        return m, "planner_giveup"

    monkeypatch.setattr("src.envstate.orchestrator.run_v1", fake_run_v1, raising=True)

    # Minimal DockerAgent stand-in carrying just what _run_v1 reads.
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    a.workplace = str(tmp_path)
    a.logs_dir = str(tmp_path)
    a.model = "m"
    a.client = object()
    a.synthesizer = types.SimpleNamespace(base_image="python:3.12", workdir="/app")
    a.action_ledger = __import__("src.envstate.ledger", fromlist=["ActionLedger"]).ActionLedger()
    a.sandbox = types.SimpleNamespace(
        exec_readonly=lambda cmd: (1, ""),
        execute=lambda cmd: (True, ""),
        close=lambda keep_alive=False: None,
    )
    a.verified_test_commands = []
    a.verification_bundle = None
    a.env_container_id = "x"
    # methods _run_v1 calls during finalize/teardown — stub to no-ops
    a._record_supervisor_path_usage = lambda *x, **k: None
    a._auto_finalize_from_verified_tests = lambda *x, **k: False
    a._finalize_supervisor_artifacts = lambda ok: ok
    a._write_run_summary = lambda *x, **k: None
    a._is_transient_llm_error = lambda e: False

    a._run_v1(max_cycles=1)

    assert captured["manifest"] is not None
    assert "flask" in {f.name.lower() for f in captured["manifest"].required}
    assert callable(captured["probe"])
