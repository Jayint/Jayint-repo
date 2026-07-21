"""RAT adapter for the slim v3 core checkout.

The historical RAT path imported ``multi_docker_eval_adapter`` which in turn
constructed ``agent.DockerAgent``.  This checkout intentionally no longer ships
that monolithic agent entrypoint, so RAT needs a thin adapter that calls the v3
core directly and then packages the resulting ``setup.sh`` for RAT's Docker
pytest harness.
"""
from __future__ import annotations

import os
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


_SAFE_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FULL_GIT_OID = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_BASE_IMAGE_RE = re.compile(r"^\[v3\] base-image:\s+(\S+)", re.MULTILINE)
_PLATFORM_RE = re.compile(r"^\[Platform\] Using platform:\s+(\S+)", re.MULTILINE)
_STABLE_IMAGE_RE = re.compile(r"^\[Platform\] Stable image:\s+(\S+)", re.MULTILINE)
_SOURCE_REVISION_VERSION = 1
_RAT_LANGUAGE_ALIASES = {
    "javascript": "nodejs",
    "typescript": "nodejs",
}

_CURATED_RUNTIME_SERVICES = {
    "redis": {
        "start": "redis-server --daemonize yes",
        "check": (
            "python3 -c \"import socket; "
            "s=socket.create_connection(('127.0.0.1',6379),2); s.close()\""
        ),
    },
}


class _SourceRevisionError(RuntimeError):
    """A setup artifact cannot be bound to one exact source commit."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _normalize_head_sha(value: Any) -> str:
    """Return a canonical full Git object id or reject ambiguous input."""
    if not isinstance(value, str) or not _FULL_GIT_OID.fullmatch(value.strip()):
        raise ValueError("head_sha must be a full 40- or 64-character hexadecimal object id")
    return value.strip().lower()


def _token_usage_from_file(path: Path) -> dict[str, int]:
    """Read one repository's eagerly persisted LLM accounting."""
    empty = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "api_calls": 0,
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(payload, dict):
        return empty
    normalized = {}
    for key in empty:
        try:
            normalized[key] = max(0, int(payload.get(key, 0) or 0))
        except (TypeError, ValueError):
            normalized[key] = 0
    return normalized


def _runtime_services_from_handoff(path: Path) -> list[dict[str, str]]:
    """Read only exact host-curated service recipes from the v3 handoff."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return []
    services = payload.get("services")
    if not isinstance(services, list):
        return []
    accepted: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in services:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        expected = _CURATED_RUNTIME_SERVICES.get(kind)
        if (
            expected is None
            or kind in seen
            or item.get("start") != expected["start"]
            or item.get("check") != expected["check"]
        ):
            continue
        accepted.append({"kind": kind, **expected})
        seen.add(kind)
    return accepted


def _runtime_commands_from_handoff(path: Path) -> list[str]:
    """Compatibility projection of validated service records to start commands."""
    return [item["start"] for item in _runtime_services_from_handoff(path)]


def _runtime_environment_from_handoff(path: Path) -> dict[str, str]:
    """Read the narrow evaluator environment policy emitted by v3.

    Do not pass arbitrary repository- or model-provided variables into the
    Dockerfile.  The sole accepted value is the deterministic pytest layout
    policy produced by static test-intent discovery.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {}
    environment = payload.get("environment")
    if not isinstance(environment, dict):
        return {}
    if environment.get("PYTEST_ADDOPTS") == "--import-mode=importlib":
        return {"PYTEST_ADDOPTS": "--import-mode=importlib"}
    return {}


def _repository_profile(repo_path: Path) -> dict[str, Any]:
    """Return deterministic language/build metadata for RAT evaluation."""
    try:
        from src.language_handlers import detect_languages

        requirements = detect_languages(str(repo_path))
    except Exception:
        requirements = ()
    primary = next(
        (
            item.language
            for item in requirements
            if item.role == "primary_runtime"
        ),
        requirements[0].language if requirements else "python",
    )
    language = _RAT_LANGUAGE_ALIASES.get(primary, primary)
    if (repo_path / "pom.xml").is_file() or (repo_path / "mvnw").is_file():
        build_system = "maven"
    elif any(
        (repo_path / name).is_file()
        for name in ("build.gradle", "build.gradle.kts", "gradlew")
    ):
        build_system = "gradle"
    elif (repo_path / "package.json").is_file():
        build_system = "npm"
    elif (repo_path / "Cargo.toml").is_file():
        build_system = "cargo"
    elif (repo_path / "go.mod").is_file():
        build_system = "go"
    else:
        build_system = "pypi"
    return {
        "primary_language": primary,
        "language": language,
        "detected_languages": [item.language for item in requirements],
        "build_system": build_system,
    }


class RATV3Adapter:
    """Run ``scripts/run_v3_e2e.py`` and render a RAT-evaluable Dockerfile."""

    def __init__(self, *, root_path: str, output_dir: str, agent_root: str | None = None):
        self.root_path = Path(root_path).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.agent_root = Path(agent_root or os.environ.get("DOCKERAGENT_ROOT", ".")).resolve()

    def process_repo(
        self,
        full_name: str,
        *,
        base_image: str = "auto",
        model: str,
        timeout: int | None = None,
        max_cycles: int = 30,
        execution_mode: str = "incremental",
        reuse_existing: bool = False,
    ) -> dict[str, Any]:
        if not _SAFE_FULL_NAME.match(full_name):
            raise ValueError(f"unsupported GitHub repo name: {full_name!r}")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        setup_path = self.output_dir / "setup.sh"
        trace_path = self.output_dir / "v3_trace.json"
        runtime_path = self.output_dir / "runtime_handoff.json"
        token_usage_path = self.output_dir / "token_usage.json"
        log_path = self.output_dir / "v3_run.log"
        source_revision_path = self.output_dir / "source_revision.json"
        setup_status_path = self.output_dir / "setup_status.json"

        if reuse_existing and setup_path.exists():
            output = (
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path.exists() else ""
            )
            resolved_base = self._resolved_base_image(output, base_image)
            resolved_ref = self._resolved_base_image_ref(output, resolved_base)
            resolved_platform = self._resolved_platform(output)
            try:
                head_sha = self._read_source_revision(source_revision_path, full_name)
            except _SourceRevisionError as exc:
                return self._source_revision_failure(
                    exc,
                    base_image=resolved_base,
                    source_revision_path=source_revision_path,
                )
            setup_text = setup_path.read_text(encoding="utf-8", errors="replace")
            setup_certified = True
            setup_failure_reason = None
            if setup_status_path.is_file():
                try:
                    setup_status = json.loads(setup_status_path.read_text(encoding="utf-8"))
                    setup_certified = bool(setup_status.get("certified_setup"))
                    setup_failure_reason = setup_status.get("failure_reason")
                except (OSError, json.JSONDecodeError, AttributeError):
                    setup_certified = False
                    setup_failure_reason = "invalid_setup_status"
            runtime_services = _runtime_services_from_handoff(runtime_path)
            runtime_environment = _runtime_environment_from_handoff(runtime_path)
            owner, repo = full_name.split("/", 1)
            repo_path = self.root_path / "input" / "repo" / owner / repo
            profile = _repository_profile(repo_path)
            return {
                "status": "success" if setup_certified else "partial",
                "failure_reason": setup_failure_reason,
                "certified_setup": setup_certified,
                "base_image": resolved_base,
                "base_image_ref": resolved_ref,
                "head_sha": head_sha,
                "dockerfile": self._render_dockerfile(
                    full_name=full_name,
                    base_image=resolved_ref,
                    setup_script_name="setup.sh",
                    head_sha=head_sha,
                    platform=resolved_platform,
                    runtime_environment=runtime_environment,
                    primary_language=profile["primary_language"],
                    tolerate_setup_failure=not setup_certified,
                ),
                "platform": resolved_platform,
                **profile,
                "setup_scripts": {"setup.sh": setup_text},
                "runtime_services": runtime_services,
                "runtime_commands": [item["start"] for item in runtime_services],
                "runtime_environment": runtime_environment,
                "token_usage": _token_usage_from_file(token_usage_path),
                "logs": {
                    "adapter": "rat_v3_adapter",
                    "reused_existing": True,
                    "setup_sh": str(setup_path),
                    "trace": str(trace_path) if trace_path.exists() else "",
                    "runtime_handoff": str(runtime_path) if runtime_path.exists() else "",
                    "token_usage": str(token_usage_path) if token_usage_path.exists() else "",
                    "source_revision": str(source_revision_path),
                    "setup_status": str(setup_status_path) if setup_status_path.exists() else "",
                    "v3_log": str(log_path) if log_path.exists() else "",
                },
            }

        # A failed fresh v3 subprocess must never be mistaken for success merely
        # because the same output directory contains a prior setup/handoff.
        for stale_path in (
            setup_path, trace_path, runtime_path, token_usage_path,
            source_revision_path, setup_status_path,
        ):
            try:
                stale_path.unlink()
            except FileNotFoundError:
                pass

        try:
            repo_path = self._ensure_repo(full_name)
            head_sha = self._repo_head_sha(repo_path)
            profile = _repository_profile(repo_path)
        except _SourceRevisionError as exc:
            return self._source_revision_failure(
                exc,
                base_image=base_image,
                source_revision_path=source_revision_path,
            )
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            return self._source_revision_failure(
                _SourceRevisionError("source_revision_unavailable", str(exc)),
                base_image=base_image,
                source_revision_path=source_revision_path,
            )
        cmd = [
            sys.executable,
            str(self.agent_root / "scripts" / "run_v3_e2e.py"),
            str(repo_path),
            "--model",
            model,
            "--base-image",
            base_image or "auto",
            "--language-hint",
            profile["primary_language"],
            "--out",
            str(setup_path),
            "--trace-out",
            str(trace_path),
            "--runtime-out",
            str(runtime_path),
            "--usage-out",
            str(token_usage_path),
            "--max-cycles",
            str(max_cycles),
            "--execution-mode",
            execution_mode,
        ]
        v3_timed_out = False
        try:
            completed = self._run_v3(cmd, log_path, timeout)
        except subprocess.TimeoutExpired:
            # Preserve evaluation coverage even when graph search exhausts its
            # budget.  The RAT harness can still build a best-effort image from
            # the selected base and whatever setup artifact was emitted so far.
            v3_timed_out = True
            try:
                output = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                output = ""
            completed = subprocess.CompletedProcess(
                args=cmd,
                returncode=124,
                stdout=output,
                stderr=None,
            )
        output = completed.stdout or ""
        token_usage = _token_usage_from_file(token_usage_path)
        resolved_base = self._resolved_base_image(output, base_image)
        resolved_ref = self._resolved_base_image_ref(output, resolved_base)
        resolved_platform = self._resolved_platform(output)

        certified_setup = completed.returncode == 0 and setup_path.exists()
        if setup_path.exists():
            setup_text = setup_path.read_text(encoding="utf-8", errors="replace")
        else:
            # A timeout can happen before run_v3_e2e reaches artifact emission.
            # An empty setup still lets the evaluator clone the exact revision,
            # attempt the deterministic editable-install bootstrap, and run tests.
            setup_text = "#!/usr/bin/env bash\ntrue\n"
            setup_path.write_text(setup_text, encoding="utf-8")

        if v3_timed_out:
            setup_failure_reason = "v3_timeout"
        elif completed.returncode != 0:
            setup_failure_reason = "v3_failed"
        elif not certified_setup:
            setup_failure_reason = "no_setup_sh"
        else:
            setup_failure_reason = None
        runtime_services = _runtime_services_from_handoff(runtime_path)
        runtime_environment = _runtime_environment_from_handoff(runtime_path)
        try:
            self._write_source_revision(source_revision_path, full_name, head_sha)
        except (OSError, ValueError) as exc:
            return self._source_revision_failure(
                _SourceRevisionError("source_revision_unavailable", str(exc)),
                base_image=resolved_base,
                source_revision_path=source_revision_path,
                head_sha=head_sha,
                token_usage=token_usage,
            )
        setup_status_path.write_text(
            json.dumps({
                "version": 1,
                "certified_setup": certified_setup,
                "failure_reason": setup_failure_reason,
                "v3_returncode": completed.returncode,
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        dockerfile = self._render_dockerfile(
            full_name=full_name,
            base_image=resolved_ref,
            setup_script_name="setup.sh",
            head_sha=head_sha,
            platform=resolved_platform,
            runtime_environment=runtime_environment,
            primary_language=profile["primary_language"],
            tolerate_setup_failure=not certified_setup,
        )
        return {
            "status": "success" if certified_setup else "partial",
            "failure_reason": setup_failure_reason,
            "certified_setup": certified_setup,
            "base_image": resolved_base,
            "base_image_ref": resolved_ref,
            "head_sha": head_sha,
            "platform": resolved_platform,
            **profile,
            "dockerfile": dockerfile,
            "setup_scripts": {"setup.sh": setup_text},
            "runtime_services": runtime_services,
            "runtime_commands": [item["start"] for item in runtime_services],
            "runtime_environment": runtime_environment,
            "token_usage": token_usage,
            "logs": {
                "adapter": "rat_v3_adapter",
                "repo_path": str(repo_path),
                "setup_sh": str(setup_path),
                "trace": str(trace_path) if trace_path.exists() else "",
                "runtime_handoff": str(runtime_path) if runtime_path.exists() else "",
                "token_usage": str(token_usage_path),
                "source_revision": str(source_revision_path),
                "setup_status": str(setup_status_path),
                "llm_trace": str(log_path.with_name("v3_llm.jsonl")),
                "v3_returncode": completed.returncode,
                "v3_log": str(log_path),
                "v3_tail": output[-8000:] if not certified_setup else "",
            },
        }

    def _ensure_repo(self, full_name: str) -> Path:
        owner, repo = full_name.split("/", 1)
        repo_path = self.root_path / "input" / "repo" / owner / repo
        repo_url = f"https://github.com/{full_name}.git"

        if (repo_path / ".git").exists():
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo_path, check=True)
            subprocess.run(["git", "clean", "-fdx"], cwd=repo_path, check=True)
            return repo_path

        if repo_path.exists() and any(repo_path.iterdir()):
            raise RuntimeError(f"repo path exists but is not a git checkout: {repo_path}")
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        if repo_path.exists():
            repo_path.rmdir()
        subprocess.run(["git", "clone", "--depth=1", repo_url, str(repo_path)], check=True)
        return repo_path

    def _repo_head_sha(self, repo_path: Path) -> str:
        """Resolve the exact clean checkout commit used by the v3 analysis."""
        if not (repo_path / ".git").exists():
            raise _SourceRevisionError(
                "source_revision_unavailable",
                f"source path is not a Git checkout: {repo_path}",
            )
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--verify", "HEAD^{commit}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return _normalize_head_sha(completed.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise _SourceRevisionError(
                "source_revision_unavailable",
                f"could not resolve exact source HEAD for {repo_path}: {exc}",
            ) from exc

    def _read_source_revision(self, path: Path, full_name: str) -> str:
        if not path.is_file():
            raise _SourceRevisionError(
                "source_revision_missing",
                f"reusable setup is missing its source revision sidecar: {path}",
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _SourceRevisionError(
                "source_revision_invalid",
                f"could not read source revision sidecar {path}: {exc}",
            ) from exc
        try:
            if not isinstance(payload, dict) or payload.get("version") != _SOURCE_REVISION_VERSION:
                raise ValueError("unsupported source revision schema")
            if payload.get("full_name") != full_name:
                raise ValueError("source revision repository does not match requested repository")
            return _normalize_head_sha(payload.get("head_sha"))
        except ValueError as exc:
            raise _SourceRevisionError(
                "source_revision_invalid",
                f"invalid source revision sidecar {path}: {exc}",
            ) from exc

    def _write_source_revision(self, path: Path, full_name: str, head_sha: str) -> None:
        payload = {
            "version": _SOURCE_REVISION_VERSION,
            "full_name": full_name,
            "head_sha": _normalize_head_sha(head_sha),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                json.dump(payload, tmp_file, indent=2, sort_keys=True)
                tmp_file.write("\n")
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_name, path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def _source_revision_failure(
        self,
        exc: _SourceRevisionError,
        *,
        base_image: str,
        source_revision_path: Path,
        head_sha: str = "",
        token_usage: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "error",
            "failure_reason": exc.reason,
            "base_image": base_image,
            "head_sha": head_sha,
            "token_usage": token_usage or _token_usage_from_file(
                self.output_dir / "token_usage.json"
            ),
            "logs": {
                "error": str(exc),
                "source_revision": str(source_revision_path),
            },
        }

    def _run_v3(
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
                            "ENVSTATE_LLM_LOG", str(log_path.with_name("v3_llm.jsonl"))
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

    def _resolved_base_image(self, output: str, requested: str) -> str:
        match = _BASE_IMAGE_RE.search(output or "")
        if match:
            return match.group(1)
        if requested and requested != "auto":
            return requested
        return "python:3.10-slim"

    def _resolved_platform(self, output: str) -> str:
        match = _PLATFORM_RE.search(output or "")
        return match.group(1) if match else ""

    def _resolved_base_image_ref(self, output: str, fallback: str) -> str:
        match = _STABLE_IMAGE_RE.search(output or "")
        return match.group(1) if match else fallback

    def _render_dockerfile(
        self,
        *,
        full_name: str,
        base_image: str,
        setup_script_name: str,
        head_sha: str,
        platform: str = "",
        runtime_environment: dict[str, str] | None = None,
        primary_language: str = "python",
        tolerate_setup_failure: bool = False,
    ) -> str:
        repo_url = shlex.quote(f"https://github.com/{full_name}.git")
        setup_name = shlex.quote(setup_script_name)
        source_sha = shlex.quote(_normalize_head_sha(head_sha))
        from_line = (
            f"FROM --platform={platform} {base_image}"
            if platform else f"FROM {base_image}"
        )
        pytest_env = ""
        if (runtime_environment or {}).get("PYTEST_ADDOPTS") == "--import-mode=importlib":
            pytest_env = " \\\n    PYTEST_ADDOPTS=--import-mode=importlib"
        setup_run = "RUN chmod +x /tmp/setup.sh && cd /app && bash /tmp/setup.sh"
        if tolerate_setup_failure:
            setup_run = """RUN chmod +x /tmp/setup.sh && \\
    cd /app && \\
    set +e; \\
    bash /tmp/setup.sh; \\
    setup_rc=$?; \\
    echo \"$setup_rc\" > /tmp/v3_setup_exit_code; \\
    exit 0"""
        python_bootstrap = ""
        if primary_language == "python":
            python_bootstrap = """
RUN cd /testbed && \\
    (python3 -m pip install --break-system-packages -e . || \\
     python3 -m pip install -e . || true)

RUN python3 -m pip install --break-system-packages pytest || \\
    python3 -m pip install pytest || true
"""
        return f"""{from_line}

ARG SOURCE_SHA={source_sha}

ENV DEBIAN_FRONTEND=noninteractive \\
    PIP_DISABLE_PIP_VERSION_CHECK=1{pytest_env}

WORKDIR /testbed

RUN if command -v apt-get >/dev/null 2>&1; then \\
      apt-get update && \\
      apt-get install -y --no-install-recommends git ca-certificates bash python3 && \\
      rm -rf /var/lib/apt/lists/*; \\
    elif command -v apk >/dev/null 2>&1; then \\
      apk add --no-cache git ca-certificates bash coreutils python3 py3-pip; \\
    elif command -v dnf >/dev/null 2>&1; then \\
      dnf install -y git ca-certificates bash coreutils python3 && dnf clean all; \\
    elif command -v yum >/dev/null 2>&1; then \\
      yum install -y git ca-certificates bash coreutils python3 && yum clean all; \\
    else \\
      echo 'unsupported base image: cannot install git/bash/coreutils/python3' >&2; \\
      exit 127; \\
    fi

RUN command -v git >/dev/null 2>&1
RUN git init /testbed && \\
    git -C /testbed remote add origin {repo_url} && \\
    git -C /testbed fetch --no-tags --depth=1 origin "$SOURCE_SHA" && \\
    git -C /testbed cat-file -e "${{SOURCE_SHA}}^{{commit}}" && \\
    git -C /testbed checkout --detach "$SOURCE_SHA" && \\
    test "$(git -C /testbed rev-parse HEAD)" = "$SOURCE_SHA"
RUN ln -sfn /testbed /app && ln -sfn /testbed /repo

COPY {setup_name} /tmp/setup.sh
{setup_run}
{python_bootstrap}

WORKDIR /testbed
"""
