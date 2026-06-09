# tests/test_agent_v1_build_observer.py
"""Unit tests for the _build_observer ledger-append helper used in v1.

In the v1 path, _build_observer is replaced by a thin ledger-append closure
that simply records ActionEvent entries into the ActionLedger without invoking
the Maintainer's per-action `interpret` (that is now Maintainer.update's job,
called once per cycle by run_v1). The thin helper is used as the sandbox
step_fn for BuildAgent.

Tests verify:
  1. _build_v1_ledger_appender returns a callable.
  2. Calling the returned closure with (cmd, rc, stdout) appends an ActionEvent
     to the ActionLedger.
  3. The appended ActionEvent has the correct cmd, rc, and stdout fields.
  4. Multiple calls append multiple events in order.
"""
from __future__ import annotations

import types
import sys
import unittest
from unittest.mock import MagicMock, patch

# Re-use the stub installer from test_agent_v1_glue to avoid import side effects.
# We duplicate the minimum needed to keep this file self-contained.

def _make_stub_module(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    return m


def _install_stubs() -> None:
    for mod_name in [
        "src.sandbox", "src.synthesizer", "src.image_selector",
        "src.verification_bundle", "src.constants", "src.memory_manager",
        "src.observation_compressor", "src.planner", "dotenv", "openai",
    ]:
        # Always install a FRESH stub so the attribute assignments below never
        # mutate a real, already-imported module (which would corrupt it for the
        # rest of the suite). sys.modules is restored right after `import agent`.
        sys.modules[mod_name] = _make_stub_module(mod_name)
    sys.modules["src.constants"].DEFAULT_LLM_MODEL = "test-model"
    sys.modules["src.constants"].DEFAULT_MEMORY_EMBEDDING_MODEL = "test-embed"
    oc = sys.modules["src.observation_compressor"]
    oc.AgentStep = object
    oc.ObservationCompressor = MagicMock
    oc.RunTokenLedger = MagicMock
    oc.build_observation_metadata = MagicMock(return_value={})
    oc.safety_compress_observation = MagicMock(return_value=("obs", False))
    oc.should_apply_compression = MagicMock(return_value=False)
    sys.modules["dotenv"].load_dotenv = lambda **kw: None
    sys.modules["openai"].OpenAI = MagicMock
    sys.modules["src.synthesizer"].Synthesizer = MagicMock
    sys.modules["src.image_selector"].ImageSelector = MagicMock
    sys.modules["src.verification_bundle"].derive_supported_verification_bundle = MagicMock(
        return_value={"test_commands": ["pytest --collect-only -q --disable-warnings"]}
    )
    sys.modules["src.memory_manager"].LongTermMemoryManager = MagicMock
    sys.modules["src.planner"].Planner = MagicMock
    sys.modules["src.sandbox"].Sandbox = MagicMock


_STUBBED_MODULE_NAMES = [
    "src.sandbox", "src.synthesizer", "src.image_selector",
    "src.verification_bundle", "src.constants", "src.memory_manager",
    "src.observation_compressor", "src.planner", "dotenv", "openai",
]
_SYS_MODULES_BEFORE_STUBS = {n: sys.modules.get(n) for n in _STUBBED_MODULE_NAMES}
_install_stubs()

import agent as _agent_module

# Restore sys.modules so these import-time stubs do not leak into other test
# modules (which must import the real heavy deps).
for _name, _orig in _SYS_MODULES_BEFORE_STUBS.items():
    if _orig is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _orig

from src.envstate.ledger import ActionLedger


# AUTHORIZED TEST-HELPER CORRECTION: Use __new__ bypass instead of calling
# __init__ directly. The plan-provided helper patched _init_planner which does
# NOT exist on DockerAgent (the Planner is built inline in __init__), and
# __init__ raises ValueError for missing API keys. Since these tests only need
# a constructed instance to call _build_v1_ledger_appender, the __new__-bypass
# pattern (established in tests/test_agent_flags.py) is appropriate.
def _make_agent_instance(**kwargs):
    agent = _agent_module.DockerAgent.__new__(_agent_module.DockerAgent)
    # Set the minimal attrs needed for behavioral tests of _build_v1_ledger_appender
    agent.enable_v1 = kwargs.get("enable_v1", False)
    agent.enable_envstate = kwargs.get("enable_envstate", agent.enable_v1)
    agent.action_ledger = None
    for k, v in kwargs.items():
        setattr(agent, k, v)
    return agent


class TestBuildV1LedgerAppender(unittest.TestCase):
    """_build_v1_ledger_appender must return a closure that appends ActionEvents."""

    def test_method_exists_on_docker_agent(self):
        """DockerAgent must expose _build_v1_ledger_appender."""
        agent = _make_agent_instance(enable_v1=True)
        self.assertTrue(
            hasattr(agent, "_build_v1_ledger_appender"),
            "DockerAgent must have _build_v1_ledger_appender method",
        )

    def test_returns_callable(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        self.assertTrue(callable(appender))

    def test_appended_event_has_correct_cmd(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("pip install .", 0, "Successfully installed")
        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].cmd, "pip install .")

    def test_appended_event_has_correct_rc(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("pip install .", 0, "ok")
        self.assertEqual(ledger.events()[0].rc, 0)

    def test_appended_event_has_correct_stdout(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("pytest --collect-only -q --disable-warnings", 0, "5 items collected")
        self.assertEqual(ledger.events()[0].stdout, "5 items collected")

    def test_multiple_calls_append_in_order(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("cmd1", 0, "out1")
        appender("cmd2", 1, "out2")
        appender("cmd3", 0, "out3")
        events = ledger.events()
        self.assertEqual(len(events), 3)
        self.assertEqual([e.cmd for e in events], ["cmd1", "cmd2", "cmd3"])
        self.assertEqual([e.rc for e in events], [0, 1, 0])

    def test_failed_command_rc_nonzero_is_stored(self):
        """Non-zero rc must be stored faithfully — no filtering on success."""
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("python setup.py install", 1, "error: command failed")
        self.assertEqual(ledger.events()[0].rc, 1)


if __name__ == "__main__":
    unittest.main()
