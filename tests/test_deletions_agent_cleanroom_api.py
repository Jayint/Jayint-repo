"""
After the migration, agent.py:_verify_cleanroom_or_fail must NOT import
from src.envstate.probes.  It must pass probe_commands=[...] (list of
pre-built command strings) to verify_cleanroom, not probes=[ProbeSpec(...)].
"""
import inspect
import pathlib
import unittest

AGENT_SRC = pathlib.Path(__file__).parent.parent / "agent.py"


class AgentCleanroomApiTest(unittest.TestCase):
    def test_agent_does_not_import_probespec_in_verify_cleanroom(self):
        text = AGENT_SRC.read_text(encoding="utf-8")
        # The _verify_cleanroom_or_fail method previously imported ProbeSpec
        # inside its body (agent.py:1116).  After migration that import is gone.
        self.assertNotIn(
            "from src.envstate.probes import ProbeSpec",
            text,
            "_verify_cleanroom_or_fail must not import ProbeSpec from probes.py",
        )

    def test_verify_cleanroom_called_with_probe_commands_kwarg(self):
        text = AGENT_SRC.read_text(encoding="utf-8")
        # The call site must use probe_commands= not probes=
        self.assertIn(
            "probe_commands=",
            text,
            "agent.py must call verify_cleanroom with probe_commands= kwarg",
        )
        self.assertNotIn(
            "probes=probes",
            text,
            "agent.py must not pass probes=probes to verify_cleanroom",
        )
