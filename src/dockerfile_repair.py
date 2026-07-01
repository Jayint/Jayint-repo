"""Dockerfile replay repair utilities.

This module promotes the bounded Dockerfile repair agent out of the benchmark
runner so the main project can reuse it after producing a Dockerfile.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

from src.evaluation_target import is_ratbench_target, normalize_evaluation_target
from src.synthesizer import Synthesizer, build_resilient_pip_install_run_instruction


DOCKERFILE_REPAIR_LOG_LIMIT = 12000
DOCKER_COMMAND_TIMEOUT_EXIT_CODE = 124
TEST_EXECUTION_SHELL_WRAPPER = (
    "if command -v bash >/dev/null 2>&1; then exec bash -s; else exec sh -s; fi"
)

DOCKERFILE_REPAIR_SYSTEM_PROMPT = """You are a bounded Dockerfile repair agent.

You receive a Dockerfile that was generated from a successful sandbox setup trajectory, plus the fresh Docker build/test failure feedback.
Your job is to repair only the Dockerfile so the fresh image can reproduce the sandbox setup and run the provided test command.

Rules:
1. Output JSON only with keys: dockerfile, rationale, confidence.
2. `dockerfile` must be the full replacement Dockerfile text, not a patch.
3. Do not modify target repository source code outside Dockerfile commands.
4. Do not invent a new setup strategy unless the trajectory evidence is insufficient.
5. Prefer restoring omitted successful setup commands from agent_run_summary in the original trajectory order.
6. Preserve command order. Do not merge, sort, hoist, or rewrite successful setup commands for convenience.
7. Fix replay gaps such as missing installs, lost ENV/WORKDIR/SHELL context, build/runtime split mistakes, or Dockerfile syntax errors.
8. Do not remove an existing Dockerfile RUN command unless the logs clearly prove it is wrong or duplicate.
9. Keep the existing base image and repository copy semantics unless the failure directly requires a change.
10. Do not emit raw multi-line RUN commands. Multi-line shell/Python/file-write content must be encoded into a single valid RUN instruction or otherwise rendered with Dockerfile-safe syntax.
11. Treat `agent_run_summary.build_recipe.build_commands` as the authoritative replay order. If a successful command edited files, created symlinks, installed packages, or patched stubs, preserve that exact command text unless Dockerfile syntax alone forces escaping.
12. Do not replace an observed successful file patch or stub with your own equivalent implementation. The goal is reproduction of the sandbox trajectory, not a cleaner independent solution.
13. Do not try to fix a test-command runtime wrapper by adding a final Dockerfile `RUN` test. If the provided test command uses a wrapper such as `xvfb-run`, preserve the test command outside the Dockerfile.
14. If the evaluation target is RATBench, full pytest may return nonzero after executing tests; repair only Dockerfile/environment replay gaps, not project logic or test assertion failures.

`confidence` must be one of: "high", "medium", "low".
"""

DOCKERFILE_REPAIR_USER_PROMPT = """Repair the Dockerfile using the failure feedback and trajectory evidence.

Input JSON:
```json
{repair_input_json}
```
"""

_MISSING_PYTHON_MODULE_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s+No module named ['\"](?P<module>[^'\"]+)['\"]"
)
_KNOWN_MISSING_MODULE_PACKAGE_FALLBACKS = {
    "ppocr": ("paddleocr", "paddleocr==2.7.3"),
    "ppstructure": ("paddleocr", "paddleocr==2.7.3"),
}
_PIP_INSTALL_OPTION_VALUE_FLAGS = {
    "-c",
    "--constraint",
    "-i",
    "--index-url",
    "--extra-index-url",
    "-f",
    "--find-links",
    "--trusted-host",
    "--platform",
    "--python-version",
    "--implementation",
    "--abi",
    "--root",
    "--prefix",
    "--target",
    "--src",
    "--upgrade-strategy",
    "--config-settings",
    "-C",
    "--global-option",
    "--compile-option",
}


def _decode_command_stream(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _normalize_pip_constraint_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "").strip()).lower()


def _pip_requirement_name(requirement: str) -> str:
    token = str(requirement or "").strip()
    if not token or token.startswith(("-", ".", "/", "git+", "http://", "https://")):
        return ""
    name = re.split(r"\[|==|!=|~=|>=|<=|>|<|@", token, maxsplit=1)[0].strip()
    return name.lower().replace("_", "-")


def _split_pip_install_command(command: str) -> tuple[list[str], list[str], list[str]] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None

    try:
        install_index = tokens.index("install")
    except ValueError:
        return None

    prefix = tokens[: install_index + 1]
    tail = tokens[install_index + 1 :]
    options: list[str] = []
    requirements: list[str] = []
    index = 0
    while index < len(tail):
        token = tail[index]
        if token == "--":
            requirements.extend(tail[index + 1 :])
            break
        if token.startswith("-"):
            options.append(token)
            option_name = token.split("=", 1)[0]
            if option_name in _PIP_INSTALL_OPTION_VALUE_FLAGS and "=" not in token and index + 1 < len(tail):
                index += 1
                options.append(tail[index])
            index += 1
            continue
        requirements.append(token)
        index += 1

    return prefix, options, requirements


def _is_bare_pip_install_command(command: str) -> bool:
    parsed = _split_pip_install_command(command)
    if not parsed:
        return False
    prefix, _, _ = parsed
    if len(prefix) < 2:
        return False
    if prefix[-1] != "install":
        return False
    executable = prefix[-2].rsplit("/", 1)[-1]
    if executable in {"pip", "pip3"}:
        return True
    if len(prefix) < 4:
        return False
    python_executable = prefix[-4].rsplit("/", 1)[-1]
    return python_executable in {"python", "python3"} and prefix[-3:] == ["-m", "pip", "install"]


def _extract_generated_pip_retry_inner_command(command: str) -> str | None:
    normalized = str(command or "")
    if "JAYINT_PIP_ATTEMPT" not in normalized or "/bin/sh" not in normalized:
        return None
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        return None
    for index, token in enumerate(tokens[:-1]):
        if token == "-lc" and _is_bare_pip_install_command(tokens[index + 1]):
            return tokens[index + 1]
    return None


def extract_missing_python_modules_from_test_execution(
    test_execution: Optional[dict[str, Any]],
) -> list[str]:
    modules: list[str] = []
    seen: set[str] = set()
    for item in (test_execution or {}).get("results") or []:
        execution = item.get("execution") or {}
        combined_output = "\n".join(
            [
                _decode_command_stream(execution.get("stdout")),
                _decode_command_stream(execution.get("stderr")),
            ]
        )
        for match in _MISSING_PYTHON_MODULE_RE.finditer(combined_output):
            module = (match.group("module") or "").split(".", 1)[0].strip()
            if module and module not in seen:
                seen.add(module)
                modules.append(module)
    return modules


def _strip_requirement_line(line: str) -> str:
    stripped = (line or "").strip()
    if not stripped or stripped.startswith("#"):
        return ""
    if " #" in stripped:
        stripped = stripped.split(" #", 1)[0].strip()
    return stripped


def _find_declared_requirement_in_workspace(
    workspace_root: Optional[Path],
    package_name: str,
) -> str | None:
    normalized_name = _normalize_pip_constraint_name(package_name)
    if not workspace_root or not normalized_name or not workspace_root.exists():
        return None

    inspected = 0
    for path in sorted(workspace_root.rglob("*requirements*.txt")):
        inspected += 1
        if inspected > 100:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            requirement = _strip_requirement_line(line)
            if _pip_requirement_name(requirement) == normalized_name:
                return requirement

    lock_inspected = 0
    package_pattern = re.compile(
        r"(?ms)^\[\[package\]\]\s*.*?^name\s*=\s*"
        + re.escape(json.dumps(package_name)[1:-1]).join(['"', '"'])
        + r"\s*$.*?^version\s*=\s*\"(?P<version>[^\"]+)\"",
    )
    for path in sorted(workspace_root.rglob("poetry.lock")):
        lock_inspected += 1
        if lock_inspected > 20:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = package_pattern.search(text)
        if match:
            return f"{package_name}=={match.group('version')}"
    return None


def _requirement_for_missing_module(
    module: str,
    workspace_root: Optional[Path],
) -> str | None:
    module_name = _normalize_pip_constraint_name((module or "").split(".", 1)[0])
    if not module_name:
        return None

    candidate_package_names: list[str] = []
    fallback_requirement = module_name
    if module_name in _KNOWN_MISSING_MODULE_PACKAGE_FALLBACKS:
        package_name, fallback_requirement = _KNOWN_MISSING_MODULE_PACKAGE_FALLBACKS[module_name]
        candidate_package_names.append(package_name)
    candidate_package_names.append(module_name)

    for package_name in candidate_package_names:
        declared = _find_declared_requirement_in_workspace(workspace_root, package_name)
        if declared:
            return declared
    return fallback_requirement


def _preferred_pip_invocation_for_dockerfile(dockerfile_text: str) -> str:
    text = dockerfile_text or ""
    if re.search(r"\bpython3\s+-m\s+pip\s+install\b", text):
        return "python3 -m pip"
    if re.search(r"\bpip3\s+install\b", text):
        return "pip3"
    return "pip"


def _dockerfile_already_installs_requirement(dockerfile_text: str, requirement: str) -> bool:
    requirement_name = _pip_requirement_name(requirement)
    if not requirement_name:
        return True
    requires_exact = bool(re.search(r"(?:===|==|~=|!=|>=|<=|>|<)", requirement))
    normalized_requirement = re.sub(r"\s+", "", requirement).lower().replace("_", "-")
    for line in (dockerfile_text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("RUN "):
            continue
        command = stripped[4:].strip()
        candidate_commands = [command]
        generated_pip_command = _extract_generated_pip_retry_inner_command(command)
        if generated_pip_command:
            candidate_commands.append(generated_pip_command)
        for candidate_command in candidate_commands:
            parsed = _split_pip_install_command(candidate_command)
            if not parsed:
                continue
            _, _, requirements = parsed
            for installed_requirement in requirements:
                if _pip_requirement_name(installed_requirement) != requirement_name:
                    continue
                if not requires_exact:
                    return True
                normalized_installed = (
                    re.sub(r"\s+", "", installed_requirement).lower().replace("_", "-")
                )
                if normalized_installed == normalized_requirement:
                    return True
    return False


def _insert_run_instruction_before_final_command(
    dockerfile_text: str,
    instruction: str,
) -> str:
    lines = (dockerfile_text or "").rstrip().splitlines()
    insert_at = len(lines)
    for index, line in enumerate(lines):
        if line.strip().upper().startswith(("CMD ", "ENTRYPOINT ")):
            insert_at = index
            break
    if insert_at > 0 and lines[insert_at - 1].strip():
        lines.insert(insert_at, "")
        insert_at += 1
    lines.insert(insert_at, instruction)
    return "\n".join(lines).rstrip() + "\n"


def repair_dockerfile_for_missing_python_modules(
    dockerfile_text: str,
    test_execution: Optional[dict[str, Any]],
    workspace_root: Optional[Path],
) -> tuple[str, list[str]]:
    modules = extract_missing_python_modules_from_test_execution(test_execution)
    if not modules:
        return dockerfile_text, []

    requirements: list[str] = []
    seen_requirement_names: set[str] = set()
    for module in modules:
        requirement = _requirement_for_missing_module(module, workspace_root)
        if not requirement or _dockerfile_already_installs_requirement(dockerfile_text, requirement):
            continue
        requirement_name = _pip_requirement_name(requirement)
        if requirement_name in seen_requirement_names:
            continue
        seen_requirement_names.add(requirement_name)
        requirements.append(requirement)

    if not requirements:
        return dockerfile_text, []

    pip_invocation = _preferred_pip_invocation_for_dockerfile(dockerfile_text)
    install_command = f"{pip_invocation} install " + " ".join(shlex.quote(item) for item in requirements)
    instruction = build_resilient_pip_install_run_instruction(install_command)
    return _insert_run_instruction_before_final_command(dockerfile_text, instruction), requirements


def truncate_for_repair_prompt(value: Any, limit: int = DOCKERFILE_REPAIR_LOG_LIMIT) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    head_limit = limit // 2
    tail_limit = limit - head_limit
    return (
        text[:head_limit]
        + "\n\n...[truncated for Dockerfile repair prompt]...\n\n"
        + text[-tail_limit:]
    )


def extract_json_object_candidates(text: str) -> list[str]:
    objects: list[str] = []
    if not text:
        return objects

    search_regions = [
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    ]
    search_regions.append(text.strip())

    for region in search_regions:
        position = 0
        while position < len(region):
            start = region.find("{", position)
            if start == -1:
                break
            depth = 0
            in_string = False
            escape = False
            found = False
            for index in range(start, len(region)):
                char = region[index]
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        objects.append(region[start:index + 1])
                        position = index + 1
                        found = True
                        break
            if not found:
                break
    return objects


def extract_dockerfile_repair_json(content: str) -> dict[str, Any]:
    for candidate in extract_json_object_candidates(content):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        dockerfile = str(parsed.get("dockerfile") or "").strip()
        if dockerfile and re.search(r"(?im)^FROM\s+\S+", dockerfile):
            confidence = str(parsed.get("confidence") or "medium").strip().lower()
            if confidence not in {"high", "medium", "low"}:
                confidence = "medium"
            return {
                "dockerfile": dockerfile.rstrip() + "\n",
                "rationale": str(parsed.get("rationale") or "").strip(),
                "confidence": confidence,
            }
    raise ValueError("Dockerfile repair response did not contain a valid JSON object with a full Dockerfile")


def build_dockerfile_repair_input(
    *,
    instance: dict[str, Any],
    workdir: str,
    dockerfile_text: str,
    run_summary: Optional[dict[str, Any]],
    runtime_commands: list[str],
    test_commands: list[str],
    docker_build: Optional[dict[str, Any]],
    test_execution: Optional[dict[str, Any]],
    evaluation_target: str = "repo2run",
) -> dict[str, Any]:
    test_results = []
    for item in (test_execution or {}).get("results") or []:
        execution = item.get("execution") or {}
        test_results.append(
            {
                "test_command": item.get("test_command"),
                "classification": item.get("classification"),
                "returncode": execution.get("returncode"),
                "timed_out": execution.get("timed_out"),
                "stdout": truncate_for_repair_prompt(execution.get("stdout")),
                "stderr": truncate_for_repair_prompt(execution.get("stderr")),
            }
        )

    minimal_run_summary = {
        "repo_url": (run_summary or {}).get("repo_url"),
        "base_commit": (run_summary or {}).get("base_commit"),
        "language": (run_summary or {}).get("language"),
        "verification_bundle": (run_summary or {}).get("verification_bundle"),
        "evaluation_target": evaluation_target,
        "verified_runtime_preparation_commands": (run_summary or {}).get(
            "verified_runtime_preparation_commands"
        ),
        "verified_test_commands": (run_summary or {}).get("verified_test_commands"),
        "build_recipe": {
            "source": ((run_summary or {}).get("build_recipe") or {}).get("source"),
            "build_commands": ((run_summary or {}).get("build_recipe") or {}).get(
                "build_commands"
            )
            or [],
            "runtime_commands": ((run_summary or {}).get("build_recipe") or {}).get(
                "runtime_commands"
            )
            or [],
        },
        "successful_actions": (run_summary or {}).get("successful_actions") or [],
        "failed_actions": (run_summary or {}).get("failed_actions") or [],
    }

    return {
        "task": {
            "instance_id": instance.get("instance_id"),
            "full_name": instance.get("full_name"),
            "sha": instance.get("sha"),
            "repo_url": instance.get("repo_url"),
            "workdir": workdir,
        },
        "dockerfile": dockerfile_text,
        "runtime_preparation_commands": runtime_commands,
        "test_commands": test_commands,
        "evaluation_target": evaluation_target,
        "agent_run_summary": minimal_run_summary,
        "docker_build": {
            "returncode": (docker_build or {}).get("returncode"),
            "timed_out": (docker_build or {}).get("timed_out"),
            "stdout": truncate_for_repair_prompt((docker_build or {}).get("stdout")),
            "stderr": truncate_for_repair_prompt((docker_build or {}).get("stderr")),
        },
        "test_execution": test_results,
    }


def repair_dockerfile_with_llm(
    *,
    client: Any,
    model: str,
    repair_input: dict[str, Any],
    artifact_dir: Path,
    round_index: int,
) -> dict[str, Any]:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    raw_content = ""
    messages = [
        {"role": "system", "content": DOCKERFILE_REPAIR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": DOCKERFILE_REPAIR_USER_PROMPT.format(
                repair_input_json=json.dumps(repair_input, ensure_ascii=False, indent=2)
            ),
        },
    ]
    repair_log_path = artifact_dir / f"dockerfile_repair_round_{round_index}.md"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    repair_log_path.write_text(
        "##### LLM INPUT (Dockerfile repair) #####\n"
        "================================ Human Message =================================\n\n"
        + "\n\n".join(f"[{message['role'].upper()}]\n{message['content']}" for message in messages)
        + "\n\n",
        encoding="utf-8",
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
        )
        usage = {
            "input_tokens": getattr(response.usage, "prompt_tokens", 0),
            "output_tokens": getattr(response.usage, "completion_tokens", 0),
            "total_tokens": getattr(response.usage, "total_tokens", 0),
        }
        raw_content = response.choices[0].message.content or ""
        parsed = extract_dockerfile_repair_json(raw_content)
        result = {
            "round": round_index,
            "source": "llm",
            "error": None,
            "usage": usage,
            "raw_content": raw_content,
            "dockerfile_text": parsed["dockerfile"],
            "rationale": parsed["rationale"],
            "confidence": parsed["confidence"],
            "log_path": str(repair_log_path),
        }
    except Exception as exc:
        result = {
            "round": round_index,
            "source": "llm_error",
            "error": str(exc),
            "usage": usage,
            "raw_content": raw_content,
            "dockerfile_text": None,
            "rationale": "",
            "confidence": "low",
            "log_path": str(repair_log_path),
        }

    with repair_log_path.open("a", encoding="utf-8") as file_obj:
        file_obj.write("================================ AI Message =================================\n\n")
        file_obj.write(f"{raw_content}\n\n")
        file_obj.write("================================ Parsed Repair =================================\n\n")
        file_obj.write(
            json.dumps(
                {k: v for k, v in result.items() if k != "raw_content"},
                ensure_ascii=False,
                indent=2,
            )
        )
        file_obj.write("\n")
    return result


def _run_docker_command(
    command: list[str],
    *,
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "timed_out": False,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": DOCKER_COMMAND_TIMEOUT_EXIT_CODE,
            "timed_out": True,
            "stdout": _decode_command_stream(exc.stdout),
            "stderr": _decode_command_stream(exc.stderr),
        }
    except Exception as exc:
        return {
            "command": command,
            "returncode": 1,
            "timed_out": False,
            "stdout": "",
            "stderr": str(exc),
        }


def _safe_docker_image_tag(context_dir: Path) -> str:
    raw_name = f"{context_dir.name}-{os.getpid()}".lower()
    safe_name = re.sub(r"[^a-z0-9_.-]+", "-", raw_name).strip(".-")
    return f"jayint-dockerfile-repair-{safe_name or 'workplace'}"


def build_docker_image_for_repair(
    *,
    dockerfile_path: Path,
    context_dir: Path,
    image_tag: str,
    timeout_seconds: int,
    docker_platform: Optional[str] = None,
) -> dict[str, Any]:
    command = ["docker", "build"]
    if docker_platform:
        command.extend(["--platform", docker_platform])
    command.extend(["-f", str(dockerfile_path), "-t", image_tag, str(context_dir)])
    return _run_docker_command(command, cwd=context_dir, timeout_seconds=timeout_seconds)


def _build_repair_test_script(
    *,
    workdir: str,
    runtime_commands: list[str],
    test_command: str,
) -> str:
    lines = ["set -e"]
    if workdir:
        lines.append(f"cd {shlex.quote(workdir)}")
    lines.extend(command for command in runtime_commands if command)
    lines.append(test_command)
    return "\n".join(lines).rstrip() + "\n"


def _classify_repair_test_execution(
    execution: dict[str, Any],
    *,
    evaluation_target: str,
) -> dict[str, Any]:
    if execution.get("timed_out"):
        return {"effective": False, "reason": "timeout"}
    if execution.get("returncode") == 0:
        return {"effective": True, "reason": "exit_zero"}
    if is_ratbench_target(evaluation_target):
        output = "\n".join(
            str(execution.get(key) or "") for key in ("stdout", "stderr")
        )
        detector = Synthesizer()
        if detector.observation_has_effective_test_signal(output):
            return {"effective": True, "reason": "ratbench_test_execution_with_failures"}
    return {"effective": False, "reason": "nonzero_exit"}


def evaluate_dockerfile_image_for_repair(
    *,
    image_tag: str,
    workdir: str,
    runtime_commands: list[str],
    test_commands: list[str],
    cwd: Path,
    timeout_seconds: int,
    docker_platform: Optional[str] = None,
    evaluation_target: str = "repo2run",
) -> dict[str, Any]:
    command_results: list[dict[str, Any]] = []
    for test_command in test_commands:
        script = _build_repair_test_script(
            workdir=workdir,
            runtime_commands=runtime_commands,
            test_command=test_command,
        )
        docker_run_command = ["docker", "run", "--rm", "-i"]
        if docker_platform:
            docker_run_command.extend(["--platform", docker_platform])
        if workdir:
            docker_run_command.extend(["-w", workdir])
        docker_run_command.extend(
            [
                image_tag,
                "sh",
                "-lc",
                TEST_EXECUTION_SHELL_WRAPPER,
            ]
        )
        execution = _run_docker_command(
            docker_run_command,
            cwd=cwd,
            input_text=script,
            timeout_seconds=timeout_seconds,
        )
        command_results.append(
            {
                "test_command": test_command,
                "runtime_preparation_commands": runtime_commands,
                "script": script,
                "execution": execution,
                "classification": _classify_repair_test_execution(
                    execution,
                    evaluation_target=evaluation_target,
                ),
            }
        )

    effective_count = sum(
        1 for item in command_results if item["classification"]["effective"]
    )
    all_effective = effective_count == len(test_commands)
    return {
        "workdir": workdir,
        "runtime_preparation_commands": runtime_commands,
        "test_commands": test_commands,
        "results": command_results,
        "effective_test_command_count": effective_count,
        "all_test_commands_effective": all_effective,
    }


def repair_generated_dockerfile(
    *,
    dockerfile_path: Path,
    context_dir: Path,
    client: Any,
    model: str,
    run_summary: Optional[dict[str, Any]],
    repo_url: str = "",
    base_commit: Optional[str] = None,
    workdir: str = "/app",
    runtime_commands: Optional[list[str]] = None,
    test_commands: Optional[list[str]] = None,
    evaluation_target: str = "repo2run",
    artifact_dir: Optional[Path] = None,
    max_repair_rounds: int = 1,
    build_timeout_seconds: int = 1800,
    test_timeout_seconds: int = 1800,
    docker_platform: Optional[str] = None,
    image_tag: Optional[str] = None,
    cleanup_image: bool = True,
) -> dict[str, Any]:
    dockerfile_path = Path(dockerfile_path)
    context_dir = Path(context_dir)
    artifact_dir = Path(artifact_dir) if artifact_dir else dockerfile_path.parent / "logs" / "dockerfile_repair"
    runtime_commands = list(runtime_commands or [])
    test_commands = list(test_commands or [])
    evaluation_target = normalize_evaluation_target(evaluation_target)
    max_repair_rounds = max(0, int(max_repair_rounds or 0))
    image_tag = image_tag or _safe_docker_image_tag(context_dir)

    report: dict[str, Any] = {
        "enabled": True,
        "dockerfile_path": str(dockerfile_path),
        "context_dir": str(context_dir),
        "artifact_dir": str(artifact_dir),
        "image_tag": image_tag,
        "workdir": workdir,
        "runtime_preparation_commands": runtime_commands,
        "test_commands": test_commands,
        "evaluation_target": evaluation_target,
        "docker_platform": docker_platform,
        "max_repair_rounds": max_repair_rounds,
        "attempts": [],
        "repair_rounds": [],
        "cleanup": None,
        "final_success": False,
        "error": None,
    }

    try:
        current_dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
    except Exception as exc:
        report["error"] = f"Could not read Dockerfile for repair: {exc}"
        return report

    instance = {
        "instance_id": Path(context_dir).name,
        "full_name": repo_url,
        "sha": base_commit,
        "repo_url": repo_url,
    }

    for attempt_index in range(max_repair_rounds + 1):
        dockerfile_path.write_text(current_dockerfile_text, encoding="utf-8")
        docker_build = build_docker_image_for_repair(
            dockerfile_path=dockerfile_path,
            context_dir=context_dir,
            image_tag=image_tag,
            timeout_seconds=build_timeout_seconds,
            docker_platform=docker_platform,
        )

        test_execution = None
        docker_build_success = bool(
            docker_build["returncode"] == 0 and not docker_build.get("timed_out")
        )
        if docker_build_success and test_commands:
            test_execution = evaluate_dockerfile_image_for_repair(
                image_tag=image_tag,
                workdir=workdir,
                runtime_commands=runtime_commands,
                test_commands=test_commands,
                cwd=context_dir,
                timeout_seconds=test_timeout_seconds,
                docker_platform=docker_platform,
                evaluation_target=evaluation_target,
            )

        attempt_success = bool(
            docker_build_success
            and (
                not test_commands
                or (test_execution and test_execution["all_test_commands_effective"])
            )
        )
        report["attempts"].append(
            {
                "attempt": attempt_index,
                "docker_build": docker_build,
                "test_execution": test_execution,
                "success": attempt_success,
            }
        )

        if attempt_success:
            report["final_success"] = True
            break
        if attempt_index >= max_repair_rounds:
            break

        if test_execution:
            repaired_text, installed_requirements = repair_dockerfile_for_missing_python_modules(
                current_dockerfile_text,
                test_execution,
                context_dir,
            )
            if repaired_text != current_dockerfile_text:
                report["repair_rounds"].append(
                    {
                        "round": attempt_index + 1,
                        "source": "deterministic_missing_python_modules",
                        "error": None,
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        },
                        "raw_content": "",
                        "dockerfile_text": repaired_text,
                        "rationale": (
                            "Installed missing Python modules reported by fresh image validation: "
                            + ", ".join(installed_requirements)
                        ),
                        "confidence": "high",
                        "log_path": None,
                    }
                )
                current_dockerfile_text = repaired_text
                continue

        if client is None:
            report["repair_rounds"].append(
                {
                    "round": attempt_index + 1,
                    "source": "llm_error",
                    "error": "No LLM client was provided for Dockerfile repair.",
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "raw_content": "",
                    "dockerfile_text": None,
                    "rationale": "",
                    "confidence": "low",
                    "log_path": None,
                }
            )
            break

        repair_input = build_dockerfile_repair_input(
            instance=instance,
            workdir=workdir,
            dockerfile_text=current_dockerfile_text,
            run_summary=run_summary,
            runtime_commands=runtime_commands,
            test_commands=test_commands,
            evaluation_target=evaluation_target,
            docker_build=docker_build,
            test_execution=test_execution,
        )
        repair_result = repair_dockerfile_with_llm(
            client=client,
            model=model,
            repair_input=repair_input,
            artifact_dir=artifact_dir,
            round_index=attempt_index + 1,
        )
        report["repair_rounds"].append(repair_result)
        repaired_text = repair_result.get("dockerfile_text")
        if not repaired_text:
            break
        current_dockerfile_text = repaired_text

    if cleanup_image:
        report["cleanup"] = _run_docker_command(
            ["docker", "image", "rm", "-f", image_tag],
            cwd=context_dir,
            timeout_seconds=120,
        )
    return report
