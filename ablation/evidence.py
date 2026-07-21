"""Bounded, deterministic repository evidence collection without a graph."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .models import EvidenceBundle, EvidenceItem


_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "target",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".gradle",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".cache",
        "coverage",
        "htmlcov",
        "output",
        "outputs",
    }
)

_EXACT_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "pipfile.lock",
        "poetry.lock",
        "pytest.ini",
        "tox.ini",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        ".nvmrc",
        ".node-version",
        "cargo.toml",
        "cargo.lock",
        "rust-toolchain",
        "rust-toolchain.toml",
        "go.mod",
        "go.sum",
        "go.work",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gradle.properties",
        "gradlew",
        "mvnw",
        "gemfile",
        "composer.json",
        "makefile",
        "cmakelists.txt",
        "readme.md",
        "readme.rst",
        "readme.txt",
    }
)


def _is_relevant(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1].lower()
    return (
        name in _EXACT_NAMES
        or name.startswith("requirements") and name.endswith(".txt")
        or name.startswith("dockerfile")
        or normalized.startswith(".github/workflows/")
        and name.endswith((".yml", ".yaml"))
        or normalized in {".gitlab-ci.yml", ".circleci/config.yml"}
    )


def _iter_files(repo: Path) -> Iterable[tuple[str, Path]]:
    for root, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in _SKIP_DIRS
            and not Path(root, directory).is_symlink()
        )
        root_path = Path(root)
        for filename in sorted(files):
            path = root_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(repo).as_posix()
            yield relative, path


def collect_repository_evidence(
    repo_path: str | Path,
    *,
    max_files: int = 80,
    max_file_chars: int = 24_000,
    max_total_chars: int = 160_000,
    max_tree_entries: int = 500,
    max_scanned_files: int = 20_000,
) -> EvidenceBundle:
    """Collect an auditable flat evidence bundle.

    This function never resolves dependencies, creates nodes, or infers edges.
    It only snapshots selected repository-owned text files plus a bounded tree.
    """

    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"repository path is not a directory: {repo}")

    tree_lines: list[str] = []
    relevant_files: list[tuple[str, Path]] = []
    scanned = 0
    scan_truncated = False
    for relative, path in _iter_files(repo):
        scanned += 1
        if len(tree_lines) < max_tree_entries:
            tree_lines.append(relative)
        if _is_relevant(relative):
            relevant_files.append((relative, path))
        if scanned >= max_scanned_files:
            scan_truncated = True
            break
    if scanned > max_tree_entries or scan_truncated:
        suffix = (
            f"... [inventory truncated after {scanned} files]"
            if scan_truncated
            else f"... [{scanned - max_tree_entries} more files]"
        )
        tree_lines.append(suffix)
    tree_text = "\n".join(tree_lines)
    tree_limit = max(512, min(40_000, max_total_chars // 4))
    if len(tree_text) > tree_limit:
        tree_text = tree_text[: max(0, tree_limit - 32)] + "\n...[tree truncated]"

    bundle = EvidenceBundle(
        (
            EvidenceItem(
                "host.repo_tree",
                "bounded repository inventory",
                tree_text,
            ),
        ),
        max_render_chars=max_total_chars,
    )
    total = len(bundle.items[0].content)
    selected = 0

    for relative, path in relevant_files:
        if selected >= max_files or total >= max_total_chars:
            break
        if not _is_relevant(relative):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4_096]:
            continue
        text = raw.decode("utf-8", errors="replace")
        remaining = max_total_chars - total
        limit = min(max_file_chars, remaining)
        if limit <= 0:
            break
        if len(text) > limit:
            text = text[: max(0, limit - 32)] + "\n...[file truncated]"
        item = EvidenceItem(
            evidence_id=f"file:{relative}",
            source=relative,
            content=text,
        )
        bundle = bundle.with_item(item)
        total += len(text)
        selected += 1
    return bundle


def add_host_evidence(
    bundle: EvidenceBundle,
    *,
    base_image: str,
    platform: str | None,
    languages: tuple[str, ...],
    test_commands: tuple[str, ...],
) -> EvidenceBundle:
    facts = (
        EvidenceItem(
            "host.base_image",
            "host-fixed experiment input",
            f"image={base_image}\nplatform={platform or 'default'}",
        ),
        EvidenceItem(
            "host.languages",
            "deterministic language detection",
            "\n".join(languages) if languages else "unknown",
        ),
        EvidenceItem(
            "host.test_commands",
            "host-fixed verification commands",
            "\n".join(test_commands),
        ),
    )
    for item in facts:
        bundle = bundle.with_item(item)
    return bundle


def add_runtime_evidence(
    bundle: EvidenceBundle,
    *,
    evidence_id: str,
    source: str,
    content: str,
    max_chars: int = 8_000,
) -> EvidenceBundle:
    if len(content) > max_chars:
        head = max_chars * 2 // 3
        tail = max_chars - head
        content = content[:head] + "\n...[truncated]...\n" + content[-tail:]
    return bundle.with_item(EvidenceItem(evidence_id, source, content))
