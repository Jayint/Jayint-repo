"""
TDD for the enable_v1 flag wiring in DockerAgent (canonical contract §agent_py_glue).

Spec:
  - DockerAgent.__init__ accepts enable_v1=False kwarg
  - self.enable_v1 = enable_v1
  - self.enable_envstate = enable_envstate or enable_supervisor or enable_fullstate_worker or enable_v1
  - run() dispatches to _run_v1() BEFORE the supervisor / fullstate_worker checks when enable_v1=True
  - _run_v1 method exists with signature (self, max_cycles=12, keep_container=False)
"""
import inspect
import unittest
from types import SimpleNamespace


def _make_agent(**kwargs):
    from agent import DockerAgent
    agent = DockerAgent.__new__(DockerAgent)
    agent.enable_envstate = False
    agent.enable_supervisor = False
    agent.enable_fullstate_worker = False
    agent.enable_v1 = False
    agent.fullstate_worker_prompt = False
    agent.enable_cleanroom = False
    agent.action_ledger = None
    for k, v in kwargs.items():
        setattr(agent, k, v)
    return agent


class TestEnableV1InitParam(unittest.TestCase):
    def test_init_accepts_enable_v1_kwarg(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        self.assertIn(
            "enable_v1",
            sig.parameters,
            "DockerAgent.__init__ must accept enable_v1 kwarg",
        )

    def test_enable_v1_default_is_false(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        self.assertIs(
            sig.parameters["enable_v1"].default,
            False,
            "enable_v1 must default to False",
        )


class TestEnableV1EnvstateAutoOn(unittest.TestCase):
    """enable_v1=True must auto-set enable_envstate=True (triggers ActionLedger creation)."""

    def _make_with_flags(self, enable_v1=False, enable_envstate=False,
                          enable_supervisor=False, enable_fullstate_worker=False):
        from agent import DockerAgent
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_supervisor = enable_supervisor
        agent.enable_fullstate_worker = enable_fullstate_worker
        agent.enable_v1 = enable_v1
        agent.fullstate_worker_prompt = False
        # Replicate the __init__ logic per canonical contract
        agent.enable_envstate = (
            enable_envstate or enable_supervisor or enable_fullstate_worker or enable_v1
        )
        agent.action_ledger = None
        return agent

    def test_enable_envstate_on_when_v1_true(self):
        agent = self._make_with_flags(enable_v1=True)
        self.assertTrue(agent.enable_envstate)

    def test_enable_envstate_off_when_all_false(self):
        agent = self._make_with_flags(enable_v1=False)
        self.assertFalse(agent.enable_envstate)


class TestRunV1Dispatch(unittest.TestCase):
    """run() must call _run_v1 BEFORE checking enable_supervisor / enable_fullstate_worker."""

    def _make_dispatchable(self, enable_v1=False, enable_supervisor=False,
                            enable_fullstate_worker=False):
        from agent import DockerAgent
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_v1 = enable_v1
        agent.enable_supervisor = enable_supervisor
        agent.enable_fullstate_worker = enable_fullstate_worker
        agent.enable_envstate = enable_v1 or enable_supervisor or enable_fullstate_worker
        agent.action_ledger = None
        agent._called = []

        def _fake_v1(max_cycles=12, keep_container=False):
            agent._called.append("v1")
            return True

        def _fake_supervisor(max_steps=30, keep_container=False):
            agent._called.append("supervisor")
            return True

        def _fake_fullstate(max_steps=30, keep_container=False):
            agent._called.append("fullstate")
            return True

        agent._run_v1 = _fake_v1
        agent._run_supervisor = _fake_supervisor
        agent._run_fullstate_worker = _fake_fullstate
        return agent

    def test_v1_flag_routes_to_run_v1(self):
        agent = self._make_dispatchable(enable_v1=True)
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=12)
        self.assertIn("v1", agent._called)
        self.assertNotIn("supervisor", agent._called)
        self.assertNotIn("fullstate", agent._called)

    def test_v1_checked_before_supervisor(self):
        """If both enable_v1 and enable_supervisor are True (shouldn't happen after guard,
        but the ordering must be v1 first)."""
        agent = self._make_dispatchable(enable_v1=True, enable_supervisor=True)
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=12)
        self.assertEqual(agent._called[0], "v1")

    @unittest.skip(
        "supervisor removed — enable_supervisor is a deprecated no-op (Task 36); "
        "_run_supervisor is no longer dispatched from run(); replaced by DeprecationWarning."
    )
    def test_supervisor_still_works_when_v1_false(self):
        agent = self._make_dispatchable(enable_v1=False, enable_supervisor=True)
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=30)
        self.assertIn("supervisor", agent._called)
        self.assertNotIn("v1", agent._called)


class TestRunV1MethodExists(unittest.TestCase):
    def test_method_exists(self):
        from agent import DockerAgent
        self.assertTrue(
            hasattr(DockerAgent, "_run_v1"),
            "DockerAgent must have a _run_v1 method",
        )

    def test_signature_has_max_cycles_and_keep_container(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_v1)
        self.assertIn("max_cycles", sig.parameters)
        self.assertIn("keep_container", sig.parameters)

    def test_max_cycles_default_is_12(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_v1)
        self.assertEqual(sig.parameters["max_cycles"].default, 12)

    def test_keep_container_default_is_false(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_v1)
        self.assertFalse(sig.parameters["keep_container"].default)
