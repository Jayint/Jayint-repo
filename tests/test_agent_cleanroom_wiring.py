import unittest
from types import SimpleNamespace

from agent import DockerAgent


class _FakeSynth:
    def generate_dockerfile(self, *a, **k):
        return "FROM python:3.11-slim\nCOPY . /app\n"


class AgentCleanroomWiringTests(unittest.TestCase):
    def _agent(self, build_ok):
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_cleanroom = True
        agent.synthesizer = _FakeSynth()
        agent.env_snapshot = None
        agent.verified_test_commands = ["pytest -q"]
        agent.workplace = "/tmp"  # any existing dir; fake build ignores it

        class _Images:
            def build(self, **kwargs):
                if not build_ok:
                    raise RuntimeError("boom")
                return ("img", iter([]))

        agent.sandbox = SimpleNamespace(client=SimpleNamespace(
            images=_Images(),
            containers=SimpleNamespace(run=lambda *a, **k: b"ok"),
        ))
        return agent

    def test_passes_when_build_and_tests_pass(self):
        self.assertTrue(self._agent(build_ok=True)._verify_cleanroom_or_fail())

    def test_fails_when_build_fails(self):
        agent = self._agent(build_ok=False)
        self.assertFalse(agent._verify_cleanroom_or_fail())
        self.assertFalse(agent.run_summary_cleanroom["passed"])

    def test_disabled_returns_true(self):
        agent = self._agent(build_ok=False)
        agent.enable_cleanroom = False
        self.assertTrue(agent._verify_cleanroom_or_fail())
