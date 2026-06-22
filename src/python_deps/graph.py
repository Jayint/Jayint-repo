from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .import_mapping import normalize_package_name
from .models import (
    ConstraintEdge,
    ConstraintGraph,
    DependencyConstraint,
    DependencyReport,
    PackageCandidate,
    PythonCandidate,
)
from .pypi_metadata import (
    PyPIMetadataClient,
    marker_applies_to_python,
    parse_requires_dist,
    python_satisfies_specifier,
)


DEFAULT_PYTHON_CANDIDATES = ("3.11", "3.10", "3.9", "3.8")


def build_constraint_graph(
    diagnostics: Mapping[str, object],
    *,
    latest_report: DependencyReport | Mapping[str, object] | None = None,
    metadata_client: PyPIMetadataClient | Any | None = None,
    max_packages: int = 20,
    max_versions_per_package: int = 8,
    enable_network: bool = True,
) -> ConstraintGraph:
    python_candidates = _python_candidates_from_diagnostics(diagnostics)
    seeds, declared_specifiers, conditional_markers = _seed_packages(
        diagnostics,
        latest_report,
        python_candidates,
    )
    seed_packages = list(seeds.keys())[:max_packages]
    required_packages = [
        package
        for package in seed_packages
        if package not in conditional_markers
    ]

    client = metadata_client
    if client is None and enable_network:
        client = PyPIMetadataClient()

    package_candidates: dict[str, list[PackageCandidate]] = {}
    provenance: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for package_name in seed_packages:
        preferred = _preferred_versions(declared_specifiers.get(package_name, []))
        candidates = _fetch_candidates(
            client,
            package_name,
            python_candidates,
            max_versions_per_package=max_versions_per_package,
            preferred_versions=preferred,
            required_specifiers=[
                specifier
                for specifier, _, _, marker in declared_specifiers.get(package_name, [])
                if specifier
                and _marker_applies_to_any_python(marker, python_candidates)
            ],
        )
        if candidates:
            package_candidates[package_name] = candidates
            provenance.append({"package": package_name, "source": seeds[package_name]})
        else:
            errors.append({"package": package_name, "error": "no candidates available"})

    edges: list[ConstraintEdge] = []
    soft_edges: list[ConstraintEdge] = []

    for package_name, specifiers in declared_specifiers.items():
        for specifier, source, hard, marker in specifiers:
            target = package_name
            edge = ConstraintEdge(
                kind="declared_specifier",
                source_node=target,
                target_package=target,
                target_specifier=specifier,
                marker=marker,
                hard=hard,
                source=source,
                reason=f"{source} requires {target}{specifier}{_marker_reason(marker)}",
            )
            if hard:
                edges.append(edge)
            else:
                soft_edges.append(edge)

    for package_name, markers in conditional_markers.items():
        if package_name in required_packages:
            continue
        for marker in sorted(markers):
            edges.append(
                ConstraintEdge(
                    kind="include_package",
                    source_node=package_name,
                    target_package=package_name,
                    marker=marker,
                    hard=True,
                    source="pep508.marker",
                    reason=f"{package_name} is required when {marker}",
                )
            )

    _add_phase3_edges(edges, soft_edges, latest_report)
    _add_metadata_edges(edges, package_candidates, python_candidates)
    _expand_transitive_layers(
        edges,
        package_candidates,
        python_candidates,
        client,
        max_total_packages=max_packages,
        max_versions_per_package=max_versions_per_package,
    )

    return ConstraintGraph(
        python_candidates=python_candidates,
        package_candidates=package_candidates,
        required_packages=[package for package in required_packages if package in package_candidates],
        edges=edges,
        soft_edges=soft_edges,
        blocked_assignments=_blocked_assignments_from_diagnostics(diagnostics),
        provenance=provenance + errors,
    )


def _python_candidates_from_diagnostics(diagnostics: Mapping[str, object]) -> list[PythonCandidate]:
    specifiers = [
        item.get("specifier", "")
        for item in diagnostics.get("python_requires", []) or []
        if isinstance(item, dict)
    ]
    candidates = []
    for version in DEFAULT_PYTHON_CANDIDATES:
        if all(python_satisfies_specifier(version, specifier) for specifier in specifiers):
            candidates.append(PythonCandidate(version=version, source="default+requires-python", rank=len(candidates)))
    return candidates


def _seed_packages(
    diagnostics: Mapping[str, object],
    latest_report: DependencyReport | Mapping[str, object] | None,
    python_candidates: list[PythonCandidate],
) -> tuple[
    OrderedDict[str, str],
    dict[str, list[tuple[str, str, bool, str]]],
    dict[str, set[str]],
]:
    seeds: OrderedDict[str, str] = OrderedDict()
    declared_specifiers: dict[str, list[tuple[str, str, bool, str]]] = {}
    optional_specifiers: dict[str, list[tuple[str, str, bool, str]]] = {}
    conditional_markers: dict[str, set[str]] = {}
    unconditional_packages: set[str] = set()
    runtime_required_packages: set[str] = set()

    for item in diagnostics.get("declared_dependencies", []) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        package_name = normalize_package_name(str(item["name"]))
        marker = str(item.get("marker") or "").strip()
        if marker and not _marker_applies_to_any_python(marker, python_candidates):
            continue
        specifier = str(item.get("specifier") or "").strip()
        specifier_tuple = (
            specifier,
            str(item.get("source") or "declared_dependency"),
            item.get("trust", "high") == "high",
            marker,
        )
        if item.get("kind") == "optional_dependency":
            if specifier:
                optional_specifiers.setdefault(package_name, []).append(specifier_tuple)
            continue

        seeds.setdefault(package_name, item.get("source", "declared_dependency"))
        if marker and package_name not in unconditional_packages:
            conditional_markers.setdefault(package_name, set()).add(marker)
        elif not marker:
            unconditional_packages.add(package_name)
            conditional_markers.pop(package_name, None)
        if specifier:
            declared_specifiers.setdefault(package_name, []).append(specifier_tuple)

    for item in diagnostics.get("constraint_dependencies", []) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        package_name = normalize_package_name(str(item["name"]))
        marker = str(item.get("marker") or "").strip()
        if marker and not _marker_applies_to_any_python(marker, python_candidates):
            continue
        seeds.setdefault(package_name, item.get("source", "constraint_dependency"))
        if marker and package_name not in unconditional_packages:
            conditional_markers.setdefault(package_name, set()).add(marker)
        elif not marker:
            unconditional_packages.add(package_name)
            conditional_markers.pop(package_name, None)
        specifier = str(item.get("specifier") or "").strip()
        if specifier:
            declared_specifiers.setdefault(package_name, []).append(
                (specifier, str(item.get("source") or "constraint_dependency"), True, marker)
            )

    report_dict = _report_to_dict(latest_report)
    package_name = report_dict.get("package_name")
    if package_name and report_dict.get("failure_type") != "dependency_conflict":
        target = normalize_package_name(str(package_name))
        seeds.setdefault(target, "latest_dependency_report")
        runtime_required_packages.add(target)
    for constraint in report_dict.get("constraints", []) or []:
        if not isinstance(constraint, dict):
            continue
        if constraint.get("kind") in {"include_package", "version_specifier", "block_unavailable_candidate"}:
            target = normalize_package_name(str(constraint.get("target") or ""))
            if target:
                seeds.setdefault(target, f"phase3:{constraint.get('kind')}")
                runtime_required_packages.add(target)
                unconditional_packages.add(target)
                conditional_markers.pop(target, None)
                if constraint.get("kind") == "version_specifier" and constraint.get("specifier"):
                    declared_specifiers.setdefault(target, []).append(
                        (
                            str(constraint["specifier"]),
                            str(constraint.get("source") or "phase3"),
                            bool(constraint.get("hard")),
                            "",
                        )
                    )
    for package_name in sorted(runtime_required_packages):
        for specifier_tuple in optional_specifiers.get(package_name, []):
            declared_specifiers.setdefault(package_name, []).append(specifier_tuple)
    return seeds, declared_specifiers, conditional_markers


def _fetch_candidates(
    client: Any,
    package_name: str,
    python_candidates: list[PythonCandidate],
    *,
    max_versions_per_package: int,
    preferred_versions: set[str],
    required_specifiers: list[str] | None = None,
) -> list[PackageCandidate]:
    if client is None:
        return []
    python_versions = [candidate.version for candidate in python_candidates]
    try:
        return list(
            client.get_package_candidates(
                package_name,
                python_versions=python_versions,
                max_versions=max_versions_per_package,
                preferred_versions=preferred_versions,
                required_specifiers=required_specifiers or [],
            )
        )
    except Exception:
        return []


def _preferred_versions(specifiers: list[tuple[str, str, bool, str]]) -> set[str]:
    preferred = set()
    for specifier, _, _, marker in specifiers:
        if marker:
            continue
        if specifier.startswith("=="):
            preferred.add(specifier[2:].strip())
    return preferred


def _marker_applies_to_any_python(marker: str, python_candidates: list[PythonCandidate]) -> bool:
    if not marker:
        return True
    return any(
        marker_applies_to_python(marker, candidate.version)
        for candidate in python_candidates
    )


def _marker_reason(marker: str) -> str:
    return f"; {marker}" if marker else ""


def _add_phase3_edges(
    edges: list[ConstraintEdge],
    soft_edges: list[ConstraintEdge],
    latest_report: DependencyReport | Mapping[str, object] | None,
) -> None:
    for constraint in (_report_to_dict(latest_report).get("constraints", []) or []):
        if not isinstance(constraint, dict):
            continue
        kind = constraint.get("kind")
        target = normalize_package_name(str(constraint.get("target") or ""))
        if not target:
            continue
        if kind == "block_unavailable_candidate":
            edge = ConstraintEdge(
                kind="block_candidate",
                source_node=target,
                target_package=target,
                target_specifier=str(constraint.get("specifier") or ""),
                hard=True,
                source=str(constraint.get("source") or "phase3"),
                reason=str(constraint.get("reason") or "blocked by runtime failure"),
            )
            edges.append(edge)
        elif kind == "version_specifier":
            edge = ConstraintEdge(
                kind="declared_specifier",
                source_node=target,
                target_package=target,
                target_specifier=str(constraint.get("specifier") or ""),
                hard=bool(constraint.get("hard")),
                source=str(constraint.get("source") or "phase3"),
                reason=str(constraint.get("reason") or "runtime version constraint"),
            )
            (edges if edge.hard else soft_edges).append(edge)


def _add_metadata_edges(
    edges: list[ConstraintEdge],
    package_candidates: dict[str, list[PackageCandidate]],
    python_candidates: list[PythonCandidate],
) -> None:
    for package_name, candidates in list(package_candidates.items()):
        for candidate in candidates:
            if candidate.requires_python:
                edges.append(
                    ConstraintEdge(
                        kind="requires_python",
                        source_node=candidate.node_id,
                        target_package="python",
                        target_specifier=candidate.requires_python,
                        hard=True,
                        source="pypi.requires_python",
                        reason=f"{candidate.node_id} supports Python {candidate.requires_python}",
                    )
                )


def _expand_transitive_layers(
    edges: list[ConstraintEdge],
    package_candidates: dict[str, list[PackageCandidate]],
    python_candidates: list[PythonCandidate],
    client: Any,
    *,
    max_total_packages: int,
    max_versions_per_package: int,
) -> None:
    if client is None:
        return
    marker_pythons = [candidate.version for candidate in python_candidates] or ["3.11"]
    while len(package_candidates) < max_total_packages:
        dependency_specifiers: dict[str, set[str]] = {}
        for candidates in package_candidates.values():
            for candidate in candidates:
                for marker_python in marker_pythons:
                    for dependency_name, specifier in parse_requires_dist(candidate.requires_dist, marker_python):
                        if dependency_name not in package_candidates:
                            dependency_specifiers.setdefault(dependency_name, set()).add(specifier)

        if not dependency_specifiers:
            return

        added = False
        for dependency_name, specifiers in sorted(dependency_specifiers.items()):
            if len(package_candidates) >= max_total_packages:
                return
            candidates = _fetch_candidates(
                client,
                dependency_name,
                python_candidates,
                max_versions_per_package=max_versions_per_package,
                preferred_versions=set(),
                required_specifiers=sorted(specifier for specifier in specifiers if specifier),
            )
            if not candidates:
                continue
            package_candidates[dependency_name] = candidates
            _add_metadata_edges(edges, {dependency_name: candidates}, python_candidates)
            added = True

        if not added:
            return


def _blocked_assignments_from_diagnostics(diagnostics: Mapping[str, object]) -> list[dict[str, str]]:
    blocked = diagnostics.get("blocked_assignments")
    if not isinstance(blocked, list):
        return []
    return [item for item in blocked if isinstance(item, dict)]


def _report_to_dict(report: DependencyReport | Mapping[str, object] | None) -> dict[str, Any]:
    if report is None:
        return {}
    if isinstance(report, DependencyReport):
        return report.to_dict()
    if isinstance(report, Mapping):
        return dict(report)
    return {}


def candidate_satisfies_specifier(candidate: PackageCandidate, specifier: str) -> bool:
    if not specifier:
        return True
    try:
        return Version(candidate.version) in SpecifierSet(specifier)
    except (InvalidVersion, InvalidSpecifier):
        return True


def parse_dependency_string(requirement_text: str) -> tuple[str, str] | None:
    try:
        requirement = Requirement(requirement_text)
    except InvalidRequirement:
        return None
    return normalize_package_name(requirement.name), str(requirement.specifier)
