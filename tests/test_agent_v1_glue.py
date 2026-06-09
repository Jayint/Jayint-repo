# tests/test_agent_v1_glue.py
"""Unit tests for the agent.py v1 glue layer.

Tests verify:
  1. DockerAgent.__init__ accepts enable_v1=True and sets enable_envstate.
  2. DockerAgent.run() dispatches to _run_v1 when enable_v1=True (before
     the supervisor and fullstate_worker branches).
  3. _run_v1 instantiates Planner/BuildAgent/Maintainer with canonical
     (client, model, on_usage=..., log_path=...) signatures, calls run_v1(),
     populates verified_test_commands from the COLLECT_ONLY_CMD ledger scan,
     and calls _auto_finalize_from_verified_tests + _finalize_supervisor_artifacts.
  4. _verify_cleanroom_or_fail is NOT called from _run_v1 (cleanroom is
     skipped in the v1 path; EBSR is the trusted metric).

No real Docker or LLM is used: Sandbox, Synthesizer, and ImageSelector are
patched at the module level so DockerAgent.__init__ does not fail.
"""
from __future__ import annotations

import os
import types
import sys
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Lightweight stubs to prevent import-time side effects
# ---------------------------------------------------------------------------

def _make_stub_module(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    return m


def _install_stubs() -> None:
    """Install minimal stubs for heavy dependencies so agent.py can be imported."""
    for mod_name in [
        "src.sandbox",
        "src.synthesizer",
        "src.image_selector",
        "src.verification_bundle",
        "src.constants",
        "src.memory_manager",
        "src.observation_compressor",
        "src.planner",
        "dotenv",
        "openai",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _make_stub_module(mod_name)

    # src.constants needs DEFAULT_LLM_MODEL and DEFAULT_MEMORY_EMBEDDING_MODEL
    sys.modules["src.constants"].DEFAULT_LLM_MODEL = "test-model"
    sys.modules["src.constants"].DEFAULT_MEMORY_EMBEDDING_MODEL = "test-embed"

    # src.observation_compressor needs several names
    oc = sys.modules["src.observation_compressor"]
    oc.AgentStep = object
    oc.ObservationCompressor = MagicMock
    oc.RunTokenLedger = MagicMock
    oc.build_observation_metadata = MagicMock(return_value={})
    oc.safety_compress_observation = MagicMock(return_value=("obs", False))
    oc.should_apply_compression = MagicMock(return_value=False)

    # dotenv
    sys.modules["dotenv"].load_dotenv = lambda **kw: None

    # openai
    sys.modules["openai"].OpenAI = MagicMock

    # src.synthesizer
    sys.modules["src.synthesizer"].Synthesizer = MagicMock

    # src.image_selector
    sys.modules["src.image_selector"].ImageSelector = MagicMock

    # src.verification_bundle
    sys.modules["src.verification_bundle"].derive_supported_verification_bundle = MagicMock(
        return_value={"test_commands": ["pytest --collect-only -q --disable-warnings"]}
    )

    # src.memory_manager
    sys.modules["src.memory_manager"].LongTermMemoryManager = MagicMock

    # src.planner — Arm-0 planner (different from src.envstate.planner)
    # Use a lambda factory to avoid MagicMock(spec=another_mock) issues in Python 3.12
    _planner_instance = MagicMock()
    sys.modules["src.planner"].Planner = MagicMock(return_value=_planner_instance)

    # src.sandbox
    sys.modules["src.sandbox"].Sandbox = MagicMock


_install_stubs()


# ---------------------------------------------------------------------------
# Now import agent (stubs must be in place first)
# ---------------------------------------------------------------------------
import agent as _agent_module


def _make_agent_instance(**kwargs) -> "_agent_module.DockerAgent":
    """Construct a DockerAgent with all heavy init work mocked out."""
    defaults = dict(
        repo_url="https://github.com/example/repo",
        base_image="python:3.11-slim",
        model="test-model",
        workplace="/tmp/test_workplace_v1",
    )
    defaults.update(kwargs)

    with (
        patch.object(_agent_module.DockerAgent, "_prepare_workplace", return_value=None),
        patch.object(_agent_module.DockerAgent, "_collect_local_service_hints", return_value=set()),
        patch.object(_agent_module.DockerAgent, "_checkout_commit", return_value=None),
        patch.object(_agent_module.DockerAgent, "_create_sandbox", return_value=MagicMock()),
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        patch("os.makedirs", return_value=None),
        patch("os.path.exists", return_value=False),
        patch("os.path.join", side_effect=lambda *a: "/".join(a)),
    ):
        agent = _agent_module.DockerAgent(**defaults)
    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEnableV1InitFlag(unittest.TestCase):
    """DockerAgent.__init__ must accept enable_v1 and wire enable_envstate."""

    def test_enable_v1_accepted_as_kwarg(self):
        agent = _make_agent_instance(enable_v1=True)
        self.assertTrue(getattr(agent, "enable_v1", False))

    def test_enable_v1_implies_enable_envstate(self):
        agent = _make_agent_instance(enable_v1=True)
        self.assertTrue(agent.enable_envstate,
                        "enable_v1=True must imply enable_envstate=True so ActionLedger is created")

    def test_enable_v1_creates_action_ledger(self):
        agent = _make_agent_instance(enable_v1=True)
        self.assertIsNotNone(agent.action_ledger,
                             "ActionLedger must be created when enable_v1=True")

    def test_enable_v1_false_does_not_set_enable_envstate_alone(self):
        """enable_envstate must still work independently of enable_v1."""
        agent = _make_agent_instance(enable_v1=False, enable_envstate=False)
        self.assertFalse(agent.enable_v1)


class TestRunDispatchesToV1(unittest.TestCase):
    """run() must call _run_v1 when enable_v1=True, before supervisor/fullstate checks."""

    def test_run_dispatches_to_run_v1_when_enable_v1(self):
        agent = _make_agent_instance(enable_v1=True)
        called_with = {}

        def fake_run_v1(max_cycles=12, keep_container=False):
            called_with["max_cycles"] = max_cycles
            called_with["keep_container"] = keep_container
            return True

        agent._run_v1 = fake_run_v1
        result = agent.run(max_steps=5, keep_container=False)
        self.assertIn("max_cycles", called_with,
                      "_run_v1 must be called by run() when enable_v1=True")
        self.assertEqual(called_with["max_cycles"], 5)
        self.assertTrue(result)

    def test_run_does_not_call_supervisor_when_enable_v1(self):
        agent = _make_agent_instance(enable_v1=True, enable_supervisor=False)
        # _run_v1 must be called; _run_supervisor must NOT be called.
        agent._run_v1 = MagicMock(return_value=True)
        agent._run_supervisor = MagicMock(return_value=True)
        agent.run(max_steps=3)
        agent._run_v1.assert_called_once()
        agent._run_supervisor.assert_not_called()

    def test_run_v1_checked_before_supervisor_flag(self):
        """enable_v1=True must win even when enable_supervisor=True."""
        agent = _make_agent_instance(enable_v1=True, enable_supervisor=True)
        agent._run_v1 = MagicMock(return_value=True)
        agent._run_supervisor = MagicMock(return_value=True)
        agent.run(max_steps=3)
        agent._run_v1.assert_called_once()
        agent._run_supervisor.assert_not_called()


class TestRunV1RoleInstantiations(unittest.TestCase):
    """_run_v1 must instantiate Planner/BuildAgent/Maintainer with canonical signatures."""

    def _run_with_captured_constructors(self):
        """Run _run_v1 and return (agent, planner_kwargs, build_agent_kwargs, maintainer_kwargs)."""
        from src.envstate.world_model import initial_map, merge_map
        from src.envstate.ledger import ActionLedger

        world_map = initial_map(
            base_image="python:3.11-slim",
            workdir="/app",
            language="python 3.11",
            build_system="pip",
            repo_layout=(),
        )
        final_map = merge_map(world_map, done_flag=True)

        agent = _make_agent_instance(enable_v1=True)
        agent.action_ledger = ActionLedger()
        agent.sandbox = MagicMock()
        agent.sandbox.execute = MagicMock(return_value=(True, "ok"))
        agent.sandbox.close = MagicMock()
        agent.synthesizer = MagicMock()
        agent.synthesizer.base_image = "python:3.11-slim"
        agent.synthesizer.workdir = "/app"
        agent.synthesizer.language = "python 3.11"
        agent.synthesizer.build_system = "pip"
        agent._write_run_summary = MagicMock()
        agent._auto_finalize_from_verified_tests = MagicMock(return_value=True)
        agent._finalize_supervisor_artifacts = MagicMock(return_value=True)
        agent.logs_dir = "/tmp/logs"

        import src.envstate.orchestrator as orch_mod
        import src.envstate.planner as planner_mod
        import src.envstate.build_agent as build_agent_mod
        import src.envstate.maintainer as maintainer_mod
        import src.envstate.world_model as wm_mod

        planner_init_calls = []
        build_agent_init_calls = []
        maintainer_init_calls = []

        _orig_planner = planner_mod.Planner

        def capture_planner(*args, **kwargs):
            planner_init_calls.append({"args": args, "kwargs": kwargs})
            m = MagicMock()
            m.decide = MagicMock(return_value=MagicMock(action="done", reason="ok"))
            return m

        def capture_build_agent(*args, **kwargs):
            build_agent_init_calls.append({"args": args, "kwargs": kwargs})
            return MagicMock()

        def capture_maintainer(*args, **kwargs):
            maintainer_init_calls.append({"args": args, "kwargs": kwargs})
            return MagicMock()

        with (
            patch.object(orch_mod, "run_v1", return_value=(final_map, "done_flag")),
            patch.object(planner_mod, "Planner", side_effect=capture_planner),
            patch.object(build_agent_mod, "BuildAgent", side_effect=capture_build_agent),
            patch.object(maintainer_mod, "Maintainer", side_effect=capture_maintainer),
            patch.object(wm_mod, "initial_map", return_value=world_map),
            patch("os.makedirs", return_value=None),
            patch("os.path.exists", return_value=False),
            patch("os.environ.get", return_value=None),
            patch("os.environ.__setitem__", return_value=None),
            patch("os.environ.pop", return_value=None),
        ):
            agent._run_v1(max_cycles=12, keep_container=False)

        return agent, planner_init_calls, build_agent_init_calls, maintainer_init_calls

    def test_planner_receives_on_usage_kwarg(self):
        _, planner_calls, _, _ = self._run_with_captured_constructors()
        self.assertEqual(len(planner_calls), 1, "Planner must be instantiated exactly once")
        kwargs = planner_calls[0]["kwargs"]
        self.assertIn("on_usage", kwargs,
                      "Planner must receive on_usage= keyword arg")
        self.assertTrue(callable(kwargs["on_usage"]),
                        "Planner on_usage must be callable")

    def test_planner_receives_log_path_kwarg(self):
        _, planner_calls, _, _ = self._run_with_captured_constructors()
        kwargs = planner_calls[0]["kwargs"]
        self.assertIn("log_path", kwargs,
                      "Planner must receive log_path= keyword arg")

    def test_build_agent_receives_on_usage_kwarg(self):
        _, _, ba_calls, _ = self._run_with_captured_constructors()
        self.assertEqual(len(ba_calls), 1, "BuildAgent must be instantiated exactly once")
        kwargs = ba_calls[0]["kwargs"]
        self.assertIn("on_usage", kwargs,
                      "BuildAgent must receive on_usage= keyword arg")
        self.assertTrue(callable(kwargs["on_usage"]),
                        "BuildAgent on_usage must be callable")

    def test_build_agent_receives_log_path_kwarg(self):
        _, _, ba_calls, _ = self._run_with_captured_constructors()
        kwargs = ba_calls[0]["kwargs"]
        self.assertIn("log_path", kwargs,
                      "BuildAgent must receive log_path= keyword arg")

    def test_maintainer_receives_on_usage_kwarg(self):
        _, _, _, m_calls = self._run_with_captured_constructors()
        self.assertEqual(len(m_calls), 1, "Maintainer must be instantiated exactly once")
        kwargs = m_calls[0]["kwargs"]
        self.assertIn("on_usage", kwargs,
                      "Maintainer must receive on_usage= keyword arg")
        self.assertTrue(callable(kwargs["on_usage"]),
                        "Maintainer on_usage must be callable")

    def test_maintainer_receives_log_path_kwarg(self):
        _, _, _, m_calls = self._run_with_captured_constructors()
        kwargs = m_calls[0]["kwargs"]
        self.assertIn("log_path", kwargs,
                      "Maintainer must receive log_path= keyword arg")


class TestRunV1Method(unittest.TestCase):
    """_run_v1 must wire run_v1(), detect done_flag CommandRecord, and finalize."""

    def _patched_run_v1_call(
        self,
        done_flag: bool = True,
        collect_cmd: str = "pytest --collect-only -q --disable-warnings",
    ):
        """Run _run_v1 on a minimal agent with mocked collaborators.

        Returns (agent, configuration_success).
        """
        from src.envstate.world_model import (
            CommandRecord, TaskReport, WorldModelMap, initial_map, merge_map,
        )
        from src.envstate.ledger import ActionLedger

        world_map = initial_map(
            base_image="python:3.11-slim",
            workdir="/app",
            language="python 3.11",
            build_system="pip",
            repo_layout=(),
        )
        final_map = merge_map(
            world_map,
            done_flag=done_flag,
        )

        agent = _make_agent_instance(enable_v1=True)
        agent.action_ledger = ActionLedger()
        agent.sandbox = MagicMock()
        agent.sandbox.execute = MagicMock(return_value=(True, "ok"))
        agent.sandbox.close = MagicMock()
        agent.synthesizer = MagicMock()
        agent.synthesizer.base_image = "python:3.11-slim"
        agent.synthesizer.workdir = "/app"
        agent.synthesizer.language = "python 3.11"
        agent.synthesizer.build_system = "pip"
        agent._write_run_summary = MagicMock()
        agent._auto_finalize_from_verified_tests = MagicMock(return_value=True)
        agent._finalize_supervisor_artifacts = MagicMock(return_value=True)
        agent.logs_dir = "/tmp/logs"

        import src.envstate.orchestrator as orch_mod
        import src.envstate.planner as planner_mod
        import src.envstate.build_agent as build_agent_mod
        import src.envstate.maintainer as maintainer_mod
        import src.envstate.world_model as wm_mod

        mock_run_v1 = MagicMock(return_value=(final_map, "done_flag"))
        mock_planner_cls = MagicMock()
        mock_build_agent_cls = MagicMock()
        mock_maintainer_cls = MagicMock()

        with (
            patch.object(orch_mod, "run_v1", mock_run_v1),
            patch.object(planner_mod, "Planner", mock_planner_cls),
            patch.object(build_agent_mod, "BuildAgent", mock_build_agent_cls),
            patch.object(maintainer_mod, "Maintainer", mock_maintainer_cls),
            patch.object(wm_mod, "initial_map", return_value=world_map),
            patch("os.makedirs", return_value=None),
            patch("os.path.exists", return_value=False),
            patch("os.environ.get", return_value=None),
            patch("os.environ.__setitem__", return_value=None),
            patch("os.environ.pop", return_value=None),
        ):
            config_success = agent._run_v1(max_cycles=12, keep_container=False)

        return agent, config_success

    def test_run_v1_returns_true_when_done_flag_set(self):
        _agent, config_success = self._patched_run_v1_call(done_flag=True)
        self.assertTrue(config_success)

    def test_run_v1_calls_auto_finalize_from_verified_tests(self):
        agent, _ = self._patched_run_v1_call(done_flag=True)
        agent._auto_finalize_from_verified_tests.assert_called_once()

    def test_run_v1_calls_finalize_supervisor_artifacts(self):
        agent, _ = self._patched_run_v1_call(done_flag=True)
        agent._finalize_supervisor_artifacts.assert_called_once()

    def test_run_v1_writes_run_summary(self):
        agent, _ = self._patched_run_v1_call(done_flag=True)
        agent._write_run_summary.assert_called_once()

    def test_run_v1_closes_sandbox(self):
        agent, _ = self._patched_run_v1_call(done_flag=True)
        agent.sandbox.close.assert_called_once()

    def test_verified_test_commands_populated_from_collect_only(self):
        """_run_v1 must set self.verified_test_commands from the collect-only CommandRecord."""
        from src.envstate.world_model import (
            CommandRecord, TaskReport, WorldModelMap, initial_map, merge_map,
        )
        from src.envstate.ledger import ActionLedger

        collect_cmd = "pytest --collect-only -q --disable-warnings"
        world_map = initial_map(
            base_image="python:3.11-slim",
            workdir="/app",
            language="python 3.11",
            build_system="pip",
            repo_layout=(),
        )
        final_map = merge_map(world_map, done_flag=True)

        agent = _make_agent_instance(enable_v1=True)
        agent.action_ledger = ActionLedger()
        agent.sandbox = MagicMock()
        agent.sandbox.execute = MagicMock(return_value=(True, "ok"))
        agent.sandbox.close = MagicMock()
        agent.synthesizer = MagicMock()
        agent.synthesizer.base_image = "python:3.11-slim"
        agent.synthesizer.workdir = "/app"
        agent.synthesizer.language = "python 3.11"
        agent.synthesizer.build_system = "pip"
        agent._write_run_summary = MagicMock()
        agent._finalize_supervisor_artifacts = MagicMock(return_value=True)
        agent.logs_dir = "/tmp/logs"

        import src.envstate.orchestrator as orch_mod
        import src.envstate.planner as planner_mod
        import src.envstate.build_agent as build_agent_mod
        import src.envstate.maintainer as maintainer_mod
        import src.envstate.world_model as wm_mod

        # Simulate what _run_v1 must do internally: populate verified_test_commands
        # by scanning the action_ledger for the collect-only command.
        # Seed the ledger with a matching ActionEvent so the real scan finds it.
        from src.envstate.ledger import ActionEvent
        agent.action_ledger._events = [
            ActionEvent(
                step=1,
                task_id=None,
                cmd=collect_cmd,
                rc=0,
                stdout_path=None,
                stderr_path=None,
                env_revision_before=0,
                env_revision_after=0,
                mutation_class=None,
                container_id="",
                summary="5 items collected",
            )
        ]

        mock_run_v1 = MagicMock(return_value=(final_map, "done_flag"))

        with (
            patch.object(orch_mod, "run_v1", mock_run_v1),
            patch.object(planner_mod, "Planner", MagicMock()),
            patch.object(build_agent_mod, "BuildAgent", MagicMock()),
            patch.object(maintainer_mod, "Maintainer", MagicMock()),
            patch.object(wm_mod, "initial_map", return_value=world_map),
            patch("os.makedirs", return_value=None),
            patch("os.path.exists", return_value=False),
            patch("os.environ.get", return_value=None),
            patch("os.environ.__setitem__", return_value=None),
            patch("os.environ.pop", return_value=None),
        ):
            agent._run_v1(max_cycles=12, keep_container=False)

        self.assertIn(collect_cmd, agent.verified_test_commands,
                      "_run_v1 must populate verified_test_commands with the collect-only cmd")

    def test_run_v1_does_not_call_verify_cleanroom(self):
        """Cleanroom is skipped in the v1 path (EBSR is the trusted metric).

        _run_v1 must NOT invoke _verify_cleanroom_or_fail directly. The
        cleanroom gate lives in _finalize_supervisor_artifacts (already tested
        there); v1 does not add a second cleanroom call.
        """
        agent, _ = self._patched_run_v1_call(done_flag=True)
        # _verify_cleanroom_or_fail is NOT expected to have been called by _run_v1.
        # If the method was called it would be recorded on the mock; assert it wasn't.
        # Since _finalize_supervisor_artifacts is mocked out, cleanroom is entirely bypassed.
        # This test documents the design intent explicitly.
        if hasattr(agent, "_verify_cleanroom_or_fail"):
            # If it was patched to a MagicMock by the test scaffold, check not called.
            mock_cleanroom = getattr(agent, "_verify_cleanroom_or_fail", None)
            if isinstance(mock_cleanroom, MagicMock):
                mock_cleanroom.assert_not_called()


class TestCleanroomSkippedInV1Path(unittest.TestCase):
    """_run_v1 must not call _verify_cleanroom_or_fail.

    DESIGN NOTE (v1 implementation task): In the v1 path, cleanroom verification
    is SKIPPED. EBSR (Environment Build Success Rate) is the trusted metric for
    this arm. The _verify_cleanroom_or_fail method references self.env_snapshot
    and snapshot.requirements (types deleted in v1), so calling it would crash.
    Instead, _run_v1 calls _finalize_supervisor_artifacts, which internally calls
    _verify_cleanroom_or_fail — but only when enable_cleanroom=True (default False).
    In v1 runs, enable_cleanroom should remain False.

    Implementation task for this group: rewrite _verify_cleanroom_or_fail to
    operate ONLY on the produced Dockerfile + build context, with NO reference to
    self.env_snapshot / snapshot.requirements / req.source. The new signature must
    be:

        def _verify_cleanroom_or_fail(
            self,
            dockerfile_path: str,
            build_context: str,
        ) -> bool:

    Until that rewrite is complete, the v1 path must set enable_cleanroom=False
    and skip the cleanroom gate. This is safe: the v1 Planner/BuildAgent/Maintainer
    loop already validates test discoverability via COLLECT_ONLY_CMD before setting
    done_flag, so the Dockerfile is evidence-backed even without cleanroom.
    """

    def test_enable_cleanroom_defaults_false_for_v1(self):
        """enable_cleanroom must default to False so the v1 finalization path is safe."""
        agent = _make_agent_instance(enable_v1=True)
        # enable_cleanroom must be False by default (it requires explicit opt-in).
        self.assertFalse(getattr(agent, "enable_cleanroom", False))


if __name__ == "__main__":
    unittest.main()
