#!/usr/bin/env python3
"""Run the standalone Repo2Run benchmark against this project without Multi-Docker-Eval."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.constants import DEFAULT_LLM_MODEL, DEFAULT_MEMORY_EMBEDDING_MODEL
from src.repo2run_dataset import load_repo2run_dataset
from src.synthesizer import Synthesizer


DOCKER_TIMEOUT_EXIT_CODE = 124
TEST_SIGNAL_DETECTOR = Synthesizer()
TEST_EXECUTION_SHELL_WRAPPER = (
    "if command -v bash >/dev/null 2>&1; then exec bash -s; else exec sh -s; fi"
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sanitize_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_executable_path(raw_value: str) -> str:
    candidate = Path(raw_value)
    if candidate.is_absolute() or "/" in raw_value:
        return str(candidate.resolve())
    resolved = shutil.which(raw_value)
    return resolved or raw_value


def normalize_command_list(commands: Any) -> list[str]:
    if isinstance(commands, str):
        commands = [commands]
    normalized: list[str] = []
    for command in commands or []:
        text = str(command or "").strip()
        if text:
            normalized.append(text)
    return normalized


def run_command(
    command: list[str],
    cwd: Path,
    env: Optional[dict[str, str]] = None,
    input_text: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> dict[str, Any]:
    started_at = datetime.now().astimezone()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = DOCKER_TIMEOUT_EXIT_CODE
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        timed_out = True

    finished_at = datetime.now().astimezone()
    return {
        "command": command,
        "command_shell": shlex.join(command),
        "cwd": str(cwd),
        "returncode": returncode,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
    }


def infer_workdir_from_dockerfile(dockerfile_text: str) -> str:
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("WORKDIR "):
            return stripped.split(None, 1)[1].strip()
    return "/app"


def render_eval_dockerfile(agent_dockerfile_text: str) -> str:
    lines = agent_dockerfile_text.splitlines()
    rendered: list[str] = []
    inserted_copy = False
    workdir = infer_workdir_from_dockerfile(agent_dockerfile_text)
    already_copies_context = any(
        line.strip().upper().startswith("COPY ") and "." in line.split()
        for line in lines
    )

    for index, line in enumerate(lines):
        rendered.append(line)
        stripped = line.strip()
        if (
            not inserted_copy
            and not already_copies_context
            and stripped.upper().startswith("WORKDIR ")
        ):
            rendered.append(f"COPY . {workdir}")
            next_line_is_blank = index + 1 < len(lines) and lines[index + 1].strip() == ""
            if not next_line_is_blank:
                rendered.append("")
            inserted_copy = True

    if not inserted_copy and not already_copies_context:
        if rendered and rendered[-1] != "":
            rendered.append("")
        rendered.extend(
            [
                f"WORKDIR {workdir}",
                f"COPY . {workdir}",
            ]
        )

    return "\n".join(rendered).rstrip() + "\n"


def derive_verification_commands(run_summary: Optional[dict[str, Any]]) -> tuple[list[str], list[str], str]:
    summary = run_summary or {}
    bundle = summary.get("verification_bundle") or {}

    runtime_commands = normalize_command_list(bundle.get("runtime_preparation_commands"))
    test_commands = normalize_command_list(bundle.get("test_commands"))
    source = "verification_bundle"

    if not runtime_commands:
        runtime_commands = normalize_command_list(summary.get("verified_runtime_preparation_commands"))
        if runtime_commands:
            source = "verified_runtime_preparation_commands"
    if not test_commands:
        test_commands = normalize_command_list(summary.get("verified_test_commands"))
        if test_commands:
            source = "verified_test_commands"
    if not test_commands:
        fallback = str(summary.get("verified_test_command") or "").strip()
        if fallback:
            test_commands = [fallback]
            source = "verified_test_command"
    if not test_commands:
        test_commands = ["pytest"]
        source = "default_pytest"

    return runtime_commands, test_commands, source


def build_test_execution_script(workdir: str, runtime_commands: list[str], test_command: str) -> str:
    lines = [
        "set -e",
        f"cd {shlex.quote(workdir)}",
    ]
    lines.extend(runtime_commands)
    lines.extend(
        [
            f"cd {shlex.quote(workdir)}",
            "set +e",
            test_command,
            "TEST_EXIT_CODE=$?",
            "set -e",
            'printf "\\n__REPO2RUN_TEST_EXIT_CODE__=%s\\n" "$TEST_EXIT_CODE"',
            'exit "$TEST_EXIT_CODE"',
        ]
    )
    return "\n".join(lines) + "\n"


def discover_internal_import_prefixes(workspace_root: Path) -> set[str]:
    prefixes = {"src", "tests"}
    for candidate_root in (workspace_root, workspace_root / "src"):
        if not candidate_root.is_dir():
            continue
        for child in candidate_root.iterdir():
            if child.name.startswith(".") or not child.is_dir():
                continue
            if (child / "__init__.py").exists():
                prefixes.add(child.name)
    return prefixes


def output_has_collection_error_signal(observation: str) -> bool:
    normalized = str(observation or "")
    patterns = [
        r"ERROR collecting",
        r"ImportError while importing test module",
    ]
    return any(re.search(pattern, normalized, re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def output_has_invocation_error_signal(observation: str) -> bool:
    normalized = str(observation or "")
    patterns = [
        r"found no collectors for",
        r"pytest: error:",
        r"unrecognized arguments:",
        r"usage: pytest",
    ]
    return any(re.search(pattern, normalized, re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def output_has_internal_repo_import_error_signal(
    observation: str,
    internal_import_prefixes: Optional[set[str]] = None,
) -> bool:
    normalized = str(observation or "")
    prefixes = set(internal_import_prefixes or {"src", "tests"})

    missing_module_match = re.search(
        r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]",
        normalized,
        re.IGNORECASE | re.MULTILINE,
    )
    if missing_module_match:
        missing_module = missing_module_match.group(1)
        if missing_module.split(".", 1)[0] in prefixes:
            return True

    import_from_match = re.search(
        r"ImportError:\s+cannot import name .* from ['\"][^'\"]+['\"] \((/app/[^)]+)\)",
        normalized,
        re.IGNORECASE | re.MULTILINE,
    )
    return bool(import_from_match)


def classify_test_execution(
    command_result: dict[str, Any],
    internal_import_prefixes: Optional[set[str]] = None,
) -> dict[str, Any]:
    output = f"{command_result.get('stdout') or ''}\n{command_result.get('stderr') or ''}".strip()
    effective_signal = TEST_SIGNAL_DETECTOR.observation_has_effective_test_signal(output)
    empty_signal = TEST_SIGNAL_DETECTOR.observation_has_empty_test_run_signal(output)
    help_signal = TEST_SIGNAL_DETECTOR.observation_looks_like_help_text(output)
    failure_signal = TEST_SIGNAL_DETECTOR.observation_has_test_failure_signal(output)
    invocation_error_signal = output_has_invocation_error_signal(output)
    collection_error_signal = output_has_collection_error_signal(output)
    internal_repo_import_error_signal = output_has_internal_repo_import_error_signal(
        output,
        internal_import_prefixes=internal_import_prefixes,
    )

    effective = False
    reason = "tests_did_not_execute"

    if command_result.get("timed_out"):
        reason = "timed_out"
    elif help_signal:
        reason = "help_output"
    elif invocation_error_signal:
        reason = "invocation_error"
    elif command_result.get("returncode") == 0 and effective_signal:
        effective = True
        reason = "tests_executed_successfully"
    elif collection_error_signal and internal_repo_import_error_signal:
        effective = True
        reason = "tests_executed_with_collection_failures"
    elif effective_signal and failure_signal and not collection_error_signal:
        effective = True
        reason = "tests_executed_with_failures"
    elif empty_signal:
        reason = "empty_test_run"
    elif collection_error_signal:
        reason = "collection_or_env_error"
    elif effective_signal:
        reason = "effective_signal_without_supported_exit_pattern"

    return {
        "effective": effective,
        "reason": reason,
        "effective_signal": effective_signal,
        "failure_signal": failure_signal,
        "empty_signal": empty_signal,
        "help_signal": help_signal,
        "invocation_error_signal": invocation_error_signal,
        "collection_error_signal": collection_error_signal,
        "internal_repo_import_error_signal": internal_repo_import_error_signal,
    }


def compute_paper_alignment(expected_success: bool, observed_success: bool) -> str:
    if observed_success and expected_success:
        return "matched_success"
    if (not observed_success) and (not expected_success):
        return "matched_failure"
    if observed_success and not expected_success:
        return "unexpected_success"
    return "unexpected_failure"


def compute_execution_status(
    agent_run: dict[str, Any],
    dockerfile_present: bool,
    docker_build_success: bool,
    environment_build_success: bool,
) -> str:
    if environment_build_success:
        return "environment_built"
    if not dockerfile_present:
        return "dockerfile_missing"
    if not docker_build_success:
        return "docker_build_failed"
    if agent_run.get("returncode") != 0:
        return "agent_command_failed"
    return "test_execution_failed"


def build_docker_image_tag(instance_id: str) -> str:
    return f"jayint-repo2run-{sanitize_name(instance_id).lower()}"


def remove_docker_image(image_tag: str, cwd: Path) -> dict[str, Any]:
    return run_command(
        ["docker", "image", "rm", "-f", image_tag],
        cwd=cwd,
    )


def evaluate_built_image(
    image_tag: str,
    workdir: str,
    runtime_commands: list[str],
    test_commands: list[str],
    cwd: Path,
    timeout_seconds: int,
    workspace_root: Optional[Path] = None,
) -> dict[str, Any]:
    command_results: list[dict[str, Any]] = []
    internal_import_prefixes = (
        discover_internal_import_prefixes(workspace_root) if workspace_root else None
    )

    for test_command in test_commands:
        script = build_test_execution_script(workdir, runtime_commands, test_command)
        execution = run_command(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                image_tag,
                "sh",
                "-lc",
                TEST_EXECUTION_SHELL_WRAPPER,
            ],
            cwd=cwd,
            input_text=script,
            timeout_seconds=timeout_seconds,
        )
        classification = classify_test_execution(
            execution,
            internal_import_prefixes=internal_import_prefixes,
        )
        command_results.append(
            {
                "test_command": test_command,
                "runtime_preparation_commands": runtime_commands,
                "script": script,
                "execution": execution,
                "classification": classification,
            }
        )

    effective_count = sum(
        1 for item in command_results if item["classification"]["effective"]
    )
    all_effective = bool(test_commands) and effective_count == len(test_commands)
    return {
        "workdir": workdir,
        "runtime_preparation_commands": runtime_commands,
        "test_commands": test_commands,
        "results": command_results,
        "effective_test_command_count": effective_count,
        "all_test_commands_effective": all_effective,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Repo2Run Table 15 benchmark with this project's native agent."
    )
    parser.add_argument(
        "--dataset",
        default="datasets/repo2run_table15.json",
        help="Standalone Repo2Run dataset JSON path. Defaults to datasets/repo2run_table15.json.",
    )
    parser.add_argument(
        "--output-root",
        default="outputs/repo2run_benchmark",
        help="Directory where per-instance results and summary JSON will be written.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to invoke agent.py. Defaults to the current interpreter.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_LLM_MODEL,
        help="Model forwarded to agent.py.",
    )
    parser.add_argument(
        "--base-image",
        default="auto",
        help="Base image forwarded to agent.py.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=100,
        help="Maximum agent steps per repository. Defaults to 100.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only run the first N instances after filtering.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N instances after filtering.",
    )
    parser.add_argument(
        "--instance-regex",
        default=None,
        help="Only run instances whose instance_id or full_name matches this regex.",
    )
    parser.add_argument(
        "--only-paper-success",
        action="store_true",
        help="Only run instances marked Yes in Table 15.",
    )
    parser.add_argument(
        "--docker-build-timeout",
        type=int,
        default=1800,
        help="Timeout in seconds for a single docker build. Defaults to 1800.",
    )
    parser.add_argument(
        "--test-timeout",
        type=int,
        default=1800,
        help="Timeout in seconds for a single test command execution. Defaults to 1800.",
    )
    parser.add_argument(
        "--keep-docker-artifacts",
        action="store_true",
        help="Keep built docker images for inspection instead of removing them after evaluation.",
    )
    parser.set_defaults(enable_observation_compression=True)
    parser.add_argument(
        "--enable-observation-compression",
        action="store_true",
        dest="enable_observation_compression",
        help="Enable AgentDiet-style observation compression during benchmark runs (default: enabled).",
    )
    parser.add_argument(
        "--disable-observation-compression",
        action="store_false",
        dest="enable_observation_compression",
        help="Disable AgentDiet-style observation compression during benchmark runs.",
    )
    parser.add_argument(
        "--enable-long-term-memory",
        action="store_true",
        help="Forward --enable-long-term-memory to agent.py.",
    )
    parser.add_argument(
        "--memory-path",
        default=None,
        help="Optional JSONL long-term memory path forwarded to agent.py.",
    )
    parser.add_argument(
        "--memory-embedding-model",
        default=DEFAULT_MEMORY_EMBEDDING_MODEL,
        help="Embedding model forwarded to agent.py when long-term memory is enabled.",
    )
    parser.add_argument(
        "--keep-container",
        action="store_true",
        help="Forward --keep-container to agent.py.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    dataset_path = (repo_root / args.dataset).resolve()
    output_root = (repo_root / args.output_root).resolve()
    results_dir = output_root / "results"
    workplaces_dir = output_root / "workplaces"
    eval_artifacts_dir = output_root / "eval_artifacts"

    dataset = load_repo2run_dataset(dataset_path)
    instances = list(dataset["instances"])

    if args.only_paper_success:
        instances = [instance for instance in instances if instance.get("paper_build_success")]

    if args.instance_regex:
        matcher = re.compile(args.instance_regex)
        instances = [
            instance
            for instance in instances
            if matcher.search(instance.get("instance_id", "")) or matcher.search(instance.get("full_name", ""))
        ]

    if args.offset:
        instances = instances[args.offset :]
    if args.limit is not None:
        instances = instances[: args.limit]

    output_root.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    workplaces_dir.mkdir(parents=True, exist_ok=True)
    eval_artifacts_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running Repo2Run dataset: {dataset_path}")
    print(f"Selected instances: {len(instances)}")

    python_executable = resolve_executable_path(args.python)
    per_instance_results: list[dict[str, Any]] = []
    execution_status_counter: Counter[str] = Counter()
    paper_alignment_counter: Counter[str] = Counter()

    for position, instance in enumerate(instances, start=1):
        instance_id = instance["instance_id"]
        safe_instance_id = sanitize_name(instance_id)
        workplace = workplaces_dir / safe_instance_id
        result_path = results_dir / f"{safe_instance_id}.json"
        artifact_dir = eval_artifacts_dir / safe_instance_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        print(f"[{position}/{len(instances)}] {instance['full_name']} @ {instance['sha']}")

        agent_command = [
            python_executable,
            str(repo_root / "agent.py"),
            instance["repo_url"],
            "--base-commit",
            instance["base_commit"],
            "--image",
            args.base_image,
            "--model",
            args.model,
            "--steps",
            str(args.max_steps),
            "--workplace",
            str(workplace),
        ]

        if args.enable_observation_compression:
            agent_command.append("--enable-observation-compression")
        if args.enable_long_term_memory:
            agent_command.append("--enable-long-term-memory")
            agent_command.extend(["--memory-embedding-model", args.memory_embedding_model])
            if args.memory_path:
                agent_command.extend(["--memory-path", args.memory_path])
        if args.keep_container:
            agent_command.append("--keep-container")

        agent_run = run_command(agent_command, cwd=repo_root, env=os.environ.copy())
        run_summary_path = workplace / "agent_run_summary.json"
        run_summary = load_json(run_summary_path)
        agent_dockerfile_path = workplace / "Dockerfile"
        agent_dockerfile_text = (
            agent_dockerfile_path.read_text(encoding="utf-8")
            if agent_dockerfile_path.exists()
            else None
        )

        eval_dockerfile_path = artifact_dir / "Dockerfile.eval"
        docker_build = None
        test_execution = None
        docker_cleanup = None
        workdir = "/app"
        verification_source = None
        runtime_commands: list[str] = []
        test_commands: list[str] = []
        image_tag = build_docker_image_tag(instance_id)

        if agent_dockerfile_text:
            workdir = infer_workdir_from_dockerfile(agent_dockerfile_text)
            eval_dockerfile_text = render_eval_dockerfile(agent_dockerfile_text)
            eval_dockerfile_path.write_text(eval_dockerfile_text, encoding="utf-8")

            docker_build = run_command(
                [
                    "docker",
                    "build",
                    "-f",
                    str(eval_dockerfile_path),
                    "-t",
                    image_tag,
                    str(workplace),
                ],
                cwd=repo_root,
                env=os.environ.copy(),
                timeout_seconds=args.docker_build_timeout,
            )

            if docker_build["returncode"] == 0 and not docker_build.get("timed_out"):
                runtime_commands, test_commands, verification_source = derive_verification_commands(
                    run_summary
                )
                test_execution = evaluate_built_image(
                    image_tag=image_tag,
                    workdir=workdir,
                    runtime_commands=runtime_commands,
                    test_commands=test_commands,
                    cwd=repo_root,
                    timeout_seconds=args.test_timeout,
                    workspace_root=workplace,
                )
            if not args.keep_docker_artifacts:
                docker_cleanup = remove_docker_image(image_tag, cwd=repo_root)

        dockerfile_generation_success = bool(
            docker_build and docker_build["returncode"] == 0 and not docker_build.get("timed_out")
        )
        environment_build_success = bool(
            dockerfile_generation_success
            and test_execution
            and test_execution["all_test_commands_effective"]
        )
        paper_alignment = compute_paper_alignment(
            expected_success=bool(instance.get("paper_build_success")),
            observed_success=environment_build_success,
        )
        execution_status = compute_execution_status(
            agent_run=agent_run,
            dockerfile_present=agent_dockerfile_text is not None,
            docker_build_success=dockerfile_generation_success,
            environment_build_success=environment_build_success,
        )
        execution_status_counter[execution_status] += 1
        paper_alignment_counter[paper_alignment] += 1

        payload = {
            "dataset_entry": instance,
            "agent_run": agent_run,
            "run_summary_path": str(run_summary_path),
            "run_summary": run_summary,
            "agent_claimed_success": bool((run_summary or {}).get("configuration_success")),
            "agent_dockerfile_path": str(agent_dockerfile_path),
            "agent_dockerfile_present": agent_dockerfile_text is not None,
            "eval_dockerfile_path": str(eval_dockerfile_path),
            "eval_workdir": workdir,
            "verification_command_source": verification_source,
            "runtime_preparation_commands": runtime_commands,
            "test_commands": test_commands,
            "docker_build": docker_build,
            "test_execution": test_execution,
            "docker_cleanup": docker_cleanup,
            "dockerfile_generation_success": dockerfile_generation_success,
            "environment_build_success": environment_build_success,
            "paper_build_success": bool(instance.get("paper_build_success")),
            "paper_alignment": paper_alignment,
            "execution_status": execution_status,
        }
        write_json(result_path, payload)
        per_instance_results.append(payload)

    dgsr_successes = sum(
        1 for item in per_instance_results if item["dockerfile_generation_success"]
    )
    ebsr_successes = sum(
        1 for item in per_instance_results if item["environment_build_success"]
    )

    summary = {
        "benchmark_name": dataset.get("benchmark_name", "Repo2Run Table 15"),
        "dataset_path": str(dataset_path),
        "output_root": str(output_root),
        "selected_instances": len(instances),
        "metrics": {
            "DGSR": {
                "success_count": dgsr_successes,
                "total": len(instances),
                "rate": round(dgsr_successes / len(instances), 4) if instances else 0.0,
            },
            "EBSR": {
                "success_count": ebsr_successes,
                "total": len(instances),
                "rate": round(ebsr_successes / len(instances), 4) if instances else 0.0,
            },
        },
        "execution_status_counts": dict(sorted(execution_status_counter.items())),
        "paper_alignment_counts": dict(sorted(paper_alignment_counter.items())),
        "matched_against_paper": sum(
            1 for item in per_instance_results if item["paper_alignment"] in {"matched_success", "matched_failure"}
        ),
        "paper_success_count": sum(
            1 for instance in instances if instance.get("paper_build_success")
        ),
        "paper_failure_count": sum(
            1 for instance in instances if not instance.get("paper_build_success")
        ),
        "results": [
            {
                "instance_id": item["dataset_entry"]["instance_id"],
                "full_name": item["dataset_entry"]["full_name"],
                "sha": item["dataset_entry"]["sha"],
                "execution_status": item["execution_status"],
                "paper_alignment": item["paper_alignment"],
                "dockerfile_generation_success": item["dockerfile_generation_success"],
                "environment_build_success": item["environment_build_success"],
                "paper_build_success": item["paper_build_success"],
                "result_json": str(results_dir / f"{sanitize_name(item['dataset_entry']['instance_id'])}.json"),
            }
            for item in per_instance_results
        ],
    }
    write_json(output_root / "summary.json", summary)

    print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2))
    print(json.dumps(summary["paper_alignment_counts"], ensure_ascii=False, indent=2))
    print(f"Summary written to {output_root / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
