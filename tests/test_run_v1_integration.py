"""Integration test for DockerAgent._run_v1 — REAL role construction + loop.

Regression guard for the bug where _run_v1 constructed
``BuildAgent(sandbox=..., ledger=...)`` — kwargs the real ``BuildAgent.__init__``
does not accept (it takes ``synthesizer``; the sandbox executor and ActionLedger
are passed to the run_v1 loop per-task). The dispatch glue tests mock ``_run_v1``
wholesale and the unit tests construct ``BuildAgent`` directly, so neither ever
exercised the real ``_run_v1`` construction. Only a real ``--enable-v1`` run hit
it. This test drives the REAL ``_run_v1`` end to end, faking only the two genuine
external boundaries (the LLM client and the sandbox), so the real Planner /
BuildAgent / Maintainer are constructed exactly as production does.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent as _agent_module  # noqa: E402
from src.envstate.ledger import ActionLedger  # noqa: E402


# --- Fake OpenAI-compatible client (dispatch by role system prompt) ----------
class _Msg:
    def __init__(self, c): self.content = c; self.reasoning = None
class _Choice:
    def __init__(self, c): self.message = _Msg(c)
class _Usage:
    prompt_tokens = 10; completion_tokens = 5; total_tokens = 15
class _Resp:
    def __init__(self, c): self.choices = [_Choice(c)]; self.usage = _Usage()
class _Completions:
    def __init__(self, p): self._p = p
    def create(self, model=None, messages=None, **kw): return self._p._dispatch(messages)
class _Chat:
    def __init__(self, p): self.completions = _Completions(p)


class FakeLLM:
    def __init__(self): self.chat = _Chat(self)
    def _dispatch(self, messages):
        system = messages[0]["content"] if messages else ""
        if "You are the Planner" in system:
            return _Resp(json.dumps({
                "action": "task", "goal": "reach the pytest --collect-only gate",
                "done_when": "pytest --collect-only -q exits 0",
                "layer": "tests", "facts": [],
            }))
        if "skilled in environment configuration" in system:
            # Deliberately never emits "Final Answer" — proves termination comes
            # from the map's done_flag, not the agent declaring success.
            return _Resp("Thought: probe the collection gate.\n"
                         "Action: pytest --collect-only -q --disable-warnings")
        if "State Maintainer" in system:
            return _Resp("```json\n" + json.dumps({
                "installed": [], "open_problems": [],
                "progress": {"tests": True}, "notes": [],
            }) + "\n```")
        return _Resp("")


class FakeSandbox:
    def __init__(self): self.closed = False
    def execute(self, cmd):
        if "--collect-only" in cmd:
            return True, "collected 7 items / 7 selected\n"
        return True, "ok\n"
    def exec_readonly(self, cmd):
        # Read-only env probe (snapshot.probe_env -> extractor.run_extractor).
        # Off the ledger; folded into the map by apply_deterministic each cycle.
        if "uname -m" in cmd:
            return 0, "x86_64\n"
        if "python --version" in cmd:
            return 0, "Python 3.12.1\n"
        if "pip list --format=freeze" in cmd:
            return 0, "flask==3.0.0\n"
        return 1, ""
    def close(self, keep_alive=False): self.closed = True


class FakeSynth:
    base_image = "python:3.12-slim"
    workdir = "/app"
    def command_mutates_environment(self, cmd): return False
    def classify_mutation(self, cmd): return "noop"


class RunV1IntegrationTest(unittest.TestCase):
    def test_run_v1_constructs_real_roles_and_finalizes_on_done_flag(self):
        agent = _agent_module.DockerAgent.__new__(_agent_module.DockerAgent)
        agent.logs_dir = tempfile.mkdtemp()
        agent.workplace = tempfile.mkdtemp()  # host FS root for parse_manifests()
        agent.client = FakeLLM()
        agent.model = "fake-model"
        agent.synthesizer = FakeSynth()
        agent.base_image = "python:3.12-slim"
        agent.sandbox = FakeSandbox()
        agent.action_ledger = ActionLedger()
        agent.env_container_id = "testcontainer"
        agent.verified_test_commands = []
        agent.verification_bundle = None

        calls = {"finalize": [], "artifacts": 0, "summary": 0}
        agent._record_supervisor_path_usage = lambda *a, **k: None
        def _auto(source=None, *a, **k):
            calls["finalize"].append(source)
            return True
        agent._auto_finalize_from_verified_tests = _auto
        agent._finalize_supervisor_artifacts = (
            lambda *a, **k: (calls.__setitem__("artifacts", calls["artifacts"] + 1) or True))
        agent._write_run_summary = (
            lambda *a, **k: calls.__setitem__("summary", calls["summary"] + 1))

        # Must NOT raise. The pre-fix bug raised
        # "TypeError: BuildAgent.__init__() got an unexpected keyword argument 'sandbox'"
        # here, before the loop ever ran.
        result = agent._run_v1(max_cycles=1)

        # The orchestrator finalized via the structural done_flag path.
        self.assertIn("v1_done_flag", calls["finalize"],
                      "run_v1 must finalize via the done_flag path")
        # The ledger captured the build agent's collect-only command (the
        # Dockerfile source of truth) and it was promoted to verified commands.
        self.assertTrue(
            any("--collect-only" in c for c in agent.verified_test_commands),
            "verified_test_commands must be populated from the ledger collect-only scan")
        self.assertTrue(any("--collect-only" in e.cmd for e in agent.action_ledger.events()),
                        "build agent must have executed + recorded a collect-only command")
        self.assertTrue(agent.sandbox.closed, "sandbox must be closed in the finally block")
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
