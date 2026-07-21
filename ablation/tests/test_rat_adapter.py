from __future__ import annotations

import json
import subprocess
import sys

import pytest

from ablation.rat_adapter import RATAblationAdapter, _resolved_images


_HEAD_SHA = "a" * 40
_OTHER_SHA = "b" * 40
_PROFILE = {
    "primary_language": "python",
    "language": "python",
    "detected_languages": ["python"],
    "build_system": "pypi",
}


def _adapter(monkeypatch, tmp_path):
    root = tmp_path / "rat-run"
    output = root / "output" / "owner" / "repo"
    repo = root / "input" / "repo" / "owner" / "repo"
    repo.mkdir(parents=True)
    adapter = RATAblationAdapter(
        root_path=str(root),
        output_dir=str(output),
        agent_root=str(tmp_path),
    )
    monkeypatch.setattr(adapter, "_ensure_repo", lambda _name: repo)
    monkeypatch.setattr(adapter, "_repo_head_sha", lambda _repo: _HEAD_SHA)
    monkeypatch.setattr(
        "ablation.rat_adapter._repository_profile",
        lambda _repo: dict(_PROFILE),
    )
    return adapter, repo, output / "ablation_artifacts"


def _write_success(
    artifact_dir,
    *,
    revision: str = _HEAD_SHA,
    base_image: str = "python:3.11-slim",
    image_alias: str = "jayint-ablation-base:abc-linux-arm64",
    platform: str = "linux/arm64",
):
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "setup.sh").write_text(
        "#!/usr/bin/env bash\npython -m pip install -e .\n",
        encoding="utf-8",
    )
    (artifact_dir / "result.json").write_text(
        json.dumps(
            {
                "arm": "w/o_depgraph_execute_agent_only",
                "status": "success",
                "stop_reason": "environment_ready",
                "config": {
                    "repo_revision": revision,
                    "base_image_alias": image_alias,
                    "base_image": base_image,
                    "platform": platform,
                },
            }
        ),
        encoding="utf-8",
    )


def test_adapter_runs_environment_ready_and_packages_for_rat(monkeypatch, tmp_path):
    adapter, repo, artifact_dir = _adapter(monkeypatch, tmp_path)
    artifact_dir.mkdir(parents=True)
    for name in (
        "setup.sh",
        "result.json",
        "evidence.json",
        "trace.jsonl",
        "source_revision.json",
        "ablation_run.log",
        "ablation_llm.jsonl",
    ):
        (artifact_dir / name).write_text("stale", encoding="utf-8")

    seen = {}

    def fake_run(cmd, log_path, timeout):
        seen["cmd"] = cmd
        seen["log_path"] = log_path
        seen["timeout"] = timeout
        assert all(
            not (artifact_dir / name).exists()
            for name in (
                "setup.sh",
                "result.json",
                "evidence.json",
                "trace.jsonl",
                "source_revision.json",
                "ablation_run.log",
                "ablation_llm.jsonl",
            )
        )
        _write_success(artifact_dir)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr=None)

    monkeypatch.setattr(adapter, "_run_ablation", fake_run)
    result = adapter.process_repo(
        "owner/repo",
        base_image="auto",
        model="test-model",
        timeout=321,
        max_cycles=17,
        execution_mode="incremental",
        reuse_existing=True,
    )

    assert result["status"] == "success"
    assert result["base_image"] == "python:3.11-slim"
    assert result["base_image_ref"] == "jayint-ablation-base:abc-linux-arm64"
    assert result["platform"] == "linux/arm64"
    assert result["head_sha"] == _HEAD_SHA
    assert result["runtime_services"] == []
    assert result["runtime_commands"] == []
    assert result["runtime_environment"] == {}
    assert result["dockerfile"].startswith(
        "FROM --platform=linux/arm64 jayint-ablation-base:abc-linux-arm64"
    )
    assert f"ARG SOURCE_SHA={_HEAD_SHA}" in result["dockerfile"]
    assert result["setup_scripts"]["setup.sh"].startswith("#!/usr/bin/env bash")
    assert seen["timeout"] == 321
    assert seen["log_path"] == artifact_dir / "ablation_run.log"

    command = seen["cmd"]
    assert command[:3] == [sys.executable, "-m", "ablation.run_execute_only"]
    assert command[3] == str(repo)
    assert command[command.index("--completion-policy") + 1] == "environment_ready"
    assert command[command.index("--max-cycles") + 1] == "17"
    assert command[command.index("--max-agent-calls") + 1] == "17"
    assert command[command.index("--max-turns-per-decision") + 1] == "50"
    assert command[command.index("--language-hint") + 1] == "python"

    revision = json.loads(
        (artifact_dir / "source_revision.json").read_text(encoding="utf-8")
    )
    assert revision == {
        "version": 1,
        "full_name": "owner/repo",
        "head_sha": _HEAD_SHA,
    }


def test_adapter_rejects_result_bound_to_another_revision(monkeypatch, tmp_path):
    adapter, _repo, artifact_dir = _adapter(monkeypatch, tmp_path)

    def fake_run(cmd, _log_path, _timeout):
        _write_success(artifact_dir, revision=_OTHER_SHA)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr=None)

    monkeypatch.setattr(adapter, "_run_ablation", fake_run)
    result = adapter.process_repo("owner/repo", model="test")

    assert result["status"] == "error"
    assert result["failure_reason"] == "source_revision_mismatch"
    assert "dockerfile" not in result
    assert result["head_sha"] == _HEAD_SHA


def test_failed_fresh_run_cannot_reuse_a_stale_setup(monkeypatch, tmp_path):
    adapter, _repo, artifact_dir = _adapter(monkeypatch, tmp_path)
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "setup.sh").write_text("stale setup", encoding="utf-8")
    (artifact_dir / "result.json").write_text(
        json.dumps({"status": "success", "config": {"repo_revision": _HEAD_SHA}}),
        encoding="utf-8",
    )

    def fake_run(cmd, _log_path, _timeout):
        assert not (artifact_dir / "setup.sh").exists()
        (artifact_dir / "result.json").write_text(
            json.dumps(
                {
                    "status": "failed",
                    "stop_reason": "agent_budget_exhausted",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 1, stdout="failed", stderr=None)

    monkeypatch.setattr(adapter, "_run_ablation", fake_run)
    result = adapter.process_repo("owner/repo", model="test", reuse_existing=True)

    assert result["status"] == "error"
    assert result["failure_reason"] == "agent_budget_exhausted"
    assert "dockerfile" not in result
    assert not (artifact_dir / "setup.sh").exists()


def test_adapter_rejects_unsafe_image_or_platform_from_result(monkeypatch, tmp_path):
    adapter, _repo, artifact_dir = _adapter(monkeypatch, tmp_path)

    def fake_run(cmd, _log_path, _timeout):
        _write_success(
            artifact_dir,
            image_alias="python:3.11-slim\nRUN touch /tmp/injected",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr=None)

    monkeypatch.setattr(adapter, "_run_ablation", fake_run)
    result = adapter.process_repo("owner/repo", model="test")

    assert result["status"] == "error"
    assert result["failure_reason"] == "ablation_result_invalid"
    assert "dockerfile" not in result


def test_adapter_falls_back_to_selected_base_without_stable_alias():
    assert _resolved_images(
        {
            "base_image": "python:3.12-slim",
            "base_image_ref": "sha256:" + "a" * 64,
        },
        "auto",
    ) == ("python:3.12-slim", "python:3.12-slim")


def test_adapter_requires_safe_repo_name(tmp_path):
    adapter = RATAblationAdapter(
        root_path=str(tmp_path / "run"),
        output_dir=str(tmp_path / "out"),
        agent_root=str(tmp_path),
    )
    with pytest.raises(ValueError):
        adapter.process_repo("owner/repo;touch-pwned", model="test")
