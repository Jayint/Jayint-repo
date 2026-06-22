from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PythonRequirement:
    name: str
    specifier: str = ""
    marker: str = ""
    source: str = ""
    kind: str = "dependency"
    trust: str = "high"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PythonVersionRequirement:
    specifier: str
    source: str
    trust: str = "high"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportFinding:
    import_name: str
    classification: str
    source_files: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportPackageMapping:
    import_name: str
    package_name: str
    source: str
    trust: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DependencyFailure:
    failure_type: str
    command: str = ""
    import_name: str | None = None
    package_name: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_dependency_shaped(self) -> bool:
        return self.failure_type != "not_dependency_related"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SuggestedCommand:
    command: str
    reason: str
    kind: str = "recommended"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DependencyConstraint:
    kind: str
    target: str
    specifier: str = ""
    source: str = ""
    trust: str = "medium"
    hard: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PythonCandidate:
    version: str
    source: str = ""
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PackageCandidate:
    name: str
    version: str
    requires_python: str = ""
    requires_dist: tuple[str, ...] = field(default_factory=tuple)
    upload_time: str | None = None
    has_wheel: bool = False
    yanked: bool = False
    source: str = "pypi"
    rank: int = 0

    @property
    def node_id(self) -> str:
        return f"{self.name}=={self.version}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConstraintEdge:
    kind: str
    source_node: str
    target_package: str = ""
    target_specifier: str = ""
    marker: str = ""
    hard: bool = True
    source: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConstraintGraph:
    python_candidates: list[PythonCandidate] = field(default_factory=list)
    package_candidates: dict[str, list[PackageCandidate]] = field(default_factory=dict)
    required_packages: list[str] = field(default_factory=list)
    edges: list[ConstraintEdge] = field(default_factory=list)
    soft_edges: list[ConstraintEdge] = field(default_factory=list)
    blocked_assignments: list[dict[str, str]] = field(default_factory=list)
    provenance: list[dict[str, str]] = field(default_factory=list)

    def all_edges(self, include_soft: bool = True) -> list[ConstraintEdge]:
        if include_soft:
            return list(self.edges) + list(self.soft_edges)
        return list(self.edges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "python_candidates": [item.to_dict() for item in self.python_candidates],
            "package_candidates": {
                name: [candidate.to_dict() for candidate in candidates]
                for name, candidates in sorted(self.package_candidates.items())
            },
            "required_packages": list(self.required_packages),
            "edges": [item.to_dict() for item in self.edges],
            "soft_edges": [item.to_dict() for item in self.soft_edges],
            "blocked_assignments": list(self.blocked_assignments),
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True)
class SolverResult:
    status: str
    selected_python: str | None = None
    selected_packages: dict[str, str] = field(default_factory=dict)
    install_commands: tuple[str, ...] = field(default_factory=tuple)
    verification_commands: tuple[str, ...] = field(default_factory=tuple)
    relaxed_soft_constraints: tuple[str, ...] = field(default_factory=tuple)
    explanation: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)
    unsat_core: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DependencyReport:
    status: str
    failure_type: str
    command: str = ""
    import_name: str | None = None
    package_name: str | None = None
    confidence: str = "medium"
    package_mapper: tuple[str, ...] = field(default_factory=tuple)
    constraints: tuple[DependencyConstraint, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)
    recommended_commands: tuple[str, ...] = field(default_factory=tuple)
    verification_commands: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PythonDependencyEvidence:
    repo_path: str
    python_requires: list[PythonVersionRequirement] = field(default_factory=list)
    declared_dependencies: list[PythonRequirement] = field(default_factory=list)
    constraint_dependencies: list[PythonRequirement] = field(default_factory=list)
    imports: list[ImportFinding] = field(default_factory=list)
    import_package_mappings: list[ImportPackageMapping] = field(default_factory=list)
    project_local_modules: list[str] = field(default_factory=list)
    pydeps: dict[str, Any] = field(default_factory=dict)
    collection_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        import_groups: dict[str, list[str]] = {}
        for finding in self.imports:
            import_groups.setdefault(finding.classification, []).append(finding.import_name)

        return {
            "repo_path": self.repo_path,
            "python_requires": [item.to_dict() for item in self.python_requires],
            "declared_dependencies": [item.to_dict() for item in self.declared_dependencies],
            "constraint_dependencies": [item.to_dict() for item in self.constraint_dependencies],
            "imports": [item.to_dict() for item in self.imports],
            "import_graph": {
                key: sorted(set(values))
                for key, values in sorted(import_groups.items())
            },
            "import_package_mappings": [
                item.to_dict() for item in self.import_package_mappings
            ],
            "project_local_modules": sorted(set(self.project_local_modules)),
            "pydeps": dict(self.pydeps),
            "collection_errors": list(self.collection_errors),
        }
