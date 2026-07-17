import importlib.util
import os
import subprocess
import sys
import time
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RAT_ROOT = REPO_ROOT / "runanything" / "src"
os.environ.setdefault("DOCKERAGENT_ROOT", str(REPO_ROOT))
if str(RAT_ROOT) not in sys.path:
    sys.path.insert(0, str(RAT_ROOT))

def _load_model_module_isolated():
    """Load the real model even when older tests pre-stub ``eval`` globally.

    Several legacy runner tests install collection-time ``MagicMock`` modules
    under ``eval.*``.  Importing through that shared namespace makes this test
    order-dependent, so execute the production file under a private module name
    with the model's three small framework seams supplied explicitly.
    """
    module_name = "_rat_dockeragent_model_revision_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    class TimeoutException(Exception):
        pass

    class BaseEvalModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def _check_timeout(self, start_time, step_name=""):
            if time.time() - start_time > self.timeout:
                raise TimeoutException(step_name)

    fake_weave = types.ModuleType("weave")
    fake_weave.op = lambda function: function
    fake_libkit = types.ModuleType("libkit")
    fake_libkit.__path__ = []
    fake_command = types.ModuleType("libkit.command")
    fake_command.init_output_and_repo = lambda *args, **kwargs: None
    fake_eval = types.ModuleType("eval")
    fake_eval.__path__ = []
    fake_common = types.ModuleType("eval.common")
    fake_common.__path__ = []
    fake_base = types.ModuleType("eval.common.base_model")
    fake_base.BaseEvalModel = BaseEvalModel
    fake_utils = types.ModuleType("eval.common.utils")
    fake_utils.TimeoutException = TimeoutException

    replacements = {
        "weave": fake_weave,
        "libkit": fake_libkit,
        "libkit.command": fake_command,
        "eval": fake_eval,
        "eval.common": fake_common,
        "eval.common.base_model": fake_base,
        "eval.common.utils": fake_utils,
    }
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in replacements}
    try:
        sys.modules.update(replacements)
        path = RAT_ROOT / "eval" / "models" / "dockeragent_model.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, prior in previous.items():
            if prior is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior


model_module = _load_model_module_isolated()
DockerAgentModelUnderTest = model_module.DockerAgentModel
_EXPECTED_SHA = "a" * 40
_ACTUAL_SHA = "b" * 40


def _install_fakes(
    monkeypatch,
    *,
    adapter_result,
    actual_sha=_EXPECTED_SHA,
    fail_build=False,
):
    calls = []

    class FakeAdapter:
        def __init__(self, **kwargs):
            pass

        def process_repo(self, *args, **kwargs):
            return adapter_result

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if isinstance(cmd, list) and cmd[:2] == ["docker", "build"] and fail_build:
            raise subprocess.CalledProcessError(1, cmd)
        if isinstance(cmd, list) and cmd[-4:] == ["-C", "/testbed", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=actual_sha + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(model_module, "RATV3Adapter", FakeAdapter)
    monkeypatch.setattr(model_module, "init_output_and_repo", lambda *args, **kwargs: None)
    monkeypatch.setattr(model_module.subprocess, "run", fake_run)
    return calls


def _success_adapter_result():
    return {
        "status": "success",
        "failure_reason": None,
        "base_image": "python:3.12-slim",
        "platform": "linux/amd64",
        "head_sha": _EXPECTED_SHA,
        "dockerfile": "FROM python:3.12-slim\nRUN true # pytest\n",
        "setup_scripts": {},
        "runtime_services": [],
    }


def _model(tmp_path):
    # test_run_rat_benchmark replaces the module attribute during collection;
    # keep the real class captured above so these tests remain order-independent.
    return DockerAgentModelUnderTest(
        root_path=str(tmp_path / "run"),
        timeout=300,
        llm="test-model",
        num_turn=1,
    )


def test_build_failure_keeps_adapter_source_head_sha(monkeypatch, tmp_path):
    _install_fakes(
        monkeypatch,
        adapter_result=_success_adapter_result(),
        fail_build=True,
    )

    result = _model(tmp_path).predict("owner/repo")

    assert result["status"] == "error"
    assert result["failure_reason"] == "build_failed"
    assert result["head_sha"] == _EXPECTED_SHA
    assert result["evaluated_head_sha"] == ""
    assert result["revision_match"] is None


def test_evaluator_revision_mismatch_fails_before_pytest(monkeypatch, tmp_path):
    calls = _install_fakes(
        monkeypatch,
        adapter_result=_success_adapter_result(),
        actual_sha=_ACTUAL_SHA,
    )

    result = _model(tmp_path).predict("owner/repo")

    assert result["status"] == "error"
    assert result["failure_reason"] == "revision_mismatch"
    assert result["head_sha"] == _EXPECTED_SHA
    assert result["evaluated_head_sha"] == _ACTUAL_SHA
    assert result["revision_match"] is False
    assert not any(isinstance(cmd, list) and "/run_pytest.py" in cmd for cmd in calls)


def test_matching_evaluator_revision_is_reported_independently(monkeypatch, tmp_path):
    _install_fakes(
        monkeypatch,
        adapter_result=_success_adapter_result(),
        actual_sha=_EXPECTED_SHA.upper(),
    )

    result = _model(tmp_path).predict("owner/repo")

    assert result["status"] == "success"
    assert result["head_sha"] == _EXPECTED_SHA
    assert result["evaluated_head_sha"] == _EXPECTED_SHA
    assert result["revision_match"] is True


def test_adapter_source_revision_failure_is_not_rewritten(monkeypatch, tmp_path):
    _install_fakes(
        monkeypatch,
        adapter_result={
            "status": "error",
            "failure_reason": "source_revision_missing",
            "head_sha": "",
            "logs": {"error": "missing source_revision.json"},
        },
    )

    result = _model(tmp_path).predict("owner/repo")

    assert result["status"] == "error"
    assert result["failure_reason"] == "source_revision_missing"
    assert "missing source_revision.json" in result["error"]
