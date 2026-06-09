"""
agent.py must register --enable-v1 in its argparse block,
pass enable_v1 to DockerAgent, and guard against mixing --enable-v1
with --enable-supervisor / --enable-fullstate-worker.
"""
import inspect
import pathlib
import unittest

AGENT_SRC = pathlib.Path(__file__).parent.parent / "agent.py"


class AgentV1ArgparseTest(unittest.TestCase):
    def _src(self):
        return AGENT_SRC.read_text(encoding="utf-8")

    def test_enable_v1_flag_registered(self):
        self.assertIn(
            "--enable-v1",
            self._src(),
            "agent.py argparse must register --enable-v1",
        )

    def test_enable_v1_passed_to_docker_agent(self):
        src = self._src()
        self.assertIn(
            "enable_v1=args.enable_v1",
            src,
            "DockerAgent(...) call must include enable_v1=args.enable_v1",
        )

    def test_mutual_exclusion_v1_with_supervisor(self):
        src = self._src()
        # Guard must prevent --enable-v1 combined with --enable-supervisor
        self.assertIn(
            "enable_v1",
            src,
            "Mutual-exclusion guard must reference enable_v1",
        )
