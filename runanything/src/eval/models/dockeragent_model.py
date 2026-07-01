#!/usr/bin/env python3
"""DockerAgent adapter for the offline RAT runner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import weave

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from eval.common.base_model import BaseEvalModel
from eval.common.utils import TimeoutException


class DockerAgentModel(BaseEvalModel):
    """Run this repo's DockerAgent and score the generated image with RAT tools."""

    llm: str
    num_turn: int

    @weave.op
    def predict(self, full_name: str) -> dict:
        start_time = time.time()
        print(f"\n{'=' * 60}")
        print(f"Processing: {full_name} (DockerAgent)")
        print(f"{'=' * 60}")

        repo_root = Path(os.environ.get("DOCKERAGENT_ROOT") or Path.cwd()).resolve()
        output_dir = Path(self.root_path) / "output" / full_name
        workplace = Path(self.root_path) / "workplaces" / full_name.replace("/", "__")
        eval_dir = output_dir / "eval_build"
        output_dir.mkdir(parents=True, exist_ok=True)
        eval_dir.mkdir(parents=True, exist_ok=True)

        image_name = f"dockeragent-eval-{_sanitize_name(full_name)}"
        container_name = f"dockeragent-rat-{_sanitize_name(full_name)}"

        try:
            agent_result = self._run_agent(
                repo_root=repo_root,
                full_name=full_name,
                workplace=workplace,
                output_dir=output_dir,
                start_time=start_time,
            )
            if agent_result["returncode"] != 0:
                return self._error_result(
                    full_name,
                    "agent_failed",
                    f"agent.py exited with {agent_result['returncode']}",
                )

            summary_path = workplace / "agent_run_summary.json"
            run_summary = _load_json(summary_path)
            dockerfile_path = workplace / "Dockerfile"
            if not dockerfile_path.exists():
                return self._error_result(full_name, "dockerfile_missing", "Dockerfile missing")
            summary_error = _validate_agent_run_summary(run_summary, summary_path)
            if summary_error:
                return self._error_result(full_name, "agent_failed", summary_error)
            summary_warning = _agent_run_summary_warning(run_summary)
            if summary_warning:
                print(f"⚠️ Agent summary warning: {summary_warning}")

            eval_dockerfile = self._write_eval_dockerfile(
                repo_root=repo_root,
                dockerfile_path=dockerfile_path,
                eval_dir=eval_dir,
            )
            self._build_image(
                image_name=image_name,
                dockerfile_path=eval_dockerfile,
                context_dir=workplace,
                output_dir=output_dir,
                start_time=start_time,
            )
            self._run_rat_pytest_tools(
                repo_root=repo_root,
                image_name=image_name,
                container_name=container_name,
                output_dir=output_dir,
                workdir=_infer_workdir(repo_root, eval_dockerfile),
                start_time=start_time,
            )

            execution_time = round(time.time() - start_time, 2)
            print(f"✅ DockerAgent RAT run completed. Time: {execution_time}s")
            return {
                "status": "success",
                "root_path": self.root_path,
                "full_name": full_name,
                "requested_model": "dockeragent",
                "base_image": run_summary.get("base_image"),
                "head_sha": self._read_head_sha(workplace),
                "agent_configuration_success": run_summary.get("configuration_success"),
                "agent_warning": summary_warning,
            }
        except subprocess.TimeoutExpired as exc:
            print(f"⏱️ Timeout: {exc}")
            return self._error_result(full_name, "timeout", str(exc), status="timeout")
        except TimeoutException as exc:
            print(f"⏱️ Timeout: {exc}")
            return self._error_result(full_name, "timeout", str(exc), status="timeout")
        except Exception as exc:
            print(f"❌ DockerAgent adapter error: {exc}")
            return self._error_result(full_name, "repo_error", str(exc))
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _remaining_timeout(self, start_time: float) -> int:
        elapsed = time.time() - start_time
        remaining = int(max(30, self.timeout - elapsed))
        if elapsed > self.timeout:
            raise TimeoutException(f"Timeout ({elapsed:.1f}s > {self.timeout}s)")
        return remaining

    def _run_agent(
        self,
        *,
        repo_root: Path,
        full_name: str,
        workplace: Path,
        output_dir: Path,
        start_time: float,
    ) -> dict:
        agent_path = repo_root / "agent.py"
        if not agent_path.exists():
            raise FileNotFoundError(f"agent.py not found: {agent_path}")

        if workplace.exists():
            shutil.rmtree(workplace)
        workplace.parent.mkdir(parents=True, exist_ok=True)

        repair_mode = os.environ.get("DOCKERAGENT_REPAIR_MODE", "selfverify")
        repair_rounds = os.environ.get("DOCKERAGENT_REPAIR_ROUNDS", "1")
        command = [
            sys.executable,
            str(agent_path),
            f"https://github.com/{full_name}.git",
            "--workplace",
            str(workplace),
            "--model",
            self.llm,
            "--steps",
            str(self.num_turn),
            "--command-timeout",
            str(max(60, min(1800, self.timeout))),
            "--evaluation-target",
            "ratbench",
        ]
        if repair_mode in {"selfverify", "both"}:
            command.extend(["--enable-dockerfile-repair", "--dockerfile-repair-rounds", repair_rounds])

        log_path = output_dir / "dockeragent_stdout.log"
        print(f"🤖 Running DockerAgent: {' '.join(command)}")
        with log_path.open("w", encoding="utf-8") as log_file:
            result = subprocess.run(
                command,
                cwd=repo_root,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=self._remaining_timeout(start_time),
            )
        print(f"   DockerAgent log: {log_path}")
        return {"returncode": result.returncode, "log_path": str(log_path)}

    def _write_eval_dockerfile(
        self,
        *,
        repo_root: Path,
        dockerfile_path: Path,
        eval_dir: Path,
    ) -> Path:
        sys.path.insert(0, str(repo_root))
        from run_repo2run_benchmark import (
            normalize_eval_dockerfile_for_replay,
            render_eval_dockerfile,
        )

        raw = dockerfile_path.read_text(encoding="utf-8")
        rendered = render_eval_dockerfile(raw)
        normalized = normalize_eval_dockerfile_for_replay(rendered)
        eval_dockerfile = eval_dir / "Dockerfile"
        eval_dockerfile.write_text(normalized, encoding="utf-8")
        return eval_dockerfile

    def _build_image(
        self,
        *,
        image_name: str,
        dockerfile_path: Path,
        context_dir: Path,
        output_dir: Path,
        start_time: float,
    ) -> None:
        command = [
            "docker",
            "build",
            "--pull=false",
            "-f",
            str(dockerfile_path),
            "-t",
            image_name,
            str(context_dir),
        ]
        print(f"🐳 Building image: {' '.join(command)}")
        env = os.environ.copy()
        env.setdefault("DOCKER_BUILDKIT", "0")
        log_path = output_dir / "docker_build.log"
        result_path = output_dir / "docker_build_result.json"
        try:
            result = subprocess.run(
                command,
                check=False,
                timeout=self._remaining_timeout(start_time),
                env=env,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            _write_docker_build_artifacts(
                log_path=log_path,
                result_path=result_path,
                command=command,
                returncode=None,
                stdout=_decode_stream(exc.output),
                stderr=_decode_stream(exc.stderr),
                timed_out=True,
            )
            raise

        _write_docker_build_artifacts(
            log_path=log_path,
            result_path=result_path,
            command=command,
            returncode=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            timed_out=False,
        )
        print(f"   Docker build log: {log_path}")
        if result.returncode != 0:
            raise RuntimeError(f"docker build exited with {result.returncode}; see {log_path}")

    def _run_rat_pytest_tools(
        self,
        *,
        repo_root: Path,
        image_name: str,
        container_name: str,
        output_dir: Path,
        workdir: str,
        start_time: float,
    ) -> None:
        rat_root = Path(os.environ.get("RAT_ROOT") or repo_root / "runanything" / "src")
        run_pytest_tool = rat_root / "libkit" / "tools" / "run_pytest.py"
        run_collect_tool = rat_root / "libkit" / "tools" / "run_pytest_collect.py"
        if not run_pytest_tool.exists():
            raise FileNotFoundError(f"run_pytest.py not found: {run_pytest_tool}")
        if not run_collect_tool.exists():
            raise FileNotFoundError(f"run_pytest_collect.py not found: {run_collect_tool}")

        subprocess.run(["docker", "rm", "-f", container_name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run_command = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-w",
            workdir,
            "-v",
            f"{run_pytest_tool}:/run_pytest.py",
            "-v",
            f"{run_collect_tool}:/run_pytest_collect.py",
            image_name,
            "tail",
            "-f",
            "/dev/null",
        ]
        print(f"🧪 Starting test container: {' '.join(run_command)}")
        subprocess.run(run_command, check=True, timeout=self._remaining_timeout(start_time))
        subprocess.run(["docker", "exec", container_name, "mkdir", "-p", f"{workdir}/logs"], check=True)

        self._docker_exec_tool(
            container_name=container_name,
            tool="/run_pytest_collect.py",
            start_time=start_time,
        )
        self._copy_or_write_fallback(
            container_name=container_name,
            container_path=f"{workdir}/logs/run_pytest_collect_results.json",
            host_path=output_dir / "run_pytest_collect_results.json",
            fallback={"success": False, "returncode": -1, "errors": ["missing collect result"], "raw_output": ""},
        )

        self._docker_exec_tool(
            container_name=container_name,
            tool="/run_pytest.py",
            start_time=start_time,
        )
        self._copy_or_write_fallback(
            container_name=container_name,
            container_path=f"{workdir}/logs/run_pytest_results.json",
            host_path=output_dir / "run_pytest_results.json",
            fallback={
                "summary": {
                    "total_tests": 0,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "errors": 0,
                },
                "error_breakdown": {"MissingResult": 1},
                "failed_tests": [],
                "error_tests": [],
                "returncode": -1,
            },
        )

    def _docker_exec_tool(self, *, container_name: str, tool: str, start_time: float) -> None:
        command = ["docker", "exec", container_name, "python3", tool]
        print(f"   Running: {' '.join(command)}")
        subprocess.run(command, check=False, timeout=self._remaining_timeout(start_time))

    def _copy_or_write_fallback(
        self,
        *,
        container_name: str,
        container_path: str,
        host_path: Path,
        fallback: dict,
    ) -> None:
        host_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["docker", "cp", f"{container_name}:{container_path}", str(host_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            host_path.write_text(json.dumps(fallback, indent=2), encoding="utf-8")

    def _read_head_sha(self, workplace: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=workplace,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def _error_result(self, full_name: str, reason: str, message: str, status: str = "error") -> dict:
        return {
            "status": status,
            "root_path": self.root_path,
            "full_name": full_name,
            "requested_model": "dockeragent",
            "failure_reason": reason,
            "error": message,
        }


def _sanitize_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _validate_agent_run_summary(run_summary: dict, summary_path: Path) -> str | None:
    if not run_summary:
        return f"agent_run_summary missing or unreadable: {summary_path}"

    verification_bundle = run_summary.get("verification_bundle")
    if not isinstance(verification_bundle, dict) or not verification_bundle:
        return "agent_run_summary missing verification_bundle"

    return None


def _agent_run_summary_warning(run_summary: dict) -> str | None:
    if run_summary.get("configuration_success") is not True:
        return "agent_run_summary configuration_success is not true"
    return None


def _decode_stream(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _write_docker_build_artifacts(
    *,
    log_path: Path,
    result_path: Path,
    command: list[str],
    returncode: int | None,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "command": command,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
    }
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log_path.write_text(
        "$ " + " ".join(command) + "\n"
        f"returncode: {returncode}\n"
        f"timed_out: {timed_out}\n\n"
        "##### STDOUT #####\n"
        + (stdout or "")
        + "\n\n##### STDERR #####\n"
        + (stderr or ""),
        encoding="utf-8",
    )


def _infer_workdir(repo_root: Path, dockerfile_path: Path) -> str:
    try:
        sys.path.insert(0, str(repo_root))
        from run_repo2run_benchmark import infer_workdir_from_dockerfile

        return infer_workdir_from_dockerfile(dockerfile_path.read_text(encoding="utf-8"))
    except Exception:
        return "/app"
