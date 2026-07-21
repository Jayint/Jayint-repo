"""Graph-free discovery of fixed test commands for the ablation."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "target", "dist", "build"}
)


def _node_test_commands(repo: Path) -> list[str]:
    commands: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(directory for directory in dirs if directory not in _SKIP_DIRS)
        relative_root = Path(root).relative_to(repo)
        if len(relative_root.parts) > 3:
            dirs[:] = []
            continue
        if "package.json" not in files:
            continue
        path = Path(root) / "package.json"
        try:
            package = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        script = str((package.get("scripts") or {}).get("test") or "")
        if not script or "no test specified" in script.lower():
            continue
        names = set(files)
        manager = (
            "pnpm"
            if "pnpm-lock.yaml" in names
            else "yarn"
            if "yarn.lock" in names
            else "npm"
        )
        command = f"{manager} test"
        if relative_root != Path("."):
            command = f"cd {relative_root.as_posix()} && {command}"
        commands.append(command)
    return commands


def discover_test_commands(
    repo_path: str | Path,
    languages: tuple[str, ...],
    *,
    primary_language: str | None = None,
) -> tuple[str, ...]:
    """Return stable test commands without importing ecosystem graph providers.

    An explicit primary-language hint is authoritative for test discovery. The
    image selector may report auxiliary languages found in tooling or examples;
    treating all of them as test ecosystems can create an invalid cross-language
    command for an otherwise single-language benchmark row.
    """

    repo = Path(repo_path).expanduser().resolve()
    primary = (primary_language or "").strip().lower()
    detected = (
        (primary,)
        if primary
        else tuple(dict.fromkeys(language.lower() for language in languages))
    )
    commands: list[str] = []

    if "python" in detected:
        commands.append("python -m pytest -q")
    if any(language in detected for language in ("javascript", "typescript")):
        commands.extend(_node_test_commands(repo))
    if "rust" in detected:
        commands.append("cargo test --all-targets")
    if "go" in detected:
        commands.append("go test ./...")
    if any(language in detected for language in ("java", "kotlin", "scala")):
        if (repo / "mvnw").exists():
            commands.append("./mvnw -B test")
        elif (repo / "pom.xml").exists():
            commands.append("mvn -B test")
        elif (repo / "gradlew").exists():
            commands.append("./gradlew test")
        elif (repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists():
            commands.append("gradle test")
    if "c#" in detected:
        commands.append("dotnet test")
    if "ruby" in detected:
        commands.append("bundle exec rake test")
    if "php" in detected:
        commands.append("vendor/bin/phpunit")

    if not commands:
        if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
            commands.append("python -m pytest -q")
        elif (repo / "package.json").exists():
            commands.extend(_node_test_commands(repo))

    return tuple(dict.fromkeys(command for command in commands if command.strip()))


def validate_fixed_test_commands(commands: tuple[str, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    if not commands:
        errors.append("at least one fixed test command is required")
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            errors.append("test commands must be non-empty strings")
            continue
        if "\n" in command or "\r" in command:
            errors.append("multi-line test commands are not allowed")
        if re.search(r"(?:^|\s)--(?:ignore|ignore-glob|deselect)(?:=|\s|$)", command):
            errors.append(f"test exclusion flags are not allowed: {command}")
        if re.search(r"\|\s*(?:head|tail|grep)\b", command):
            errors.append(f"test output truncation/filtering is not allowed: {command}")
        if re.search(r"\|\|\s*(?:true|:)\b|set\s+\+e\b", command):
            errors.append(f"test failure masking is not allowed: {command}")
    return tuple(dict.fromkeys(errors))
