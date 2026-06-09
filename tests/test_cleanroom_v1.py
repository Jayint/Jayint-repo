# tests/test_cleanroom_v1.py
"""Tests for the decoupled _verify_cleanroom_or_fail signature required by v1.

Current signature (broken for v1):
    _verify_cleanroom_or_fail(self) -> bool
    — reads self.env_snapshot, snapshot.requirements, req.source (deleted types)

Target signature (v1-compatible):
    _verify_cleanroom_or_fail(self, dockerfile_path: str, build_context: str) -> bool
    — operates ONLY on the produced Dockerfile + build context
    — does NOT reference self.env_snapshot / snapshot.requirements / req.source

These tests drive the rewrite. Until the rewrite is complete, they fail
(either AttributeError or TypeError depending on which signature is present).
"""
from __future__ import annotations

import os
import types
import sys
import unittest
from unittest.mock import MagicMock, patch


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
    # Use a plain callable that always returns a MagicMock so that
    # Planner(self.client, ...) does not treat self.client as a mock spec.
    sys.modules["src.planner"].Planner = lambda *a, **kw: MagicMock()
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

import inspect


def _make_agent_instance(**kwargs):
    defaults = dict(
        repo_url="https://github.com/example/repo",
        base_image="python:3.11-slim",
        model="test-model",
        workplace="/tmp/test_workplace_cleanroom",
    )
    defaults.update(kwargs)
    with (
        patch.object(_agent_module.DockerAgent, "_prepare_workplace", return_value=None),
        patch.object(_agent_module.DockerAgent, "_collect_local_service_hints", return_value=set()),
        patch.object(_agent_module.DockerAgent, "_checkout_commit", return_value=None),
        patch.object(_agent_module.DockerAgent, "_create_sandbox", return_value=MagicMock()),
        patch.object(_agent_module.DockerAgent, "_detect_python_image", return_value=None),
        patch.object(_agent_module.DockerAgent, "_collect_maven_repository_hints", return_value=[]),
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        patch("os.makedirs", return_value=None),
        patch("os.path.exists", return_value=False),
        patch("os.path.join", side_effect=lambda *a: "/".join(a)),
    ):
        return _agent_module.DockerAgent(**defaults)


class TestVerifyCleanroomDecoupledSignature(unittest.TestCase):
    """_verify_cleanroom_or_fail must accept (dockerfile_path, build_context) args."""

    def test_method_accepts_dockerfile_path_and_build_context(self):
        """The method signature must include dockerfile_path and build_context params."""
        agent = _make_agent_instance()
        sig = inspect.signature(agent._verify_cleanroom_or_fail)
        params = list(sig.parameters.keys())
        self.assertIn("dockerfile_path", params,
                      "_verify_cleanroom_or_fail must accept dockerfile_path param")
        self.assertIn("build_context", params,
                      "_verify_cleanroom_or_fail must accept build_context param")

    def test_returns_true_when_cleanroom_disabled(self):
        """When enable_cleanroom=False, must return True without touching env_snapshot."""
        agent = _make_agent_instance(enable_cleanroom=False)
        # Must not raise AttributeError about missing env_snapshot.
        result = agent._verify_cleanroom_or_fail(
            dockerfile_path="/tmp/Dockerfile",
            build_context="/tmp/workplace",
        )
        self.assertTrue(result)

    def test_does_not_reference_env_snapshot(self):
        """With enable_cleanroom=False, env_snapshot must NOT be accessed.

        This test verifies the v1 safety guarantee: calling the method on an agent
        that has no env_snapshot attribute must not raise AttributeError.
        """
        agent = _make_agent_instance(enable_cleanroom=False)
        # Explicitly delete env_snapshot to prove it is not touched.
        if hasattr(agent, "env_snapshot"):
            del agent.env_snapshot
        # Must complete without AttributeError.
        result = agent._verify_cleanroom_or_fail(
            dockerfile_path="/tmp/Dockerfile",
            build_context="/tmp/workplace",
        )
        self.assertTrue(result)

    def test_cleanroom_enabled_calls_verify_cleanroom_with_dockerfile(self):
        """When enable_cleanroom=True, must call verify_cleanroom using dockerfile_path."""
        agent = _make_agent_instance(enable_cleanroom=True)
        agent.sandbox = MagicMock()
        agent.sandbox.client = MagicMock()
        agent.verified_test_commands = ["pytest --collect-only -q --disable-warnings"]
        agent.run_summary_cleanroom = {}
        agent.synthesizer = MagicMock()
        agent.synthesizer.workdir = "/app"

        mock_verify_result = MagicMock()
        mock_verify_result.passed = True
        mock_verify_result.reason = "ok"

        from src.envstate import cleanroom as _cleanroom_mod

        with (
            patch.object(_cleanroom_mod, "verify_cleanroom", return_value=mock_verify_result) as mock_vc,
            patch.object(_cleanroom_mod, "ensure_repo_in_dockerfile", side_effect=lambda txt, wd: txt),
            patch("builtins.open", unittest.mock.mock_open(read_data="FROM python:3.11-slim\n")),
        ):
            result = agent._verify_cleanroom_or_fail(
                dockerfile_path="/tmp/test_workplace_cleanroom/Dockerfile",
                build_context="/tmp/test_workplace_cleanroom",
            )

        self.assertTrue(result)
        mock_vc.assert_called_once()
        # verify_cleanroom must receive build_context_dir from the build_context argument.
        call_kwargs = mock_vc.call_args
        passed_build_context = (
            call_kwargs.kwargs.get("build_context_dir")
            or (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
        )
        self.assertEqual(passed_build_context, "/tmp/test_workplace_cleanroom")


if __name__ == "__main__":
    unittest.main()
