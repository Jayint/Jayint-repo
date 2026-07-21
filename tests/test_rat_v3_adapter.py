import subprocess
import sys
import json

import pytest

from rat_v3_adapter import (
    RATV3Adapter, _normalize_head_sha, _runtime_commands_from_handoff,
    _runtime_environment_from_handoff, _runtime_services_from_handoff,
)


_REDIS_CHECK = (
    "python3 -c \"import socket; "
    "s=socket.create_connection(('127.0.0.1',6379),2); s.close()\""
)
_HEAD_SHA = "a" * 40


def test_adapter_threads_execution_mode_and_turn_budget_to_v3_cli(monkeypatch, tmp_path):
    root = tmp_path / "run"
    output = root / "output" / "owner" / "repo"
    repo = root / "input" / "repo" / "owner" / "repo"
    repo.mkdir(parents=True)
    adapter = RATV3Adapter(
        root_path=str(root), output_dir=str(output), agent_root=str(tmp_path)
    )
    monkeypatch.setattr(adapter, "_ensure_repo", lambda full_name: repo)
    monkeypatch.setattr(adapter, "_repo_head_sha", lambda repo_path: _HEAD_SHA)
    seen = {}

    def fake_run(cmd, log_path, timeout):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        output.mkdir(parents=True, exist_ok=True)
        (output / "setup.sh").write_text("#!/bin/bash\ntrue\n", encoding="utf-8")
        (output / "token_usage.json").write_text(json.dumps({
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "api_calls": 3,
        }))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=(
                "[v3] base-image: python:3.11-slim (py 3.11)\n"
                "[Platform] Using platform: linux/arm64\n"
                "[Platform] Stable image: jayint-v3-base:abc-linux-arm64\n"
            )
        )

    monkeypatch.setattr(adapter, "_run_v3", fake_run)
    result = adapter.process_repo(
        "owner/repo",
        model="test-model",
        timeout=123,
        max_cycles=17,
        execution_mode="incremental",
    )

    assert result["status"] == "success"
    assert result["dockerfile"].startswith(
        "FROM --platform=linux/arm64 jayint-v3-base:abc-linux-arm64"
    )
    assert result["base_image_ref"] == "jayint-v3-base:abc-linux-arm64"
    assert result["platform"] == "linux/arm64"
    assert result["head_sha"] == _HEAD_SHA
    source_revision = json.loads((output / "source_revision.json").read_text())
    assert source_revision == {
        "version": 1,
        "full_name": "owner/repo",
        "head_sha": _HEAD_SHA,
    }
    assert f"ARG SOURCE_SHA={_HEAD_SHA}" in result["dockerfile"]
    assert 'fetch --no-tags --depth=1 origin "$SOURCE_SHA"' in result["dockerfile"]
    assert 'checkout --detach "$SOURCE_SHA"' in result["dockerfile"]
    assert 'test "$(git -C /testbed rev-parse HEAD)" = "$SOURCE_SHA"' in result["dockerfile"]
    assert "docker/dockerfile" not in result["dockerfile"]
    assert (
        "apk add --no-cache git ca-certificates bash coreutils python3 py3-pip"
        in result["dockerfile"]
    )
    assert seen["timeout"] == 123
    command = seen["cmd"]
    assert command[command.index("--max-cycles") + 1] == "17"
    assert command[command.index("--execution-mode") + 1] == "incremental"
    assert command[command.index("--language-hint") + 1] == "python"
    assert command[command.index("--usage-out") + 1].endswith("token_usage.json")
    assert result["token_usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "api_calls": 3,
    }
    assert result["runtime_commands"] == []


def test_adapter_uses_detected_node_language_and_native_dockerfile(monkeypatch, tmp_path):
    root = tmp_path / "run"
    output = root / "output" / "owner" / "repo"
    repo = root / "input" / "repo" / "owner" / "repo"
    repo.mkdir(parents=True)
    (repo / "package.json").write_text(
        '{"scripts":{"test":"ava"},"engines":{"node":">=20"}}',
        encoding="utf-8",
    )
    (repo / "package-lock.json").write_text(
        '{"lockfileVersion":3,"packages":{}}',
        encoding="utf-8",
    )
    (repo / "index.js").write_text("export default 1\n", encoding="utf-8")
    adapter = RATV3Adapter(
        root_path=str(root), output_dir=str(output), agent_root=str(tmp_path)
    )
    monkeypatch.setattr(adapter, "_ensure_repo", lambda _name: repo)
    monkeypatch.setattr(adapter, "_repo_head_sha", lambda _path: _HEAD_SHA)
    seen = {}

    def fake_run(cmd, _log_path, _timeout):
        seen["cmd"] = cmd
        output.mkdir(parents=True, exist_ok=True)
        (output / "setup.sh").write_text("#!/bin/bash\nnpm install\n")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "[v3] base-image: node:20-bookworm (py 3.11)\n"
                "[Platform] Using platform: linux/arm64\n"
            ),
        )

    monkeypatch.setattr(adapter, "_run_v3", fake_run)
    result = adapter.process_repo("owner/repo", model="test")

    command = seen["cmd"]
    assert command[command.index("--language-hint") + 1] == "javascript"
    assert result["language"] == "nodejs"
    assert result["build_system"] == "npm"
    assert "python3 -m pip install" not in result["dockerfile"]
    assert "git ca-certificates bash python3" in result["dockerfile"]


def test_run_v3_streams_to_log_and_returns_captured_text(tmp_path):
    adapter = RATV3Adapter(
        root_path=str(tmp_path / "run"),
        output_dir=str(tmp_path / "out"),
        agent_root=str(tmp_path),
    )
    log_path = tmp_path / "out" / "v3_run.log"

    completed = adapter._run_v3(
        [sys.executable, "-c", "print('streamed-v3-output')"],
        log_path,
        timeout=30,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "streamed-v3-output"
    assert log_path.read_text(encoding="utf-8").strip() == "streamed-v3-output"


def test_run_v3_sets_default_llm_audit_log(monkeypatch, tmp_path):
    monkeypatch.delenv("ENVSTATE_LLM_LOG", raising=False)
    adapter = RATV3Adapter(
        root_path=str(tmp_path / "run"), output_dir=str(tmp_path / "out"),
        agent_root=str(tmp_path),
    )
    log_path = tmp_path / "out" / "v3_run.log"
    completed = adapter._run_v3(
        [sys.executable, "-c", "import os; print(os.environ['ENVSTATE_LLM_LOG'])"],
        log_path, timeout=30,
    )
    assert completed.stdout.strip() == str(tmp_path / "out" / "v3_llm.jsonl")


def test_explicit_resume_reuses_setup_without_cloning_or_calling_llm(monkeypatch, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "setup.sh").write_text("#!/bin/bash\ntrue\n", encoding="utf-8")
    (output / "v3_run.log").write_text(
        "[v3] base-image: python:3.12-slim (py 3.12)\n", encoding="utf-8"
    )
    adapter = RATV3Adapter(
        root_path=str(tmp_path / "run"), output_dir=str(output), agent_root=str(tmp_path)
    )
    adapter._write_source_revision(output / "source_revision.json", "owner/repo", _HEAD_SHA)
    monkeypatch.setattr(
        adapter, "_ensure_repo",
        lambda full_name: (_ for _ in ()).throw(AssertionError("must not clone")),
    )
    monkeypatch.setattr(
        adapter, "_run_v3",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not call v3")),
    )

    result = adapter.process_repo(
        "owner/repo", model="test", reuse_existing=True
    )

    assert result["status"] == "success"
    assert result["base_image"] == "python:3.12-slim"
    assert result["head_sha"] == _HEAD_SHA
    assert f"ARG SOURCE_SHA={_HEAD_SHA}" in result["dockerfile"]
    assert result["logs"]["reused_existing"] is True


def test_explicit_resume_preserves_partial_setup_status(monkeypatch, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "setup.sh").write_text("#!/bin/bash\nfalse\n", encoding="utf-8")
    (output / "v3_run.log").write_text(
        "[v3] base-image: python:3.12-slim (py 3.12)\n", encoding="utf-8"
    )
    (output / "setup_status.json").write_text(json.dumps({
        "version": 1,
        "certified_setup": False,
        "failure_reason": "v3_failed",
        "v3_returncode": 1,
    }))
    adapter = RATV3Adapter(
        root_path=str(tmp_path / "run"), output_dir=str(output), agent_root=str(tmp_path)
    )
    adapter._write_source_revision(output / "source_revision.json", "owner/repo", _HEAD_SHA)
    monkeypatch.setattr(
        adapter, "_run_v3",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not call v3")),
    )

    result = adapter.process_repo("owner/repo", model="test", reuse_existing=True)

    assert result["status"] == "partial"
    assert result["certified_setup"] is False
    assert result["failure_reason"] == "v3_failed"
    assert "/tmp/v3_setup_exit_code" in result["dockerfile"]


def test_explicit_resume_without_source_revision_fails_closed(monkeypatch, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "setup.sh").write_text("#!/bin/bash\ntrue\n", encoding="utf-8")
    adapter = RATV3Adapter(
        root_path=str(tmp_path / "run"), output_dir=str(output), agent_root=str(tmp_path)
    )
    monkeypatch.setattr(
        adapter, "_ensure_repo",
        lambda full_name: (_ for _ in ()).throw(AssertionError("must not clone")),
    )
    monkeypatch.setattr(
        adapter, "_run_v3",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not call v3")),
    )

    result = adapter.process_repo("owner/repo", model="test", reuse_existing=True)

    assert result["status"] == "error"
    assert result["failure_reason"] == "source_revision_missing"
    assert "dockerfile" not in result


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps({"version": 2, "full_name": "owner/repo", "head_sha": _HEAD_SHA}),
        json.dumps({"version": 1, "full_name": "other/repo", "head_sha": _HEAD_SHA}),
        json.dumps({"version": 1, "full_name": "owner/repo", "head_sha": "abc"}),
        json.dumps({
            "version": 1,
            "full_name": "owner/repo",
            "head_sha": _HEAD_SHA + "; touch /tmp/unsafe",
        }),
    ],
)
def test_explicit_resume_rejects_invalid_source_revision(payload, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "setup.sh").write_text("#!/bin/bash\ntrue\n", encoding="utf-8")
    (output / "source_revision.json").write_text(payload, encoding="utf-8")
    adapter = RATV3Adapter(
        root_path=str(tmp_path / "run"), output_dir=str(output), agent_root=str(tmp_path)
    )

    result = adapter.process_repo("owner/repo", model="test", reuse_existing=True)

    assert result["status"] == "error"
    assert result["failure_reason"] == "source_revision_invalid"
    assert "dockerfile" not in result


def test_certified_runtime_handoff_yields_in_image_restart_command(tmp_path):
    path = tmp_path / "runtime_handoff.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "services": [{
                "kind": "redis",
                "start": "redis-server --daemonize yes",
                "check": _REDIS_CHECK,
            }],
        }),
        encoding="utf-8",
    )
    commands = _runtime_commands_from_handoff(path)
    assert len(commands) == 1
    assert "redis-server --daemonize yes" in commands[0]
    assert "docker" not in commands[0]
    assert _runtime_services_from_handoff(path) == [{
        "kind": "redis",
        "start": "redis-server --daemonize yes",
        "check": _REDIS_CHECK,
    }]
    assert _runtime_commands_from_handoff(tmp_path / "missing.json") == []


def test_runtime_handoff_rejects_unstructured_commands(tmp_path):
    path = tmp_path / "runtime_handoff.json"
    path.write_text('{"version":1,"runtime_commands":"redis-server"}')
    assert _runtime_commands_from_handoff(path) == []


def test_runtime_handoff_rejects_unknown_or_modified_service_commands(tmp_path):
    path = tmp_path / "runtime_handoff.json"
    path.write_text(json.dumps({
        "version": 1,
        "services": [
            {"kind": "mongo", "start": "mongod --fork", "check": "true"},
            {"kind": "redis", "start": "redis-server --daemonize no", "check": _REDIS_CHECK},
            {"kind": "redis", "start": "redis-server --daemonize yes", "check": "true"},
        ],
    }))
    assert _runtime_services_from_handoff(path) == []


def test_runtime_handoff_allows_only_narrow_pytest_environment(tmp_path):
    path = tmp_path / "runtime_handoff.json"
    path.write_text(json.dumps({
        "version": 1,
        "environment": {
            "PYTEST_ADDOPTS": "--import-mode=importlib",
            "UNSAFE": "value",
        },
    }))
    assert _runtime_environment_from_handoff(path) == {
        "PYTEST_ADDOPTS": "--import-mode=importlib"
    }

    path.write_text(json.dumps({
        "version": 1,
        "environment": {"PYTEST_ADDOPTS": "-k easy"},
    }))
    assert _runtime_environment_from_handoff(path) == {}


def test_dockerfile_persists_allowlisted_pytest_policy(tmp_path):
    adapter = RATV3Adapter(
        root_path=str(tmp_path / "run"), output_dir=str(tmp_path / "out")
    )
    dockerfile = adapter._render_dockerfile(
        full_name="owner/repo",
        base_image="python:3.12-slim",
        setup_script_name="setup.sh",
        head_sha=_HEAD_SHA,
        runtime_environment={"PYTEST_ADDOPTS": "--import-mode=importlib"},
    )
    assert (
        "PIP_DISABLE_PIP_VERSION_CHECK=1 \\\n"
        "    PYTEST_ADDOPTS=--import-mode=importlib"
    ) in dockerfile
    assert "\n+" not in dockerfile


def test_fresh_run_removes_stale_setup_and_handoff_before_v3(monkeypatch, tmp_path):
    root = tmp_path / "run"
    output = root / "output" / "owner" / "repo"
    repo = root / "input" / "repo" / "owner" / "repo"
    output.mkdir(parents=True)
    repo.mkdir(parents=True)
    for name in ("setup.sh", "runtime_handoff.json", "v3_trace.json", "source_revision.json"):
        (output / name).write_text("stale", encoding="utf-8")
    adapter = RATV3Adapter(root_path=str(root), output_dir=str(output), agent_root=str(tmp_path))
    monkeypatch.setattr(adapter, "_ensure_repo", lambda _name: repo)
    monkeypatch.setattr(adapter, "_repo_head_sha", lambda repo_path: _HEAD_SHA)

    def fake_run(cmd, log_path, timeout):
        assert not (output / "setup.sh").exists()
        assert not (output / "runtime_handoff.json").exists()
        assert not (output / "v3_trace.json").exists()
        assert not (output / "source_revision.json").exists()
        return subprocess.CompletedProcess(cmd, 1, stdout="")

    monkeypatch.setattr(adapter, "_run_v3", fake_run)
    result = adapter.process_repo("owner/repo", model="test")
    assert result["status"] == "partial"
    assert result["failure_reason"] == "v3_failed"
    assert result["certified_setup"] is False
    assert result["head_sha"] == _HEAD_SHA
    assert "echo \"$setup_rc\" > /tmp/v3_setup_exit_code" in result["dockerfile"]
    assert result["setup_scripts"]["setup.sh"] == "#!/usr/bin/env bash\ntrue\n"


def test_v3_timeout_returns_partial_evaluation_artifact(monkeypatch, tmp_path):
    root = tmp_path / "run"
    output = root / "output" / "owner" / "repo"
    repo = root / "input" / "repo" / "owner" / "repo"
    output.mkdir(parents=True)
    repo.mkdir(parents=True)
    adapter = RATV3Adapter(root_path=str(root), output_dir=str(output), agent_root=str(tmp_path))
    monkeypatch.setattr(adapter, "_ensure_repo", lambda _name: repo)
    monkeypatch.setattr(adapter, "_repo_head_sha", lambda _path: _HEAD_SHA)

    def timeout_run(_cmd, log_path, _timeout):
        log_path.write_text("[v3] base-image: python:3.12-slim (py 3.12)\n")
        raise subprocess.TimeoutExpired(_cmd, 10)

    monkeypatch.setattr(adapter, "_run_v3", timeout_run)
    result = adapter.process_repo("owner/repo", model="test", timeout=10)

    assert result["status"] == "partial"
    assert result["failure_reason"] == "v3_timeout"
    assert result["certified_setup"] is False
    assert result["dockerfile"].startswith("FROM python:3.12-slim")
    assert (output / "source_revision.json").exists()


def test_fresh_run_without_a_git_head_fails_before_v3(monkeypatch, tmp_path):
    root = tmp_path / "run"
    output = root / "output" / "owner" / "repo"
    repo = root / "input" / "repo" / "owner" / "repo"
    repo.mkdir(parents=True)
    adapter = RATV3Adapter(root_path=str(root), output_dir=str(output), agent_root=str(tmp_path))
    monkeypatch.setattr(adapter, "_ensure_repo", lambda _name: repo)
    monkeypatch.setattr(
        adapter, "_run_v3",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not call v3")),
    )

    result = adapter.process_repo("owner/repo", model="test")

    assert result["status"] == "error"
    assert result["failure_reason"] == "source_revision_unavailable"
    assert "dockerfile" not in result


def test_head_sha_normalization_accepts_only_full_hex_object_ids():
    assert _normalize_head_sha("A" * 40) == "a" * 40
    assert _normalize_head_sha("B" * 64) == "b" * 64
    for invalid in (None, "", "a" * 39, "a" * 41, "g" * 40, "a" * 40 + "; true"):
        with pytest.raises(ValueError):
            _normalize_head_sha(invalid)


def test_render_dockerfile_rejects_invalid_head_sha(tmp_path):
    adapter = RATV3Adapter(
        root_path=str(tmp_path / "run"), output_dir=str(tmp_path / "out")
    )
    with pytest.raises(ValueError):
        adapter._render_dockerfile(
            full_name="owner/repo",
            base_image="python:3.12-slim",
            setup_script_name="setup.sh",
            head_sha=_HEAD_SHA + "; true",
        )
