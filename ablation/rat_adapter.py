"""RAT packaging adapter for the ExecuteAgent-only ablation.

This module deliberately reuses only the graph-free boundary utilities from
``rat_v3_adapter``: obtaining an exact checkout revision and rendering the
resulting ``setup.sh`` into RAT's evaluation Dockerfile.  Environment synthesis
itself is delegated to ``ablation.run_execute_only``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from rat_v3_adapter import (
    RATV3Adapter,
    _SourceRevisionError,
    _normalize_head_sha,
    _repository_profile,
)


_SAFE_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")
_SAFE_PLATFORM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_ARTIFACT_NAMES = (
    "setup.sh",
    "result.json",
    "evidence.json",
    "trace.jsonl",
    "source_revision.json",
    "ablation_run.log",
    "ablation_llm.jsonl",
)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read ablation result {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"ablation result must be a JSON object: {path}")
    return value


def _safe_image(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_IMAGE.fullmatch(value.strip()):
        raise ValueError(f"invalid {field} in ablation result")
    return value.strip()


def _safe_platform(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not _SAFE_PLATFORM.fullmatch(value.strip()):
        raise ValueError("invalid platform in ablation result")
    return value.strip()


def _resolved_images(config: dict[str, Any], requested: str) -> tuple[str, str]:
    """Return ``(selected_base, downstream_build_ref)``.

    ``Sandbox.base_image_ref`` can be a raw local image id.  BuildKit may treat
    that value as a registry reference and try to pull it, so RAT must use the
    platform-specific ``base_image_alias`` when available and otherwise fall
    back to the selected ``base_image`` tag.
    """
    base_raw = config.get("base_image") or (
        requested if requested and requested != "auto" else None
    )
    base = _safe_image(base_raw, field="base_image")
    alias_raw = config.get("base_image_alias")
    build_ref = (
        base
        if alias_raw in (None, "")
        else _safe_image(alias_raw, field="base_image_alias")
    )
    return base, build_ref


class RATAblationAdapter(RATV3Adapter):
    """Generate a flat setup with ExecuteAgent, then package it for RAT.

    ``RATV3Adapter`` is inherited only for its checkout/revision helpers and
    Dockerfile renderer.  This class never calls the v3 runner and always emits
    empty runtime-service handoffs.
    """

    def _clear_artifacts(self, artifact_dir: Path) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for name in _ARTIFACT_NAMES:
            try:
                (artifact_dir / name).unlink()
            except FileNotFoundError:
                pass

    def _run_ablation(
        self,
        cmd: list[str],
        log_path: Path,
        timeout: int | None,
    ) -> subprocess.CompletedProcess[str]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w+", encoding="utf-8", errors="replace") as log_file:
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=self.agent_root,
                    text=True,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                    env={
                        **os.environ,
                        "PYTHONUNBUFFERED": "1",
                        "ENVSTATE_LLM_LOG": os.environ.get(
                            "ENVSTATE_LLM_LOG",
                            str(log_path.with_name("ablation_llm.jsonl")),
                        ),
                    },
                )
            except subprocess.TimeoutExpired:
                log_file.flush()
                raise
            log_file.flush()
            log_file.seek(0)
            output = log_file.read()
        return subprocess.CompletedProcess(
            args=completed.args,
            returncode=completed.returncode,
            stdout=output,
            stderr=None,
        )

    def _failure(
        self,
        reason: str,
        message: str,
        *,
        requested_base: str,
        head_sha: str = "",
        profile: dict[str, Any] | None = None,
        log_path: Path | None = None,
        returncode: int | None = None,
    ) -> dict[str, Any]:
        logs: dict[str, Any] = {
            "adapter": "ablation.rat_adapter",
            "error": message,
        }
        if log_path is not None:
            logs["ablation_log"] = str(log_path)
        if returncode is not None:
            logs["ablation_returncode"] = int(returncode)
        return {
            "status": "error",
            "failure_reason": reason,
            "base_image": requested_base,
            "head_sha": head_sha,
            **(profile or {}),
            "logs": logs,
        }

    def process_repo(
        self,
        full_name: str,
        *,
        base_image: str = "auto",
        model: str,
        timeout: int | None = None,
        max_cycles: int = 30,
        execution_mode: str = "fresh",
        reuse_existing: bool = False,
    ) -> dict[str, Any]:
        if not _SAFE_FULL_NAME.fullmatch(full_name):
            raise ValueError(f"unsupported GitHub repo name: {full_name!r}")
        if max_cycles <= 0:
            raise ValueError("max_cycles must be positive")

        # A RAT resume happens above this adapter at the completed result-row
        # boundary.  Reusing setup artifacts here would weaken the exact-source
        # and fresh-generation guarantees, so this boundary is always fresh.
        del execution_mode, reuse_existing

        artifact_dir = self.output_dir / "ablation_artifacts"
        setup_path = artifact_dir / "setup.sh"
        result_path = artifact_dir / "result.json"
        revision_path = artifact_dir / "source_revision.json"
        log_path = artifact_dir / "ablation_run.log"
        self._clear_artifacts(artifact_dir)

        try:
            repo_path = self._ensure_repo(full_name)
            head_sha = self._repo_head_sha(repo_path)
            profile = _repository_profile(repo_path)
        except _SourceRevisionError as exc:
            return self._failure(
                exc.reason,
                str(exc),
                requested_base=base_image,
            )
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            return self._failure(
                "source_revision_unavailable",
                str(exc),
                requested_base=base_image,
            )

        cmd = [
            sys.executable,
            "-m",
            "ablation.run_execute_only",
            str(repo_path),
            "--model",
            model,
            "--base-image",
            base_image or "auto",
            "--language-hint",
            str(profile["primary_language"]),
            "--output-dir",
            str(artifact_dir),
            "--max-cycles",
            str(max_cycles),
            "--max-agent-calls",
            str(max_cycles),
            "--max-turns-per-decision",
            "50",
            "--completion-policy",
            "environment_ready",
        ]
        completed = self._run_ablation(cmd, log_path, timeout)

        try:
            result = _read_object(result_path)
        except ValueError as exc:
            return self._failure(
                "ablation_result_invalid",
                str(exc),
                requested_base=base_image,
                head_sha=head_sha,
                profile=profile,
                log_path=log_path,
                returncode=completed.returncode,
            )

        if completed.returncode != 0 or result.get("status") != "success":
            reason = str(result.get("stop_reason") or "ablation_failed")
            return self._failure(
                reason,
                "ExecuteAgent-only ablation did not produce a certified environment",
                requested_base=base_image,
                head_sha=head_sha,
                profile=profile,
                log_path=log_path,
                returncode=completed.returncode,
            )
        if not setup_path.is_file():
            return self._failure(
                "no_setup_sh",
                "successful ablation result is missing setup.sh",
                requested_base=base_image,
                head_sha=head_sha,
                profile=profile,
                log_path=log_path,
                returncode=completed.returncode,
            )

        config = result.get("config")
        if not isinstance(config, dict):
            return self._failure(
                "ablation_result_invalid",
                "successful ablation result is missing its config object",
                requested_base=base_image,
                head_sha=head_sha,
                profile=profile,
                log_path=log_path,
                returncode=completed.returncode,
            )
        try:
            result_revision = _normalize_head_sha(config.get("repo_revision"))
        except ValueError as exc:
            return self._failure(
                "source_revision_invalid",
                str(exc),
                requested_base=base_image,
                head_sha=head_sha,
                profile=profile,
                log_path=log_path,
                returncode=completed.returncode,
            )
        if result_revision != head_sha:
            return self._failure(
                "source_revision_mismatch",
                (
                    f"ablation result revision {result_revision} does not match "
                    f"checkout revision {head_sha}"
                ),
                requested_base=base_image,
                head_sha=head_sha,
                profile=profile,
                log_path=log_path,
                returncode=completed.returncode,
            )

        try:
            selected_image, build_image = _resolved_images(config, base_image)
            platform = _safe_platform(config.get("platform"))
            self._write_source_revision(revision_path, full_name, head_sha)
            setup_text = setup_path.read_text(encoding="utf-8", errors="replace")
            dockerfile = self._render_dockerfile(
                full_name=full_name,
                base_image=build_image,
                setup_script_name="setup.sh",
                head_sha=head_sha,
                platform=platform,
                runtime_environment={},
                primary_language=str(profile["primary_language"]),
            )
        except (OSError, ValueError) as exc:
            return self._failure(
                "ablation_result_invalid",
                str(exc),
                requested_base=base_image,
                head_sha=head_sha,
                profile=profile,
                log_path=log_path,
                returncode=completed.returncode,
            )

        return {
            "status": "success",
            "failure_reason": None,
            "base_image": selected_image,
            "base_image_ref": build_image,
            "head_sha": head_sha,
            "platform": platform,
            **profile,
            "dockerfile": dockerfile,
            "setup_scripts": {"setup.sh": setup_text},
            # The flat ablation has no graph-derived service handoff.  RAT must
            # therefore never replay runtime services on its behalf.
            "runtime_services": [],
            "runtime_commands": [],
            "runtime_environment": {},
            "logs": {
                "adapter": "ablation.rat_adapter",
                "repo_path": str(repo_path),
                "setup_sh": str(setup_path),
                "result": str(result_path),
                "trace": str(artifact_dir / "trace.jsonl"),
                "evidence": str(artifact_dir / "evidence.json"),
                "source_revision": str(revision_path),
                "ablation_log": str(log_path),
                "ablation_llm": str(artifact_dir / "ablation_llm.jsonl"),
                "ablation_returncode": completed.returncode,
            },
        }


__all__ = ["RATAblationAdapter"]
