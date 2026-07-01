from __future__ import annotations

import ast
import configparser
import glob
import os
import re
from pathlib import Path
from typing import Iterable

from packaging.requirements import InvalidRequirement, Requirement

from .import_graph import collect_pydeps_evidence, scan_imports
from .import_mapping import map_import_to_package
from .models import (
    ImportPackageMapping,
    PythonDependencyEvidence,
    PythonRequirement,
    PythonVersionRequirement,
)

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None


def collect_python_dependency_evidence(repo_path: str | Path) -> PythonDependencyEvidence:
    root = Path(repo_path)
    evidence = PythonDependencyEvidence(repo_path=str(root))

    collectors = (
        _collect_pyproject_metadata,
        _collect_setup_cfg_metadata,
        _collect_setup_py_metadata,
        _collect_requirements_files,
        _collect_constraints_files,
    )
    for collector in collectors:
        try:
            collector(root, evidence)
        except Exception as error:  # Evidence collection must not abort an agent run.
            evidence.collection_errors.append(f"{collector.__name__}: {error}")

    try:
        imports, project_local_modules, import_errors = scan_imports(root)
        evidence.imports.extend(imports)
        evidence.project_local_modules.extend(project_local_modules)
        evidence.collection_errors.extend(import_errors)
    except Exception as error:
        evidence.collection_errors.append(f"scan_imports: {error}")

    evidence.pydeps = collect_pydeps_evidence(root)
    evidence.import_package_mappings.extend(_build_import_mappings(evidence))
    return evidence


def _collect_pyproject_metadata(root: Path, evidence: PythonDependencyEvidence) -> None:
    path = root / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    requires_python = project.get("requires-python")
    if isinstance(requires_python, str) and requires_python.strip():
        evidence.python_requires.append(
            PythonVersionRequirement(
                specifier=requires_python.strip(),
                source="pyproject.toml:project.requires-python",
            )
        )
    for requirement in project.get("dependencies", []) or []:
        _add_requirement_line(evidence.declared_dependencies, requirement, "pyproject.toml:project.dependencies")
    optional_dependencies = project.get("optional-dependencies", {}) or {}
    if isinstance(optional_dependencies, dict):
        for group, requirements in optional_dependencies.items():
            for requirement in requirements or []:
                _add_requirement_line(
                    evidence.declared_dependencies,
                    requirement,
                    f"pyproject.toml:project.optional-dependencies.{group}",
                    kind="optional_dependency",
                    trust="medium",
                )

    poetry_dependencies = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    if isinstance(poetry_dependencies, dict):
        for name, value in poetry_dependencies.items():
            if name.lower() == "python":
                if isinstance(value, str):
                    evidence.python_requires.append(
                        PythonVersionRequirement(
                            specifier=value,
                            source="pyproject.toml:tool.poetry.dependencies.python",
                        )
                    )
                continue
            specifier = value if isinstance(value, str) else ""
            evidence.declared_dependencies.append(
                PythonRequirement(
                    name=name,
                    specifier=specifier,
                    source="pyproject.toml:tool.poetry.dependencies",
                )
            )


def _collect_setup_cfg_metadata(root: Path, evidence: PythonDependencyEvidence) -> None:
    path = root / "setup.cfg"
    if not path.is_file():
        return
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if parser.has_option("options", "python_requires"):
        evidence.python_requires.append(
            PythonVersionRequirement(
                specifier=parser.get("options", "python_requires").strip(),
                source="setup.cfg:options.python_requires",
            )
        )
    if parser.has_option("options", "install_requires"):
        for line in _split_multiline_value(parser.get("options", "install_requires")):
            _add_requirement_line(evidence.declared_dependencies, line, "setup.cfg:options.install_requires")
    if parser.has_section("options.extras_require"):
        for group, value in parser.items("options.extras_require"):
            for line in _split_multiline_value(value):
                _add_requirement_line(
                    evidence.declared_dependencies,
                    line,
                    f"setup.cfg:options.extras_require.{group}",
                    kind="optional_dependency",
                    trust="medium",
                )


def _collect_setup_py_metadata(root: Path, evidence: PythonDependencyEvidence) -> None:
    path = root / "setup.py"
    if not path.is_file():
        return
    content = path.read_text(encoding="utf-8")
    if len(content) > 250_000:
        evidence.collection_errors.append("setup.py: skipped metadata parse because file is too large")
        return
    try:
        tree = ast.parse(content)
    except SyntaxError as error:
        evidence.collection_errors.append(f"setup.py: syntax error while parsing metadata: {error}")
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if func_name != "setup":
            continue
        for keyword in node.keywords:
            if keyword.arg == "python_requires":
                value = _literal_string(keyword.value)
                if value:
                    evidence.python_requires.append(
                        PythonVersionRequirement(
                            specifier=value,
                            source="setup.py:setup.python_requires",
                        )
                    )
            elif keyword.arg == "install_requires":
                for requirement in _literal_string_list(keyword.value):
                    _add_requirement_line(
                        evidence.declared_dependencies,
                        requirement,
                        "setup.py:setup.install_requires",
                    )
            elif keyword.arg == "extras_require":
                for group, requirements in _literal_extras_require(keyword.value).items():
                    for requirement in requirements:
                        _add_requirement_line(
                            evidence.declared_dependencies,
                            requirement,
                            f"setup.py:setup.extras_require.{group}",
                            kind="optional_dependency",
                            trust="medium",
                        )


def _collect_requirements_files(root: Path, evidence: PythonDependencyEvidence) -> None:
    for path in _glob_metadata_files(root, "requirements*.txt"):
        for line in _read_requirement_lines(path):
            _add_requirement_line(
                evidence.declared_dependencies,
                line,
                _relative_source(root, path),
            )


def _collect_constraints_files(root: Path, evidence: PythonDependencyEvidence) -> None:
    for path in _glob_metadata_files(root, "constraints*.txt"):
        for line in _read_requirement_lines(path):
            _add_requirement_line(
                evidence.constraint_dependencies,
                line,
                _relative_source(root, path),
                kind="constraint",
            )


def _build_import_mappings(evidence: PythonDependencyEvidence) -> list[ImportPackageMapping]:
    declared_package_names = {
        requirement.name for requirement in evidence.declared_dependencies if requirement.name
    }
    mappings: list[ImportPackageMapping] = []
    for finding in evidence.imports:
        if finding.classification != "external":
            continue
        mapping = map_import_to_package(finding.import_name, declared_package_names)
        mappings.append(
            ImportPackageMapping(
                import_name=mapping.import_name,
                package_name=mapping.package_name,
                source=mapping.source,
                trust=mapping.trust,
            )
        )
    return sorted(mappings, key=lambda item: item.import_name)


def _glob_metadata_files(root: Path, pattern: str) -> list[Path]:
    matches = [
        Path(path)
        for path in glob.glob(str(root / pattern))
        if Path(path).is_file()
    ]
    return sorted(matches)


def _read_requirement_lines(path: Path) -> Iterable[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")
    for raw_line in content.splitlines():
        line = _strip_inline_comment(raw_line).strip()
        if not line or line.startswith(("-", "--")):
            continue
        yield line


def _add_requirement_line(
    target: list[PythonRequirement],
    line: object,
    source: str,
    *,
    kind: str = "dependency",
    trust: str = "high",
) -> None:
    if not isinstance(line, str):
        return
    parsed = _parse_requirement_line(line)
    if not parsed:
        return
    name, specifier, marker, extras = parsed
    target.append(
        PythonRequirement(
            name=name,
            specifier=specifier,
            marker=marker,
            extras=extras,
            source=source,
            kind=kind,
            trust=trust,
        )
    )


def _parse_requirement_line(line: str) -> tuple[str, str, str, tuple[str, ...]] | None:
    cleaned = _strip_inline_comment(line).strip()
    if not cleaned or cleaned.startswith(("-", "--")):
        return None
    if "://" in cleaned or cleaned.startswith(("git+", "hg+", "svn+")):
        egg_match = re.search(r"[#&]egg=([A-Za-z0-9_.-]+)", cleaned)
        if egg_match:
            return egg_match.group(1), cleaned, "", ()
        return None
    try:
        requirement = Requirement(cleaned)
    except InvalidRequirement:
        return None
    marker = str(requirement.marker) if requirement.marker is not None else ""
    extras = tuple(sorted(requirement.extras))
    return requirement.name, str(requirement.specifier), marker, extras


def _strip_inline_comment(line: str) -> str:
    if " #" not in line:
        return line
    return line.split(" #", 1)[0]


def _split_multiline_value(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _literal_string(node: ast.AST) -> str | None:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _literal_string_list(node: ast.AST) -> list[str]:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def _literal_extras_require(node: ast.AST) -> dict[str, list[str]]:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for group, requirements in value.items():
        if not isinstance(group, str):
            continue
        if isinstance(requirements, str):
            result[group] = [requirements]
        elif isinstance(requirements, (list, tuple)):
            result[group] = [item for item in requirements if isinstance(item, str)]
    return result


def _relative_source(root: Path, path: Path) -> str:
    return os.path.relpath(path, root)
