# tests/test_run_v1_cycle_trace.py
"""_run_v1 must persist a structured per-cycle component trace (NL excluded):
cycle 0 = grounded initial map (Planner's first input); each later line =
planner decision + build report + the post-cycle state map (Maintainer output).

We monkeypatch run_v1 to invoke the on_cycle callback, so no Docker/LLM.
"""
import json
import os
import types

import agent as agent_mod


def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_run_v1_writes_structured_cycle_trace(monkeypatch, tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")

    def fake_run_v1(*args, **kwargs):
        on_cycle = kwargs.get("on_cycle")
        from src.envstate.world_model import (
            initial_map, merge_map, Fact, PlannerDecision, Task, TaskReport, CommandRecord,
        )
        m = initial_map(base_image="python:3.12", workdir="/app", language="python",
                        build_system="unknown", repo_layout=())
        m1 = merge_map(m, installed=(Fact("flask", "3.0.0"),))
        decision = PlannerDecision(action="task",
                                   task=Task("install flask", "import flask works", "deps", ()))
        report = TaskReport(task_goal="install flask", status="done",
                            commands=(CommandRecord("pip install flask", 0,
                                                    "Successfully installed flask-3.0.0"),),
                            learning="installed flask")
        assert on_cycle is not None, "agent._run_v1 must pass on_cycle"
        on_cycle(1, m1, decision, report)
        on_cycle(2, m1, PlannerDecision(action="done", reason="deps satisfied"), None)
        return m1, "planner_done"

    monkeypatch.setattr("src.envstate.orchestrator.run_v1", fake_run_v1, raising=True)

    logs_dir = str(tmp_path / "logs")
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    a.workplace = str(tmp_path)
    a.logs_dir = logs_dir
    a.model = "m"
    a.client = object()
    a.synthesizer = types.SimpleNamespace(base_image="python:3.12", workdir="/app")
    a.action_ledger = __import__("src.envstate.ledger", fromlist=["ActionLedger"]).ActionLedger()
    a.sandbox = types.SimpleNamespace(
        exec_readonly=lambda cmd: (1, ""),         # empty probe -> degrade (no Docker)
        execute=lambda cmd: (True, ""),
        close=lambda keep_alive=False: None,
    )
    a.verified_test_commands = []
    a.verification_bundle = None
    a.env_container_id = "x"
    a._record_supervisor_path_usage = lambda *x, **k: None
    a._auto_finalize_from_verified_tests = lambda *x, **k: False
    a._finalize_supervisor_artifacts = lambda ok: ok
    a._write_run_summary = lambda *x, **k: None
    a._is_transient_llm_error = lambda e: False

    a._run_v1(max_cycles=2)

    trace_path = os.path.join(logs_dir, "setup_logs", "envstate_cycles.jsonl")
    assert os.path.exists(trace_path), "per-cycle trace file must be written"
    recs = _read_jsonl(trace_path)

    # cycle 0 = grounded initial map (planner's first input): manifest facts folded in
    assert recs[0]["cycle"] == 0
    assert recs[0]["planner"] is None
    assert recs[0]["state_map"]["build_system"] == "pip"
    assert "flask" in {f["name"].lower() for f in recs[0]["state_map"]["required"]}

    # cycle 1 = planner decision + build report + post-cycle state map
    c1 = next(r for r in recs if r["cycle"] == 1)
    assert c1["planner"]["action"] == "task"
    assert c1["planner"]["task"]["goal"] == "install flask"
    assert c1["build_report"]["commands"][0]["rc"] == 0
    assert "flask" in {f["name"].lower() for f in c1["state_map"]["installed"]}

    # cycle 2 = planner ended the run
    c2 = next(r for r in recs if r["cycle"] == 2)
    assert c2["planner"]["action"] == "done"
    assert c2["build_report"] is None

    # NL prompts are excluded from the trace
    raw = open(trace_path, encoding="utf-8").read()
    assert "You are the" not in raw
