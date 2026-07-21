#!/usr/bin/env python3
"""Standalone entrypoint for the w/o-DepGraph ExecuteAgent ablation."""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Support both:
#   python -m ablation.run_execute_only ...
#   python ablation/run_execute_only.py ...
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (_PROJECT_ROOT, _PROJECT_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from httpx import Timeout
from openai import OpenAI

from src.envstate.base_image_selection import choose_base_image
from src.sandbox import Sandbox

from ablation.controller import ExecuteOnlyController
from ablation.discovery import (
    discover_test_commands,
    validate_fixed_test_commands,
)
from ablation.evidence import add_host_evidence, collect_repository_evidence
from ablation.execute_agent import ScriptExecuteAgent
from ablation.integrity import collect_source_manifest
from ablation.policy import FlatPlanGate
from ablation.runtime import SandboxHost
from ablation.trace import TraceRecorder


_PYTHON_ALPINE_RE = re.compile(
    r"^python:(?P<version>\d+(?:\.\d+){0,2})-alpine(?:\d+(?:\.\d+)*)?$",
    re.IGNORECASE,
)


def _sandbox_compatible_auto_image(
    image: str,
    *,
    automatic: bool,
) -> tuple[str, str | None]:
    """Normalize auto-selected Python Alpine images to a Bash-capable variant.

    The shared Sandbox executes setup and probes through ``/bin/bash``. Alpine
    images intentionally omit Bash, so accepting a repository Dockerfile's
    Alpine base would fail before ExecuteAgent gets a turn. Explicit user image
    choices remain untouched; this compatibility policy applies only to auto
    selection in the isolated ablation arm.
    """

    if not automatic:
        return image, None
    match = _PYTHON_ALPINE_RE.fullmatch(image.strip())
    if match is None:
        return image, None
    compatible = f"python:{match.group('version')}-slim"
    return (
        compatible,
        f"normalized auto-selected {image!r} to Bash-capable {compatible!r}",
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _parse_environment(entries: list[str]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"--env expects KEY=VALUE, got: {entry!r}")
        key, value = entry.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--env has an empty key: {entry!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"--env has an invalid environment key: {key!r}")

        upper_key = key.upper()
        stripped_value = value.strip()
        if upper_key == "PYTEST_ADDOPTS" and stripped_value not in {
            "",
            "--import-mode=importlib",
        }:
            raise ValueError(
                "PYTEST_ADDOPTS is restricted to '--import-mode=importlib'; "
                "test selection, exclusion, and collection overrides are forbidden"
            )
        if upper_key in {
            "TESTBRIDGE_TEST_ONLY",
            "VSTEST_TESTCASEFILTER",
            "JEST_TEST_NAME_PATTERN",
        } and stripped_value:
            raise ValueError(
                f"{upper_key} may filter the fixed test suite and is forbidden"
            )
        if upper_key == "GOFLAGS" and re.search(
            r"(?:^|\s)-(?:run|skip)(?:=|\s|$)",
            stripped_value,
        ):
            raise ValueError("GOFLAGS may not filter tests with -run or -skip")
        if upper_key == "NODE_OPTIONS" and re.search(
            r"(?:^|\s)--(?:require|import)(?:=|\s|$)",
            stripped_value,
        ):
            raise ValueError(
                "NODE_OPTIONS may not inject code into the fixed test process"
            )
        environment[key] = value
    return environment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "ExecuteAgent-only environment builder for the w/o-DepGraph ablation"
        )
    )
    parser.add_argument("repo", help="Path to the checked-out target repository")
    parser.add_argument("--model", default=None, help="OpenAI-compatible model slug")
    parser.add_argument(
        "--base-image",
        default="auto",
        help=(
            'Base image. Use an explicit image for formal paired experiments; '
            '"auto" is convenient for smoke tests.'
        ),
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional Docker platform override, e.g. linux/amd64",
    )
    parser.add_argument(
        "--language-hint",
        default=None,
        help="Optional primary-language hint for base-image selection",
    )
    parser.add_argument(
        "--test-command",
        action="append",
        default=[],
        help=(
            "Fixed test command. Repeat for multiple commands. If omitted, the "
            "ablation performs graph-free deterministic discovery."
        ),
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="Container environment KEY=VALUE. Repeat as needed.",
    )
    parser.add_argument("--max-cycles", type=_positive_int, default=12)
    parser.add_argument("--max-agent-calls", type=_positive_int, default=30)
    parser.add_argument(
        "--max-turns-per-decision",
        type=_positive_int,
        default=50,
        help="Maximum LLM calls, including probes, in one synthesis/repair decision",
    )
    parser.add_argument(
        "--command-timeout-seconds",
        type=_positive_int,
        default=600,
        help="Per-command Sandbox timeout used by the fixed test gate",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "output"),
        help="Directory for setup.sh, result.json, evidence.json, and trace.jsonl",
    )
    parser.add_argument(
        "--max-evidence-chars",
        type=_positive_int,
        default=160_000,
        help="Bound on repository evidence characters sent to ExecuteAgent",
    )
    parser.add_argument(
        "--completion-policy",
        choices=("all_tests_pass", "environment_ready"),
        default="all_tests_pass",
        help=(
            "Completion rule. environment_ready exports a fresh-replayed setup "
            "only after pytest is evaluable; it does not require every test to "
            "pass. The outer run_rat_benchmark.py scorer remains authoritative "
            "for per-repository pass rate and ESSR."
        ),
    )
    return parser


def _client() -> OpenAI:
    api_key = (
        os.getenv("OPENROUTER_API_KEY")
        or os.getenv("MINIMAX_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    base_url = (
        os.getenv("OPENROUTER_API_BASE")
        or os.getenv("MINIMAX_API_BASE")
        or os.getenv("OPENAI_API_BASE")
    )
    if not api_key:
        raise RuntimeError(
            "set OPENROUTER_API_KEY, MINIMAX_API_KEY, or OPENAI_API_KEY"
        )
    return OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        max_retries=0,
        timeout=Timeout(
            connect=10.0,
            read=float(os.getenv("LLM_READ_TIMEOUT", "120")),
            write=30.0,
            pool=10.0,
        ),
    )


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _repo_revision(repo: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = completed.stdout.strip()
    return revision or None


def run(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        print(f"ERROR: repository path is not a directory: {repo}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceRecorder()
    sandbox_host: SandboxHost | None = None
    experiment_started = time.monotonic()

    try:
        client = _client()
        model = args.model or os.getenv("LLM_MODEL", "gpt-4o")
        choice = choose_base_image(
            str(repo),
            client,
            model,
            explicit=None if args.base_image == "auto" else args.base_image,
            language_hint=args.language_hint,
        )
        base_image, compatibility_note = _sandbox_compatible_auto_image(
            choice.image,
            automatic=args.base_image == "auto",
        )
        if compatibility_note:
            trace(
                {
                    "event": "base_image_normalized",
                    "selected_image": choice.image,
                    "compatible_image": base_image,
                    "reason": compatibility_note,
                }
            )
            print(f"[ablation] {compatibility_note}")
        platform = args.platform or choice.platform_override
        language_requirements = tuple(getattr(choice, "languages", ()) or ())
        language_names = tuple(
            str(getattr(item, "language", "unknown"))
            for item in language_requirements
        )
        language_labels = tuple(
            (
                f"{getattr(item, 'language', 'unknown')}:"
                f"{getattr(item, 'role', 'unknown')}"
                + (
                    f"({getattr(item, 'version_constraint')})"
                    if getattr(item, "version_constraint", None)
                    else ""
                )
            )
            for item in language_requirements
        )

        test_commands = tuple(
            command.strip()
            for command in args.test_command
            if command and command.strip()
        )
        if not test_commands:
            test_commands = discover_test_commands(
                repo,
                language_names,
                primary_language=args.language_hint,
            )
        test_errors = validate_fixed_test_commands(test_commands)
        if test_errors:
            raise ValueError("; ".join(test_errors))

        evidence = collect_repository_evidence(
            repo,
            max_total_chars=args.max_evidence_chars,
        )
        source_manifest = collect_source_manifest(repo)
        environment = _parse_environment(args.env)
        sandbox = Sandbox(
            base_image=base_image,
            workdir="/app",
            platform=platform,
            seed_dir=str(repo),
            enable_cache_volume=False,
            command_timeout_seconds=args.command_timeout_seconds,
            ensure_native_platform=platform is None,
            environment=environment or None,
        )
        sandbox_host = SandboxHost(
            sandbox,
            source_manifest=source_manifest,
        )
        base_image_ref = str(
            getattr(sandbox, "base_image_ref", None) or base_image
        )
        base_image_alias = getattr(sandbox, "base_image_alias", None)
        resolved_platform = getattr(sandbox, "platform", None) or platform
        evidence = add_host_evidence(
            evidence,
            base_image=base_image_ref,
            platform=resolved_platform,
            languages=language_labels or language_names,
            test_commands=test_commands,
        )
        _write_json(output_dir / "evidence.json", evidence.to_dict())

        gate = FlatPlanGate()
        agent = ScriptExecuteAgent(
            client,
            model,
            gate=gate,
            event_sink=trace,
        )
        controller = ExecuteOnlyController(
            agent=agent,
            host=sandbox_host,
            evidence=evidence,
            base_image=base_image_ref,
            languages=language_labels or language_names,
            test_commands=test_commands,
            gate=gate,
            max_cycles=args.max_cycles,
            max_agent_calls=args.max_agent_calls,
            max_turns_per_decision=args.max_turns_per_decision,
            completion_policy=args.completion_policy,
            event_sink=trace,
        )

        result = controller.run()
        duration_seconds = time.monotonic() - experiment_started
        (output_dir / "setup.sh").write_text(result.setup_sh, encoding="utf-8")
        payload = result.to_dict()
        replay_events = [
            event
            for event in trace.events
            if event.get("event") == "setup_replay"
        ]
        payload["metrics"] = {
            "duration_seconds": round(duration_seconds, 3),
            "fresh_replays": len(replay_events),
            "search_replays": sum(
                event.get("stage") == "search" for event in replay_events
            ),
            "terminal_replays": sum(
                event.get("stage") == "terminal" for event in replay_events
            ),
            "accepted_repairs": sum(
                event.get("event") == "patch_accepted"
                for event in trace.events
            ),
        }
        payload["config"] = {
            "repo": str(repo),
            "model": model,
            "base_image": base_image,
            "base_image_selected": choice.image,
            "base_image_ref": base_image_ref,
            "base_image_alias": base_image_alias,
            "base_image_reason": "; ".join(
                item for item in (choice.reason, compatibility_note) if item
            ),
            "platform": resolved_platform,
            "repo_revision": _repo_revision(repo),
            "languages": list(language_labels or language_names),
            "test_commands": list(test_commands),
            "environment_keys": sorted(environment),
            "protected_file_count": len(source_manifest),
            "shared_cache_enabled": False,
            "completion_policy": args.completion_policy,
            "max_cycles": args.max_cycles,
            "max_agent_calls": args.max_agent_calls,
            "max_turns_per_decision": args.max_turns_per_decision,
        }
        _write_json(output_dir / "result.json", payload)
        trace.write_jsonl(output_dir / "trace.jsonl")
        print(
            f"[ablation] status={result.status} reason={result.stop_reason} "
            f"cycles={result.cycles} llm_calls={result.llm_calls}"
        )
        print(f"[ablation] setup.sh -> {output_dir / 'setup.sh'}")
        print(f"[ablation] result -> {output_dir / 'result.json'}")
        return 0 if result.status == "success" else 1
    except Exception as exc:  # CLI boundary: preserve a structured failure artifact
        trace({"event": "cli_error", "error": repr(exc)})
        trace.write_jsonl(output_dir / "trace.jsonl")
        _write_json(
            output_dir / "result.json",
            {
                "arm": "w/o_depgraph_execute_agent_only",
                "status": "failed",
                "stop_reason": "cli_error",
                "error": repr(exc),
                "repo": str(repo),
                "metrics": {
                    "duration_seconds": round(
                        time.monotonic() - experiment_started,
                        3,
                    )
                },
            },
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if sandbox_host is not None:
            sandbox_host.close()


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
