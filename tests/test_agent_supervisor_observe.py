import unittest
from types import SimpleNamespace

from agent import DockerAgent
from src.synthesizer import Synthesizer
from src.envstate.ledger import ActionLedger
# BaseFacts/EnvStateSnapshot/Source/Status removed with types.py (Task 39)
from src.observation_compressor import RunTokenLedger


class _FakeMaintainer:
    """Returns a proposal asking the host to probe `pg_config`."""
    def interpret(self, snapshot, task_spec, action_event, observation):
        proposal = {"probe_requests": [
            {"kind": "cli", "name": "pg_config", "predicate": "path exists",
             "requirement_id": "tool:pg_config"}
        ]}
        return snapshot, proposal, [], {"total_tokens": 0}


@unittest.skip("v0 supervisor/types removed — EnvStateSnapshot/BaseFacts/Source/Status deleted with types.py")
class AgentSupervisorObserveTests(unittest.TestCase):
    def _make_agent(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.synthesizer = Synthesizer()
        agent.successful_test_commands = []
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.verified_runtime_preparation_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.failed_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
        agent._environment_revision = 0
        agent._current_verification_group = []
        agent.required_local_services = set()
        agent.enable_envstate = True
        agent.action_ledger = ActionLedger()
        agent.run_token_ledger = RunTokenLedger()
        agent.current_task_id = "t1"
        agent.env_container_id = "abc123"
        # exec_readonly probe runner: pg_config present (rc 0)
        agent.sandbox = SimpleNamespace(exec_readonly=lambda cmd: (0, "/usr/bin/pg_config\n10.1"))
        return agent

    def test_observer_certifies_present_via_host_probe(self):
        agent = self._make_agent()
        observer = agent._build_observer(_FakeMaintainer())
        snapshot = EnvStateSnapshot(revision=0, container_id="abc123",
                                    base=BaseFacts(image="python:3.11-slim"))
        # a failing pip install triggers maintainer -> probe_request -> host certify
        snapshot = observer(snapshot, {"task_id": "t1"}, 1,
                            "pip install psycopg2==2.8.6", False,
                            "Error: pg_config executable not found")
        req = [r for r in snapshot.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.PRESENT)
        self.assertEqual(req.source, Source.PROBE)
        self.assertIsNotNone(req.evidence)
        self.assertEqual(agent.action_ledger.events()[-1].cmd, "pip install psycopg2==2.8.6")


if __name__ == "__main__":
    unittest.main()
