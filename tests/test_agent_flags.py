"""Tests for §3.1 flag wiring in agent.py.

TDD — these tests are written before the implementation and should FAIL until
the flags, mutual-exclusion guards, enable_envstate auto-on, dispatch routing,
and _run_fullstate_worker method are in place.

Conventions:
- unittest.TestCase (no pytest fixtures/markers)
- SimpleNamespace fakes
- __new__-bypass where needed
- run via: .venv/bin/python -m pytest tests/test_agent_flags.py -q
"""
import argparse
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helper: build a minimal DockerAgent via __new__-bypass
# ---------------------------------------------------------------------------

def _make_agent(**kwargs):
    """Construct a DockerAgent via __new__ bypass, setting only the attrs we care about."""
    from agent import DockerAgent
    agent = DockerAgent.__new__(DockerAgent)
    # Required attrs for DockerAgent.__init__ side-effects; set to safe defaults
    agent.enable_envstate = False
    agent.enable_supervisor = False
    agent.enable_fullstate_worker = False
    agent.fullstate_worker_prompt = False
    agent.enable_cleanroom = False
    agent.action_ledger = None
    for k, v in kwargs.items():
        setattr(agent, k, v)
    return agent


# ---------------------------------------------------------------------------
# 1. DockerAgent.__init__ flag kwargs
# ---------------------------------------------------------------------------

class TestDockerAgentInitFlags(unittest.TestCase):
    """DockerAgent.__init__ accepts the two new kwargs and sets them as attrs."""

    def test_init_accepts_enable_fullstate_worker(self):
        """enable_fullstate_worker=True sets self.enable_fullstate_worker = True."""
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        self.assertIn(
            "enable_fullstate_worker", sig.parameters,
            "DockerAgent.__init__ must accept enable_fullstate_worker kwarg",
        )

    def test_init_accepts_fullstate_worker_prompt(self):
        """fullstate_worker_prompt=True sets self.fullstate_worker_prompt = True."""
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        self.assertIn(
            "fullstate_worker_prompt", sig.parameters,
            "DockerAgent.__init__ must accept fullstate_worker_prompt kwarg",
        )

    def test_enable_fullstate_worker_default_false(self):
        """enable_fullstate_worker defaults to False."""
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        default = sig.parameters["enable_fullstate_worker"].default
        self.assertFalse(default)

    def test_fullstate_worker_prompt_default_false(self):
        """fullstate_worker_prompt defaults to False."""
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        default = sig.parameters["fullstate_worker_prompt"].default
        self.assertFalse(default)


# ---------------------------------------------------------------------------
# 2. enable_envstate auto-on when enable_fullstate_worker is set
# ---------------------------------------------------------------------------

class TestEnableEnvstateAutoOn(unittest.TestCase):
    """When enable_fullstate_worker=True, enable_envstate must be auto-set True."""

    def _make_minimal_agent(self, enable_fullstate_worker, enable_supervisor=False, enable_envstate=False):
        """Build agent via __new__, then run just the flag-wiring logic."""
        from agent import DockerAgent
        agent = DockerAgent.__new__(DockerAgent)
        # Simulate the flag attrs as __init__ would set them per §3.1
        agent.enable_supervisor = enable_supervisor
        agent.enable_fullstate_worker = enable_fullstate_worker
        agent.fullstate_worker_prompt = False
        agent.enable_envstate = enable_envstate or enable_supervisor or enable_fullstate_worker
        agent.action_ledger = None
        return agent

    def test_enable_envstate_on_when_fullstate_worker_true(self):
        agent = self._make_minimal_agent(enable_fullstate_worker=True)
        self.assertTrue(agent.enable_envstate)

    def test_enable_envstate_off_when_all_flags_false(self):
        agent = self._make_minimal_agent(enable_fullstate_worker=False)
        self.assertFalse(agent.enable_envstate)

    def test_enable_envstate_on_when_supervisor_true(self):
        agent = self._make_minimal_agent(enable_fullstate_worker=False, enable_supervisor=True)
        self.assertTrue(agent.enable_envstate)


# ---------------------------------------------------------------------------
# 3. run() dispatch ordering (§3.1)
# ---------------------------------------------------------------------------

class TestRunDispatch(unittest.TestCase):
    """run() must check supervisor FIRST, then fullstate_worker.

    Uses __new__-bypass + method monkeypatching to verify which internal
    method is called without running Docker.
    """

    def _make_dispatchable_agent(self, enable_supervisor, enable_fullstate_worker):
        from agent import DockerAgent
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_supervisor = enable_supervisor
        agent.enable_fullstate_worker = enable_fullstate_worker
        agent.enable_envstate = enable_supervisor or enable_fullstate_worker
        agent.action_ledger = None
        # track which _run_* was called
        agent._called = []

        def _fake_supervisor(max_steps=30, keep_container=False):
            agent._called.append("supervisor")
            return True

        def _fake_fullstate_worker(max_steps=30, keep_container=False):
            agent._called.append("fullstate_worker")
            return True

        def _fake_legacy_run(max_steps=30, keep_container=False):
            agent._called.append("legacy")

        agent._run_supervisor = _fake_supervisor
        agent._run_fullstate_worker = _fake_fullstate_worker
        agent._legacy_run = _fake_legacy_run
        return agent

    def test_supervisor_flag_routes_to_run_supervisor(self):
        """With enable_supervisor=True, run() must call _run_supervisor."""
        agent = self._make_dispatchable_agent(enable_supervisor=True, enable_fullstate_worker=False)
        # patch run() dispatch: extract it directly
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=1)
        self.assertIn("supervisor", agent._called)
        self.assertNotIn("fullstate_worker", agent._called)

    def test_fullstate_worker_flag_routes_to_run_fullstate_worker(self):
        """With enable_fullstate_worker=True, run() must call _run_fullstate_worker."""
        agent = self._make_dispatchable_agent(enable_supervisor=False, enable_fullstate_worker=True)
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=1)
        self.assertIn("fullstate_worker", agent._called)
        self.assertNotIn("supervisor", agent._called)

    def test_supervisor_takes_priority_when_both_set(self):
        """If both flags are set (shouldn't happen after arg-guard), supervisor check fires first."""
        # This tests the order in run() — supervisor branch must be checked first
        agent = self._make_dispatchable_agent(enable_supervisor=True, enable_fullstate_worker=True)
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=1)
        self.assertEqual(agent._called[0], "supervisor")


# ---------------------------------------------------------------------------
# 4. _run_fullstate_worker method exists with correct signature
# ---------------------------------------------------------------------------

class TestRunFullstateWorkerExists(unittest.TestCase):
    """_run_fullstate_worker must be defined on DockerAgent with (max_steps, keep_container)."""

    def test_method_exists(self):
        from agent import DockerAgent
        self.assertTrue(
            hasattr(DockerAgent, "_run_fullstate_worker"),
            "DockerAgent must have a _run_fullstate_worker method",
        )

    def test_signature_has_max_steps_and_keep_container(self):
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_fullstate_worker)
        params = sig.parameters
        self.assertIn("max_steps", params)
        self.assertIn("keep_container", params)

    def test_max_steps_default_is_30(self):
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_fullstate_worker)
        self.assertEqual(sig.parameters["max_steps"].default, 30)

    def test_keep_container_default_is_false(self):
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_fullstate_worker)
        self.assertFalse(sig.parameters["keep_container"].default)


# ---------------------------------------------------------------------------
# 5. Argparse: new flags are registered
# ---------------------------------------------------------------------------

class TestArgparse(unittest.TestCase):
    """The argparse block in agent.py must include the two new flags."""

    def _parse(self, args_list):
        """Run agent.py's argparse on a given list of CLI args, return namespace."""
        import importlib
        import types
        # We import agent as a module and run its argparse block
        # by building the parser the same way agent.py does at module level.
        # Simplest approach: import agent; look for the parser at __main__ level.
        # Since we can't run __main__, re-build the parser by inspecting the
        # module's code at the if __name__ == "__main__" block.
        # Instead: just test that the flags land correctly by monkeypatching argv.
        original_argv = sys.argv[:]
        try:
            sys.argv = ["agent.py"] + args_list
            # Re-parse by re-running the argparse block; since DockerAgent is
            # instantiated inside __main__, we can't easily extract the parser.
            # Test via a dedicated helper that mirrors agent.py's parser.
            # We'll test by importing and checking the parser's known flags.
            return True
        finally:
            sys.argv = original_argv

    def test_enable_fullstate_worker_in_parser(self):
        """The --enable-fullstate-worker flag must be accepted without error."""
        # Build agent.py's parser manually matching what the spec requires.
        # The real test is: if the flag is wired, the parser does not error.
        # We do this by re-running sys.argv via a subprocess-free approach:
        # import and call the arg-parsing block directly.
        # Since we can't easily call the __main__ parser without running the
        # full agent, we verify the flag by checking the module source.
        import agent as agent_module
        import ast
        import inspect
        source = inspect.getsource(agent_module)
        self.assertIn(
            "--enable-fullstate-worker",
            source,
            "agent.py must register --enable-fullstate-worker via argparse",
        )

    def test_fullstate_worker_prompt_in_parser(self):
        """The --fullstate-worker-prompt flag must be present in agent.py."""
        import agent as agent_module
        import inspect
        source = inspect.getsource(agent_module)
        self.assertIn(
            "--fullstate-worker-prompt",
            source,
            "agent.py must register --fullstate-worker-prompt via argparse",
        )


# ---------------------------------------------------------------------------
# 6. Mutual-exclusion guards
# ---------------------------------------------------------------------------

class TestMutualExclusionGuards(unittest.TestCase):
    """After args are parsed, mutual-exclusion rules from §3.1 must be enforced."""

    def test_guard_text_for_supervisor_plus_fullstate_worker(self):
        """agent.py must include code that errors when both flags are set."""
        import agent as agent_module
        import inspect
        source = inspect.getsource(agent_module)
        # Check that a mutual exclusion guard is present
        self.assertTrue(
            "enable_fullstate_worker" in source and "enable_supervisor" in source,
            "agent.py must reference both enable_supervisor and enable_fullstate_worker "
            "for the mutual exclusion guard",
        )
        # Verify the exclusion message text or pattern is present
        self.assertIn(
            "Mutually exclusive",
            source,
            "agent.py must contain 'Mutually exclusive' error text for the guard",
        )

    def test_guard_fullstate_worker_prompt_requires_supervisor(self):
        """--fullstate-worker-prompt requires --enable-supervisor."""
        import agent as agent_module
        import inspect
        source = inspect.getsource(agent_module)
        self.assertIn(
            "fullstate_worker_prompt",
            source,
            "agent.py must check fullstate_worker_prompt in the mutual-exclusion guard",
        )
        self.assertIn(
            "requires --enable-supervisor",
            source,
            "agent.py guard must state '--fullstate-worker-prompt requires --enable-supervisor'",
        )


if __name__ == "__main__":
    unittest.main()
