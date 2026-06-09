#!/usr/bin/env python3
"""Extract failed Repo2Run benchmark result records from results/*.json."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_RESULTS_DIR = Path("outputs/repo2run_benchmark/results")
DEFAULT_OUTPUT_JSONL = Path("outputs/repo2run_benchmark/failed_results.jsonl")
DEFAULT_SUMMARY_JSON = Path("outputs/repo2run_benchmark/failed_results_summary.json")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def is_failed(result: dict[str, Any], mode: str) -> bool:
    if mode == "environment":
        return result.get("environment_build_success") is not True
    if mode == "execution-status":
        return result.get("execution_status") not in {"success", "environment_built"}
    if mode == "needs-repair":
        return result.get("needs_repair") is True or result.get("goal_status") == "needs_repair"
    raise ValueError(f"Unknown failure mode: {mode}")


def get_nested(mapping: dict[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def compact_record(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    dataset_entry = result.get("dataset_entry") or {}
    debug_artifacts = result.get("debug_artifacts") or {}
    docker_build = result.get("docker_build") or {}
    test_execution = result.get("test_execution") or {}
    run_summary = result.get("run_summary") or {}

    return {
        "instance_id": dataset_entry.get("instance_id") or path.stem,
        "full_name": dataset_entry.get("full_name"),
        "repo_url": dataset_entry.get("repo_url"),
        "sha": dataset_entry.get("sha"),
        "base_commit": dataset_entry.get("base_commit"),
        "paper_build_success": result.get("paper_build_success"),
        "paper_build_success_label": dataset_entry.get("paper_build_success_label"),
        "language": dataset_entry.get("language"),
        "execution_status": result.get("execution_status"),
        "paper_alignment": result.get("paper_alignment"),
        "goal_status": result.get("goal_status"),
        "needs_repair": result.get("needs_repair"),
        "agent_claimed_success": result.get("agent_claimed_success"),
        "agent_dockerfile_present": result.get("agent_dockerfile_present"),
        "agent_dockerfile_usable": result.get("agent_dockerfile_usable"),
        "dockerfile_generation_success": result.get("dockerfile_generation_success"),
        "environment_build_success": result.get("environment_build_success"),
        "verification_command_source": result.get("verification_command_source"),
        "docker_build": {
            "returncode": docker_build.get("returncode"),
            "timed_out": docker_build.get("timed_out"),
            "duration_seconds": docker_build.get("duration_seconds"),
        },
        "test_execution": {
            "all_test_commands_effective": test_execution.get("all_test_commands_effective"),
            "returncode": test_execution.get("returncode"),
            "timed_out": test_execution.get("timed_out"),
            "duration_seconds": test_execution.get("duration_seconds"),
        },
        "run_summary": {
            "configuration_success": run_summary.get("configuration_success"),
            "base_image": run_summary.get("base_image"),
            "platform_override": run_summary.get("platform_override"),
            "verified_test_command": run_summary.get("verified_test_command"),
            "verified_test_commands": run_summary.get("verified_test_commands") or [],
        },
        "result_json_path": str(path),
        "benchmark_log_path": (
            debug_artifacts.get("benchmark_log_path")
            or result.get("benchmark_log_path")
            or get_nested(result, "debug_artifacts", "benchmark_log_path")
        ),
        "run_summary_path": result.get("run_summary_path"),
        "eval_dockerfile_path": result.get("eval_dockerfile_path"),
    }


def extract_failed(
    results_dir: Path,
    failure_mode: str,
    include_full: bool,
) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*.json")):
        result = load_json(path)
        if not is_failed(result, failure_mode):
            continue
        if include_full:
            record = {
                "instance_id": (result.get("dataset_entry") or {}).get("instance_id") or path.stem,
                "result_json_path": str(path),
                "result": result,
            }
        else:
            record = compact_record(path, result)
        failed.append(record)
    return failed


def build_summary(records: list[dict[str, Any]], failure_mode: str) -> dict[str, Any]:
    status_counts = Counter(record.get("execution_status") for record in records)
    alignment_counts = Counter(record.get("paper_alignment") for record in records)
    goal_counts = Counter(record.get("goal_status") for record in records)
    return {
        "failure_mode": failure_mode,
        "failed_count": len(records),
        "execution_status_counts": dict(status_counts),
        "paper_alignment_counts": dict(alignment_counts),
        "goal_status_counts": dict(goal_counts),
        "instance_ids": [record.get("instance_id") for record in records],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract failed records from outputs/repo2run_benchmark/results/*.json."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help=f"Directory containing result JSON files. Default: {DEFAULT_RESULTS_DIR}",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=DEFAULT_OUTPUT_JSONL,
        help=f"Path for extracted failed records. Default: {DEFAULT_OUTPUT_JSONL}",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=DEFAULT_SUMMARY_JSON,
        help=f"Path for summary JSON. Default: {DEFAULT_SUMMARY_JSON}",
    )
    parser.add_argument(
        "--failure-mode",
        choices=("environment", "execution-status", "needs-repair"),
        default="environment",
        help=(
            "Failure definition. 'environment' means environment_build_success is not true; "
            "'execution-status' means execution_status is not success/environment_built; "
            "'needs-repair' means needs_repair or goal_status indicates repair is needed."
        ),
    )
    parser.add_argument(
        "--include-full",
        action="store_true",
        help="Write the full original result JSON under each extracted record.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.results_dir.is_dir():
        raise SystemExit(f"Results directory does not exist: {args.results_dir}")

    failed = extract_failed(args.results_dir, args.failure_mode, args.include_full)
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as handle:
        for record in failed:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = build_summary(failed, args.failure_mode)
    write_json(args.summary_json, summary)

    print(f"Extracted {len(failed)} failed result(s)")
    print(f"JSONL: {args.output_jsonl}")
    print(f"Summary: {args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
