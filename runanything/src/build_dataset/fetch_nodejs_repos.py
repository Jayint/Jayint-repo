#!/usr/bin/env python3
"""
Fetch Node.js repositories (JavaScript/TypeScript) from the GitHub API

Usage:
    python -m build_dataset.fetch_nodejs_repos \
        --count 100 \
        --output output/dataset_xxx/1_nodejs_repos.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from build_dataset._common import (
    GitHubClient,
    categorize_repos_by_size,
    print_size_statistics,
    save_categorized_repos,
)
from libkit.config import config


def parse_args() -> argparse.Namespace:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="Number of repositories to collect (default: 50)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(f"output/datasets_{timestamp}"),
        help="Directory to save categorized output files (default: output/datasets_<timestamp>)",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=50,
        help="Items per search page (max 100, default: 50)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Maximum number of search pages to iterate (default: 20)",
    )
    parser.add_argument(
        "--small-threshold",
        type=int,
        default=500_000,
        help="Small project size threshold in bytes (default: 500000 = 500 KB)",
    )
    parser.add_argument(
        "--large-threshold",
        type=int,
        default=5_000_000,
        help="Large project size threshold in bytes (default: 5000000 = 5 MB)",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=365,
        help="Maximum age in days (default: 365). Repos not updated in this many days will be excluded.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Get GitHub token
    token = config.GITHUB_TOKEN
    if not token:
        print(
            "Warning: No GitHub token provided. API rate limits will be very low.",
            file=sys.stderr,
        )

    # Initialize client
    print("Initializing GitHub client...")
    client = GitHubClient(token)

    # Search repositories
    print(f"Searching for {args.count} JavaScript/TypeScript repositories")
    print(f"Requirements: package.json")
    print(f"{'-' * 60}")

    try:
        repos = client.collect_repos(
            target_count=args.count,
            language="JavaScript",
            per_page=max(1, min(args.per_page, 100)),
            max_pages=max(1, args.max_pages),
            require_dockerfile=False,
            require_tests=False,
            build_tool="nodejs",
        )
    except Exception as exc:
        print(f"Error during repository collection: {exc}", file=sys.stderr)
        return 1

    # Save results
    if len(repos) < args.count:
        print(
            f"\nWarning: only collected {len(repos)} repositories (requested {args.count})",
            file=sys.stderr,
        )

    if not repos:
        print("No repositories collected. Exiting.", file=sys.stderr)
        return 1

    # Categorize by size
    print(f"\n{'=' * 60}")
    print(f"Categorizing repositories by size...")
    print(f"{'=' * 60}")

    categories = categorize_repos_by_size(
        repos,
        small_threshold=args.small_threshold,
        large_threshold=args.large_threshold,
    )

    # Print size stats
    print_size_statistics(categories, len(repos))

    # Save categorized results
    save_categorized_repos(
        repos=repos,
        categories=categories,
        output_dir=args.output_dir,
        language_prefix="nodejs",
        extra_summary_fields={
            "language": "nodejs",
            "small_threshold_bytes": args.small_threshold,
            "large_threshold_bytes": args.large_threshold,
        },
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
