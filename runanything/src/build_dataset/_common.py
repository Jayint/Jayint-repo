"""Common utilities and shared classes.

This module contains:
- Data class definitions (RepoInfo, PytestResult, BuildResult)
- GitHubClient: a unified GitHub API client
- PytestResultParser: a unified pytest result parser
- PathManager: unified input/output path management
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

import requests


# ============ Constants ============

# Code language list (used to filter out non-code resources like images and docs)
CODE_LANGUAGES = {
    # Mainstream languages
    "Python",
    "Java",
    "JavaScript",
    "TypeScript",
    "Rust",
    "Go",
    "C",
    "C++",
    "C#",
    "Ruby",
    "PHP",
    "Swift",
    "Kotlin",
    "Scala",
    # Shell and scripting
    "Shell",
    "Bash",
    "PowerShell",
    # Other common languages
    "Dart",
    "Lua",
    "R",
    "Perl",
    "Haskell",
    "Elixir",
    "Erlang",
    "Clojure",
    "F#",
    "OCaml",
    "Objective-C",
    "Groovy",
    "Julia",
    "Nim",
    "Zig",
    "V",
    "Crystal",
    # Web-related (backend logic only)
    "Vue",
    "Svelte",
}


# ============ Data Classes ============


@dataclass
class RepoInfo:
    """Repository information."""

    full_name: str
    clone_url: str
    default_branch: str
    stargazers_count: int = 0
    language: str = "Python"
    description: Optional[str] = None
    html_url: Optional[str] = None
    forks_count: int = 0
    topics: Optional[List[str]] = None
    total_bytes: int = 0  # Total bytes across all languages
    code_bytes: int = 0  # Bytes of code languages only (excluding images/docs/etc.)
    language_breakdown: Optional[Dict[str, float]] = (
        None  # Language distribution percentage
    )
    size: Optional[str] = None  # Project size: 'small', 'medium', 'large'

    def __post_init__(self):
        if self.topics is None:
            self.topics = []
        if self.language_breakdown is None:
            self.language_breakdown = {}


@dataclass
class PytestResult:
    """pytest execution result."""

    repo: str
    image_tag: str
    returncode: int
    status: str  # 'passed' | 'tests_failed' | 'no_tests' | 'error' | 'timeout'
    passed_tests: List[str]
    failed_tests: List[str]
    error_files: List[str]
    stdout: str
    stderr: str
    is_valid: bool  # True if pytest ran successfully (rc=0 or 1)
    final_test_cmd: List[str]


@dataclass
class BuildResult:
    """Build result."""

    repo: str
    image_tag: str
    build_success: bool
    build_method: str  # 'simple' | 'agent'
    pytest_result: Optional[PytestResult]
    error: Optional[str]
    log_dir: Path


@dataclass
class SizeCategory:
    """Project size category."""

    name: str  # 'small', 'medium', 'large'
    display_name: str  # 'Small', 'Medium', 'Large'
    repos: List[RepoInfo]
    size_range: str  # e.g. "< 500 KB"
    min_bytes: int
    max_bytes: int
    avg_bytes: int
    count: int

    def __post_init__(self):
        if not self.repos:
            self.min_bytes = 0
            self.max_bytes = 0
            self.avg_bytes = 0
            self.count = 0
        else:
            self.count = len(self.repos)
            sizes = [r.total_bytes for r in self.repos]
            self.min_bytes = min(sizes)
            self.max_bytes = max(sizes)
            self.avg_bytes = sum(sizes) // len(sizes) if sizes else 0


# ============ GitHub API Client ============


class GitHubClient:
    """A unified GitHub API client."""

    API_ROOT = "https://api.github.com"
    TEST_DIR_NAMES = {"tests", "test", "__tests__", "unit_tests", "unittests"}

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self.session = self._build_session()

    def _build_session(self) -> requests.Session:
        """Build a requests session."""
        session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "rat-dataset-builder/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        session.headers.update(headers)
        return session

    def _request_with_backoff(
        self, method: str, url: str, retries: int = 5, **kwargs
    ) -> requests.Response:
        """Request with retries and rate-limit handling."""
        backoff = 1.0
        while True:
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
            except requests.RequestException as exc:
                if retries <= 0:
                    raise RuntimeError(f"Request to {url} failed: {exc}")
                time.sleep(backoff)
                retries -= 1
                backoff = min(backoff * 2, 60)
                continue

            # Handle rate limiting
            if (
                response.status_code == 403
                and response.headers.get("X-RateLimit-Remaining") == "0"
            ):
                reset_ts = int(response.headers.get("X-RateLimit-Reset", "0") or 0)
                sleep_for = max(reset_ts - int(time.time()) + 1, 1)
                print(
                    f"Hit GitHub rate limit, sleeping {sleep_for}s until reset",
                    file=sys.stderr,
                )
                time.sleep(sleep_for)
                continue

            # Handle server errors
            if response.status_code >= 500:
                if retries <= 0:
                    response.raise_for_status()
                    return response
                time.sleep(backoff)
                retries -= 1
                backoff = min(backoff * 2, 60)
                continue

            return response

    def search_repos(
        self,
        query: str,
        per_page: int = 50,
        page: int = 1,
        sort_by: Optional[str] = "updated",
    ) -> Dict:
        """Search repositories.

        Args:
            query: Search query string
            per_page: Results per page
            page: Page number
            sort_by: Sort key: 'stars', 'updated', 'forks', or None (GitHub best match)
        """
        params = {
            "q": query,
            "per_page": per_page,
            "page": page,
        }
        # Add sorting parameters (if specified)
        if sort_by:
            params["sort"] = sort_by
            params["order"] = "desc"

        response = self._request_with_backoff(
            "GET", f"{self.API_ROOT}/search/repositories", params=params
        )
        response.raise_for_status()
        return response.json()

    def get_repo_tree(self, full_name: str, branch: str) -> Optional[List[Dict]]:
        """Get the repository file tree."""
        url = f"{self.API_ROOT}/repos/{full_name}/git/trees/{branch}"
        response = self._request_with_backoff("GET", url, params={"recursive": "1"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        return payload.get("tree")

    def get_repo_languages(self, full_name: str) -> Dict[str, int]:
        """
        Get repository language statistics.

        Args:
            full_name: Repository full name (owner/repo)

        Returns:
            Dict[language, bytes], e.g. {'Python': 102030, 'HTML': 5000}
        """
        url = f"{self.API_ROOT}/repos/{full_name}/languages"
        response = self._request_with_backoff("GET", url)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    def has_dockerfile(self, tree_items: List[Dict]) -> bool:
        """Check whether the repository contains a Dockerfile."""
        for item in tree_items:
            path = item.get("path", "").lower()
            item_type = item.get("type", "")
            if item_type == "blob" and path == "dockerfile":
                return True
            # Also check Dockerfile in subdirectories
            if "/" in path and path.endswith("/dockerfile"):
                return True
        return False

    def has_maven(self, tree_items: List[Dict], root_only: bool = False) -> bool:
        """Check whether the repository contains Maven build files (pom.xml).

        Args:
            tree_items: Repository file tree
            root_only: Check only repo root (True) or all directories (False)
        """
        for item in tree_items:
            path = item.get("path", "").lower()
            item_type = item.get("type", "")
            if item_type == "blob":
                if root_only:
                    # Check only root
                    if path == "pom.xml":
                        return True
                else:
                    # Check all directories
                    if path.endswith("pom.xml"):
                        return True
        return False

    def has_gradle(self, tree_items: List[Dict], root_only: bool = False) -> bool:
        """Check whether the repository contains Gradle build files.

        Args:
            tree_items: Repository file tree
            root_only: Check only repo root (True) or all directories (False)
        """
        for item in tree_items:
            path = item.get("path", "").lower()
            item_type = item.get("type", "")
            if item_type == "blob":
                if root_only:
                    # Check only root
                    if path == "build.gradle" or path == "build.gradle.kts":
                        return True
                else:
                    # Check all directories
                    if path.endswith("build.gradle") or path.endswith(
                        "build.gradle.kts"
                    ):
                        return True
        return False

    def has_nodejs(self, tree_items: List[Dict]) -> bool:
        """Check whether the repository contains Node.js project files (package.json)."""
        for item in tree_items:
            path = item.get("path", "").lower()
            item_type = item.get("type", "")
            if item_type == "blob" and path == "package.json":
                return True
        return False

    def has_rust(self, tree_items: List[Dict]) -> bool:
        """Check whether the repository contains Rust project files (Cargo.toml)."""
        for item in tree_items:
            path = item.get("path", "").lower()
            item_type = item.get("type", "")
            if item_type == "blob" and path == "cargo.toml":
                return True
        return False

    def has_build_tool(
        self, tree_items: List[Dict], tool: str, root_only: bool = False
    ) -> bool:
        """
        Check whether the repository contains a specific build tool.

        Args:
            tree_items: Repository file tree
            tool: 'maven', 'gradle', 'nodejs', 'rust', or 'any' (Java only)
            root_only: Check only repo root (True) or all directories (False)

        Returns:
            bool: Whether the specified build tool is present
        """
        if tool == "maven":
            return self.has_maven(tree_items, root_only=root_only)
        elif tool == "gradle":
            return self.has_gradle(tree_items, root_only=root_only)
        elif tool == "any":
            return self.has_maven(tree_items, root_only=root_only) or self.has_gradle(
                tree_items, root_only=root_only
            )
        elif tool == "nodejs":
            return self.has_nodejs(tree_items)
        elif tool == "rust":
            return self.has_rust(tree_items)
        else:
            return False

    def has_tests(self, tree_items: List[Dict]) -> bool:
        """Check whether the repository contains test files."""
        for item in tree_items:
            path = item.get("path", "")
            if not path:
                continue

            parts = path.split("/")

            # Check for test directories
            if any(part.lower() in self.TEST_DIR_NAMES for part in parts[:-1]):
                return True

            # Check for test files
            if item.get("type") == "blob" and self._is_test_file(path):
                return True

        return False

    def _is_test_file(self, path: str) -> bool:
        """Determine whether a path is a test file."""
        filename = path.rsplit("/", 1)[-1].lower()
        if not filename.endswith(".py"):
            return False
        if filename.startswith("test_") or filename.endswith("_test.py"):
            return True
        return filename in {"conftest.py"}

    def collect_repos(
        self,
        target_count: int,
        language: str = "Python",
        per_page: int = 50,
        max_pages: int = 20,
        require_dockerfile: bool = True,
        require_tests: bool = True,
        build_tool: Optional[str] = None,
        root_only: bool = False,
        max_age_days: int = 365,
    ) -> List[RepoInfo]:
        """Collect repositories that meet the criteria (two-dimensional stratified sampling).

        This uses 2D stratified sampling based on repo size and star count to get a more balanced distribution:

        Size buckets (40% small / 40% medium / 20% large):
        - Small repos (< 1MB)
        - Medium repos (1-5MB)
        - Large repos (> 5MB)

        Star buckets within each size bucket (30% / 30% / 30% / 10%):
        - 11-100 stars: long-tail repos (30%)
        - 101-1000 stars: mid-tier popularity (30%)
        - 1001-10000 stars: popular projects (30%)
        - >10000 stars: top-tier projects (10%)

        Args:
            target_count: Target repository count
            language: Programming language (e.g. 'Python', 'Java', 'JavaScript', 'Rust')
            per_page: Results per page
            max_pages: Max pages
            require_dockerfile: Whether Dockerfile is required
            require_tests: Whether tests are required
            build_tool: Build tool (e.g. 'maven', 'gradle', 'nodejs', 'rust', 'any')
            root_only: Whether to check build files only in repo root
            max_age_days: Max days since last update
        """
        collected: List[RepoInfo] = []
        seen = set()

        # Define 2D stratified search strategy: size × stars
        search_strategies = [
            {
                "size_range": "size:<1000",  # < 1MB
                "percentage": 0.40,
                "description": "Small repos (< 1MB)",
                "star_buckets": [
                    {
                        "stars": "11..100",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "long-tail",
                    },
                    {
                        "stars": "101..1000",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "mid-tier",
                    },
                    {
                        "stars": "1001..10000",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "popular",
                    },
                    {
                        "stars": ">10000",
                        "percentage": 0.10,
                        "sort": "updated",
                        "desc": "top-tier",
                    },
                ],
            },
            {
                "size_range": "size:1000..5000",  # 1-5MB
                "percentage": 0.40,
                "description": "Medium repos (1-5MB)",
                "star_buckets": [
                    {
                        "stars": "11..100",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "long-tail",
                    },
                    {
                        "stars": "101..1000",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "mid-tier",
                    },
                    {
                        "stars": "1001..10000",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "popular",
                    },
                    {
                        "stars": ">10000",
                        "percentage": 0.10,
                        "sort": "updated",
                        "desc": "top-tier",
                    },
                ],
            },
            {
                "size_range": "size:>5000",  # > 5MB
                "percentage": 0.20,
                "description": "Large repos (> 5MB)",
                "star_buckets": [
                    {
                        "stars": "11..100",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "long-tail",
                    },
                    {
                        "stars": "101..1000",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "mid-tier",
                    },
                    {
                        "stars": "1001..10000",
                        "percentage": 0.30,
                        "sort": "updated",
                        "desc": "popular",
                    },
                    {
                        "stars": ">10000",
                        "percentage": 0.10,
                        "sort": "updated",
                        "desc": "top-tier",
                    },
                ],
            },
        ]

        # Compute cutoff date
        cutoff_date = (datetime.now() - timedelta(days=max_age_days)).strftime(
            "%Y-%m-%d"
        )

        # Outer loop: iterate size buckets
        for size_idx, size_strategy in enumerate(search_strategies, 1):
            size_target = int(target_count * size_strategy["percentage"])

            # For the last size bucket, collect the remaining needed count
            if size_idx == len(search_strategies):
                size_target = target_count - len(collected)

            if size_target <= 0:
                continue

            print(
                f"\n{'=' * 70}",
                file=sys.stderr,
            )
            print(
                f"SIZE BUCKET {size_idx}/{len(search_strategies)}: {size_strategy['description']}",
                file=sys.stderr,
            )
            print(
                f"Size target: {size_target} repos (total collected so far: {len(collected)}/{target_count})",
                file=sys.stderr,
            )
            print(
                f"{'=' * 70}",
                file=sys.stderr,
            )

            size_collected = 0

            # Inner loop: iterate star buckets
            for star_idx, star_bucket in enumerate(size_strategy["star_buckets"], 1):
                star_target = int(size_target * star_bucket["percentage"])

                # For the last star bucket, collect the remaining needed count
                if star_idx == len(size_strategy["star_buckets"]):
                    star_target = size_target - size_collected

                if star_target <= 0:
                    continue

                # Build query string: language + pushed date + size + stars
                query = f"language:{language} pushed:>{cutoff_date} stars:{star_bucket['stars']} {size_strategy['size_range']}"

                print(
                    f"\n  --- Star bucket {star_idx}/{len(size_strategy['star_buckets'])}: "
                    f"stars:{star_bucket['stars']} ({star_bucket['desc']}) ---",
                    file=sys.stderr,
                )
                print(
                    f"  Query: {query}",
                    file=sys.stderr,
                )
                print(
                    f"  Sort: {star_bucket['sort']}",
                    file=sys.stderr,
                )
                print(
                    f"  Target: {star_target} repos (size bucket: {size_collected}/{size_target})",
                    file=sys.stderr,
                )

                page = 1
                star_collected = 0

                while star_collected < star_target and page <= max_pages:
                    print(
                        f"    Page {page} (star collected: {star_collected}/{star_target})",
                        file=sys.stderr,
                    )

                    try:
                        data = self.search_repos(
                            query,
                            per_page=per_page,
                            page=page,
                            sort_by=star_bucket["sort"],
                        )
                    except Exception as exc:
                        print(
                            f"    Search failed on page {page}: {exc}", file=sys.stderr
                        )
                        break

                    items = data.get("items", [])
                    if not items:
                        print(
                            f"    No more results for this star range", file=sys.stderr
                        )
                        break

                    for repo in items:
                        full_name = repo.get("full_name")
                        if not full_name or full_name in seen:
                            continue
                        seen.add(full_name)

                        default_branch = repo.get("default_branch") or "main"

                        # Check for Dockerfile and tests
                        try:
                            tree_items = self.get_repo_tree(full_name, default_branch)
                            if tree_items is None:
                                continue

                            has_docker = self.has_dockerfile(tree_items)
                            has_test = self.has_tests(tree_items)

                            if require_dockerfile and not has_docker:
                                continue
                            if require_tests and not has_test:
                                continue

                            # Check build tool (if specified)
                            if build_tool:
                                if not self.has_build_tool(
                                    tree_items, build_tool, root_only=root_only
                                ):
                                    continue

                        except requests.HTTPError as exc:
                            print(f"    Skipping {full_name}: {exc}", file=sys.stderr)
                            continue

                        # Get language stats
                        try:
                            lang_stats = self.get_repo_languages(full_name)
                        except requests.HTTPError as exc:
                            print(
                                f"    Warning: Failed to get language stats for {full_name}: {exc}",
                                file=sys.stderr,
                            )
                            lang_stats = {}

                        # Compute total bytes, code bytes, and language percentage breakdown
                        total_bytes = sum(lang_stats.values())
                        code_bytes = sum(
                            byte_count
                            for lang, byte_count in lang_stats.items()
                            if lang in CODE_LANGUAGES
                        )
                        lang_breakdown = {}
                        if total_bytes > 0:
                            for lang, count in lang_stats.items():
                                percentage = (count / total_bytes) * 100
                                if (
                                    percentage > 0.1
                                ):  # Keep only languages with >0.1% share
                                    lang_breakdown[lang] = round(percentage, 2)

                        # Create RepoInfo
                        repo_info = RepoInfo(
                            full_name=full_name,
                            html_url=repo.get("html_url"),
                            clone_url=repo.get("clone_url"),
                            default_branch=default_branch,
                            stargazers_count=repo.get("stargazers_count", 0),
                            forks_count=repo.get("forks_count", 0),
                            language=repo.get("language", "Python"),
                            topics=repo.get("topics", []),
                            description=repo.get("description"),
                            total_bytes=total_bytes,
                            code_bytes=code_bytes,
                            language_breakdown=lang_breakdown,
                        )

                        collected.append(repo_info)
                        star_collected += 1
                        size_collected += 1
                        print(
                            f"    ✓ Added {full_name} ({repo_info.stargazers_count} ⭐, "
                            f"code: {code_bytes:,} bytes) "
                            f"[star: {star_collected}/{star_target}, "
                            f"size: {size_collected}/{size_target}, "
                            f"total: {len(collected)}/{target_count}]",
                            file=sys.stderr,
                        )

                        if star_collected >= star_target:
                            break

                    page += 1

                print(
                    f"  ✓ Star bucket completed: collected {star_collected}/{star_target} repos",
                    file=sys.stderr,
                )

                # If the size bucket has hit its target, stop iterating star buckets
                if size_collected >= size_target:
                    break

            print(
                f"\n✓ SIZE BUCKET {size_idx} completed: collected {size_collected}/{size_target} repos",
                file=sys.stderr,
            )

            # If we've reached the overall target, exit early
            if len(collected) >= target_count:
                break

        # Sort by code_bytes ascending (smaller repos first; higher build success rates)
        collected.sort(key=lambda x: x.code_bytes)

        return collected


def categorize_repos_by_size(
    repos: List[RepoInfo],
    small_threshold: int = 500_000,  # 500 KB
    large_threshold: int = 5_000_000,  # 5 MB
) -> Dict[str, SizeCategory]:
    """Categorize repositories by project size (based on code bytes).

    Args:
        repos: Repository list (should already be sorted by code_bytes)
        small_threshold: Upper bound for small projects (code bytes)
        large_threshold: Lower bound for large projects (code bytes)

    Returns:
        Dict[category_name, SizeCategory] containing 'small', 'medium', 'large'
    """
    small_repos = []
    medium_repos = []
    large_repos = []

    for repo in repos:
        # Categorize by code_bytes rather than total_bytes
        if repo.code_bytes < small_threshold:
            repo.size = "small"
            small_repos.append(repo)
        elif repo.code_bytes < large_threshold:
            repo.size = "medium"
            medium_repos.append(repo)
        else:
            repo.size = "large"
            large_repos.append(repo)

    categories = {
        "small": SizeCategory(
            name="small",
            display_name="Small",
            repos=small_repos,
            size_range=f"< {small_threshold // 1000} KB",
            min_bytes=0,
            max_bytes=0,
            avg_bytes=0,
            count=0,
        ),
        "medium": SizeCategory(
            name="medium",
            display_name="Medium",
            repos=medium_repos,
            size_range=f"{small_threshold // 1000} KB - {large_threshold // 1_000_000} MB",
            min_bytes=0,
            max_bytes=0,
            avg_bytes=0,
            count=0,
        ),
        "large": SizeCategory(
            name="large",
            display_name="Large",
            repos=large_repos,
            size_range=f"> {large_threshold // 1_000_000} MB",
            min_bytes=0,
            max_bytes=0,
            avg_bytes=0,
            count=0,
        ),
    }

    return categories


def format_bytes(num_bytes: int) -> str:
    """Format a byte count into a human-readable string."""
    size = float(num_bytes)
    for unit in ["bytes", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:,.0f} {unit}"
        size /= 1024.0
    return f"{size:,.0f} TB"


def print_size_statistics(
    categories: Dict[str, SizeCategory], total_repos: int
) -> None:
    """Print repository size distribution statistics.

    Args:
        categories: Categories after grouping
        total_repos: Total repository count
    """
    print(f"\n{'=' * 60}")
    print(f"Repository Size Distribution")
    print(f"{'=' * 60}")
    print(f"Total Repositories: {total_repos}\n")

    for cat_name in ["small", "medium", "large"]:
        cat = categories[cat_name]
        print(f"📦 {cat.display_name} Projects ({cat.size_range}): {cat.count} repos")
        if cat.count > 0:
            print(
                f"   Range: {format_bytes(cat.min_bytes)} - {format_bytes(cat.max_bytes)}"
            )
            print(f"   Average: {format_bytes(cat.avg_bytes)}")
            print(f"   Examples:")
            for repo in cat.repos[:2]:  # Show the first 2 examples
                code_pct = (
                    (repo.code_bytes / repo.total_bytes * 100)
                    if repo.total_bytes > 0
                    else 0
                )
                print(f"   - {repo.full_name}")
                print(
                    f"     Code: {format_bytes(repo.code_bytes)}, Total: {format_bytes(repo.total_bytes)} ({code_pct:.1f}% code)"
                )
        print()


def save_categorized_repos(
    repos: List[RepoInfo],
    categories: Dict[str, SizeCategory],
    output_dir: Path,
    language_prefix: str,
    extra_summary_fields: Optional[Dict[str, Any]] = None,
) -> None:
    """Save categorized repositories to JSON files.

    Args:
        repos: All repositories
        categories: Categories after grouping
        output_dir: Output directory
        language_prefix: File name prefix (e.g. 'python', 'java', 'nodejs', 'rust')
        extra_summary_fields: Additional summary fields (e.g. build_tool, language)
    """
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save all repositories (sorted by size)
    all_repos_file = output_dir / f"{language_prefix}_repos_all.json"
    with open(all_repos_file, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in repos], f, ensure_ascii=False, indent=2)

    # Save each category
    for cat_name in ["small", "medium", "large"]:
        cat = categories[cat_name]
        cat_file = output_dir / f"{language_prefix}_repos_{cat_name}.json"
        with open(cat_file, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in cat.repos], f, ensure_ascii=False, indent=2)

    # Save categorization summary
    summary = {
        "total_count": len(repos),
        "categorization_method": "absolute",
        "categories": {
            cat_name: {
                "count": categories[cat_name].count,
                "size_range": categories[cat_name].size_range,
                "min_bytes": categories[cat_name].min_bytes,
                "max_bytes": categories[cat_name].max_bytes,
                "avg_bytes": categories[cat_name].avg_bytes,
            }
            for cat_name in ["small", "medium", "large"]
        },
    }

    # Add extra summary fields
    if extra_summary_fields:
        summary.update(extra_summary_fields)

    summary_file = output_dir / f"{language_prefix}_repos_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Print save info
    print(f"{'=' * 60}")
    print(f"Files saved to: {output_dir}")
    print(f"{'=' * 60}")
    print(f"  - {language_prefix}_repos_all.json ({len(repos)} repos)")
    print(f"  - {language_prefix}_repos_small.json ({categories['small'].count} repos)")
    print(
        f"  - {language_prefix}_repos_medium.json ({categories['medium'].count} repos)"
    )
    print(f"  - {language_prefix}_repos_large.json ({categories['large'].count} repos)")
    print(f"  - {language_prefix}_repos_summary.json (categorization summary)")
    print(f"{'=' * 60}")


# ============ Pytest Result Parser ============


class PytestResultParser:
    """A unified pytest result parser."""

    @staticmethod
    def parse(
        stdout: str, stderr: str, returncode: int, repo: str, image_tag: str
    ) -> PytestResult:
        """
        Parse pytest output.

        Args:
            stdout: pytest stdout
            stderr: pytest stderr
            returncode: pytest exit code
            repo: Repository name
            image_tag: Image tag

        Returns:
            PytestResult object
        """
        # Determine status and validity
        status = "unknown"
        is_valid = False

        if returncode == 0:
            status = "passed"
            is_valid = True
        elif returncode == 1:
            status = "tests_failed"
            is_valid = True
        elif returncode == 5:
            status = "no_tests"
            is_valid = False
        else:
            status = "error"
            is_valid = False

        # Extract ERROR files (collection errors)
        error_files = re.findall(r"ERROR\s+([\w/\._-]+\.py)", stdout)
        error_files = list(set(error_files))  # Deduplicate

        # Extract PASSED tests (requires -v output)
        # Format: tests/test_foo.py::test_bar PASSED
        passed_tests = re.findall(r"([\w/\.:_-]+)\s+PASSED", stdout)
        passed_tests = list(set(passed_tests))

        # Extract FAILED tests
        # Format: tests/test_foo.py::test_baz FAILED
        failed_tests = re.findall(r"([\w/\.:_-]+)\s+FAILED", stdout)
        failed_tests = list(set(failed_tests))

        # Build filter command (exclude ERROR files)
        final_test_cmd = PytestResultParser._build_filter_cmd(error_files)

        return PytestResult(
            repo=repo,
            image_tag=image_tag,
            returncode=returncode,
            status=status,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            error_files=error_files,
            stdout=stdout,
            stderr=stderr,
            is_valid=is_valid,
            final_test_cmd=final_test_cmd,
        )

    @staticmethod
    def _build_filter_cmd(error_files: List[str]) -> List[str]:
        """Build a pytest filter command to exclude ERROR files."""
        if not error_files:
            return ["pytest"]

        not_expressions = []
        for error_file in error_files:
            # Extract file name from path (without .py)
            name = error_file.split("/")[-1].replace(".py", "")
            not_expressions.append(f"not {name}")

        filter_str = " and ".join(not_expressions)
        return ["pytest", "-k", filter_str]


# ============ Path Manager ============


class PathManager:
    """Unified input/output path management."""

    def __init__(self, base_output_dir: Path):
        self.base = Path(base_output_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def repos_json(self) -> Path:
        """Step 1 output: repository list."""
        return self.base / "1_repos.json"

    def build_results_dir(self) -> Path:
        """Step 2 output directory."""
        d = self.base / "2_build_results"
        d.mkdir(exist_ok=True)
        return d

    def build_summary_json(self) -> Path:
        """Step 2 output: build summary."""
        return self.build_results_dir() / "summary.json"

    def logs_dir(self) -> Path:
        """Logs directory."""
        d = self.build_results_dir() / "logs"
        d.mkdir(exist_ok=True)
        return d

    def repo_log_dir(self, repo_name: str) -> Path:
        """Log directory for a single repository."""
        safe_name = repo_name.replace("/", "__")
        d = self.logs_dir() / safe_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def final_dataset_json(self) -> Path:
        """Step 3 output: final dataset."""
        return self.base / "3_final_dataset.json"

    def pipeline_log(self) -> Path:
        """Pipeline log file."""
        return self.base / "pipeline.log"
