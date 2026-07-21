"""Host-owned source-integrity guard for the ablation test gate."""
from __future__ import annotations

import hashlib
import os
import re
import shlex
from pathlib import Path


_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "out",
        "output",
        "outputs",
        "target",
        "venv",
    }
)
_PROTECTED_SUFFIXES = frozenset(
    {
        ".bash",
        ".c",
        ".cc",
        ".cfg",
        ".cjs",
        ".cpp",
        ".cs",
        ".cxx",
        ".dart",
        ".erl",
        ".ex",
        ".exs",
        ".fish",
        ".fs",
        ".fsx",
        ".go",
        ".gradle",
        ".h",
        ".hh",
        ".hpp",
        ".hrl",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".mjs",
        ".php",
        ".py",
        ".pyi",
        ".pyx",
        ".R",
        ".r",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".vue",
        ".xml",
        ".yaml",
        ".yml",
        ".zsh",
    }
)
_PROTECTED_NAMES = frozenset(
    {
        ".coveragerc",
        ".npmrc",
        "CMakeLists.txt",
        "Cargo.lock",
        "Dockerfile",
        "Gemfile",
        "Gemfile.lock",
        "Makefile",
        "Pipfile",
        "Pipfile.lock",
        "go.mod",
        "go.sum",
        "gradlew",
        "mvnw",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "requirements-dev.txt",
        "requirements-test.txt",
        "requirements.txt",
        "tox.ini",
        "uv.lock",
        "yarn.lock",
    }
)
_HASH_LINE_RE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")
_PROTECTED_SUFFIXES_LOWER = frozenset(
    suffix.lower() for suffix in _PROTECTED_SUFFIXES
)


def _protected(path: Path) -> bool:
    return (
        path.name in _PROTECTED_NAMES
        or path.suffix.lower() in _PROTECTED_SUFFIXES_LOWER
    )


def collect_source_manifest(
    repo: str | Path,
    *,
    max_files: int = 50_000,
) -> dict[str, str]:
    """Hash the pre-existing source, tests, and build/test configuration."""

    root = Path(repo).expanduser().resolve()
    manifest: dict[str, str] = {}
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in _SKIP_DIRS
        )
        for name in sorted(files):
            path = Path(current) / name
            if path.is_symlink() or not _protected(path):
                continue
            relative = path.relative_to(root).as_posix()
            if "\n" in relative or "\r" in relative:
                raise ValueError(
                    f"source-integrity guard cannot represent path: {relative!r}"
                )
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                raise ValueError(
                    f"cannot hash protected repository file {relative}: {exc}"
                ) from exc
            manifest[relative] = digest
            if len(manifest) > max_files:
                raise ValueError(
                    f"source-integrity guard exceeds {max_files} protected files"
                )
    return manifest


def _container_manifest_command() -> str:
    skip = " -o ".join(
        f"-name {shlex.quote(name)}" for name in sorted(_SKIP_DIRS)
    )
    patterns = [
        *(f"*.{suffix.lstrip('.')}" for suffix in sorted(_PROTECTED_SUFFIXES)),
        *sorted(_PROTECTED_NAMES),
    ]
    names = " -o ".join(
        f"-name {shlex.quote(pattern)}" for pattern in patterns
    )
    return (
        "find . "
        f"\\( -type d \\( {skip} \\) -prune \\) -o "
        f"\\( -type f \\( {names} \\) -exec sha256sum -- {{}} + \\)"
    )


def read_container_source_manifest(sandbox) -> tuple[dict[str, str] | None, str]:
    """Read the protected-file manifest from the live container."""

    command = _container_manifest_command()
    rc, output = sandbox.exec_readonly(command)
    if rc != 0:
        return None, (
            "source-integrity scan failed "
            f"(rc={rc}); command={command}\n{output}"
        )
    manifest: dict[str, str] = {}
    for raw_line in (output or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        match = _HASH_LINE_RE.fullmatch(line)
        if match is None:
            return None, f"source-integrity scan returned an unparseable line: {line!r}"
        relative = match.group(2)
        if relative.startswith("./"):
            relative = relative[2:]
        if relative in manifest:
            return None, f"source-integrity scan returned a duplicate path: {relative}"
        manifest[relative] = match.group(1).lower()
    return manifest, ""


def verify_source_manifest(
    sandbox,
    expected: dict[str, str],
) -> tuple[bool, str]:
    """Compare the live container against the host-owned baseline."""

    actual, error = read_container_source_manifest(sandbox)
    if actual is None:
        return False, error
    expected_paths = set(expected)
    actual_paths = set(actual)
    added = sorted(actual_paths - expected_paths)
    removed = sorted(expected_paths - actual_paths)
    changed = sorted(
        path
        for path in expected_paths & actual_paths
        if expected[path] != actual[path]
    )
    if not (added or removed or changed):
        return True, f"protected_files={len(expected)}; source tree unchanged"

    def sample(values: list[str]) -> str:
        preview = values[:20]
        suffix = f" (+{len(values) - len(preview)} more)" if len(values) > 20 else ""
        return ", ".join(preview) + suffix

    parts = ["source/test/config integrity violation"]
    if added:
        parts.append(f"added: {sample(added)}")
    if removed:
        parts.append(f"removed: {sample(removed)}")
    if changed:
        parts.append(f"changed: {sample(changed)}")
    return False, "\n".join(parts)
