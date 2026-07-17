#!/usr/bin/env python3
"""
Aggregate build results into the final dataset (keep only repos where pytest ran)

Filtering rules:
- Keep: returncode = 0 (all passed) or 1 (some failed but pytest ran)
- Exclude: returncode = 5 (no tests) or other error codes

Usage:
    python -m build_dataset.aggregate_results \
        --input output/dataset_xxx/2_build_results/summary.json \
        --output output/dataset_xxx/3_final_dataset.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to build summary JSON (from step 2)",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Path to save final dataset JSON"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print detailed statistics"
    )
    return parser.parse_args()


def aggregate_results(build_results: List[Dict[str, Any]], verbose: bool = False):
    """
    Aggregate build results and keep only valid pytest runs.

    Args:
        build_results: Build result entries.
        verbose: Whether to print detailed statistics.

    Returns:
        (final_dataset, statistics)
    """
    valid_results = []

    stats = {
        "total": len(build_results),
        "build_failed": 0,
        "no_pytest": 0,
        "pytest_invalid": 0,
        "pytest_valid": 0,
    }

    for result in build_results:
        # Stats: build failed
        if not result.get("build_success"):
            stats["build_failed"] += 1
            if verbose:
                print(f"  ✗ {result['repo']}: Build failed", file=sys.stderr)
            continue

        # Stats: no pytest result
        pytest_result = result.get("pytest_result")
        if not pytest_result:
            stats["no_pytest"] += 1
            if verbose:
                print(f"  ✗ {result['repo']}: No pytest result", file=sys.stderr)
            continue

        # Stats: invalid pytest result (returncode is not 0 or 1)
        if not pytest_result.get("is_valid"):
            stats["pytest_invalid"] += 1
            status = pytest_result.get("status", "unknown")
            if verbose:
                print(f"  ✗ {result['repo']}: Pytest {status}", file=sys.stderr)
            continue

        # Valid result
        stats["pytest_valid"] += 1

        # Build final dataset entry
        entry = {
            "repo": result["repo"],
            "image_tag": result["image_tag"],
            "build_method": result["build_method"],
            "pytest_status": pytest_result["status"],
            "returncode": pytest_result["returncode"],
            "passed_tests": pytest_result["passed_tests"],
            "failed_tests": pytest_result["failed_tests"],
            "error_files": pytest_result["error_files"],
            "final_test_cmd": pytest_result["final_test_cmd"],
            "log_dir": str(result["log_dir"]),
        }

        valid_results.append(entry)

        if verbose:
            passed = len(pytest_result["passed_tests"])
            failed = len(pytest_result["failed_tests"])
            errors = len(pytest_result["error_files"])
            print(
                f"  ✓ {result['repo']}: P={passed} F={failed} E={errors}",
                file=sys.stderr,
            )

    return valid_results, stats


def main() -> int:
    args = parse_args()

    # Check input file
    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    # Load build results
    try:
        with open(args.input, encoding="utf-8") as f:
            build_results = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON: {e}", file=sys.stderr)
        return 1

    if not isinstance(build_results, list):
        print(
            f"Error: Expected a list in JSON, got {type(build_results)}",
            file=sys.stderr,
        )
        return 1

    print(f"Processing {len(build_results)} build results...")
    if args.verbose:
        print(f"\nDetailed results:", file=sys.stderr)

    # Aggregate results
    valid_results, stats = aggregate_results(build_results, verbose=args.verbose)

    # Save final dataset
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(valid_results, f, indent=2, ensure_ascii=False)

    # Print statistics
    print(f"\n{'=' * 60}")
    print(f"Aggregation Summary")
    print(f"{'=' * 60}")
    print(f"Total build attempts:     {stats['total']}")
    print(f"  - Build failed:         {stats['build_failed']}")
    print(f"  - No pytest result:     {stats['no_pytest']}")
    print(f"  - Pytest invalid:       {stats['pytest_invalid']}")
    print(f"  - Pytest valid:         {stats['pytest_valid']}")
    print(f"{'=' * 60}")
    print(
        f"Success rate:             {stats['pytest_valid']}/{stats['total']} ({stats['pytest_valid'] / stats['total'] * 100:.1f}%)"
    )
    print(f"{'=' * 60}")
    print(f"\nFinal dataset saved to:")
    print(f"  {args.output}")
    print(f"  Total entries: {len(valid_results)}")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
