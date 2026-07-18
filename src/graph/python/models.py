"""Data models for the v3 depgraph stack.

This is the verbatim subset of the original ``graph.python.models`` needed by
``evidence.py``, ``import_graph.py`` and ``failure_classifier.py`` — all
transitively reached from ``depgraph/build.py`` and ``depgraph/roots.py``.

The z3-era constraint/solver classes (DependencyConstraint, ConstraintGraph,
ConstraintEdge, SolverResult, DependencyReport, PythonCandidate,
PackageCandidate, SuggestedCommand) are excluded from the v3-only branch.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PythonRequirement:
    name: str
    specifier: str = ""
    marker: str = ""
    extras: tuple[str, ...] = ()
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
    # True when every occurrence of this import is lexically guarded — by a
    # try/except-ImportError or an ``if`` branch (an "optional" import). A hard
    # runtime need for the same name anywhere dominates and makes this False (see
    # _dedupe_findings).
    optional: bool = False
    # The attributes / from-import names the code actually uses on this top-level
    # import (e.g. ``cv2.imread`` / ``from cv2 import imread`` -> ``("imread",)``),
    # sorted and unioned across every source file and through dedupe. Usage
    # disambiguates look-alike distribution names for the install-lane LLM
    # guesser; unconsumed here (see Tasks 3-5). A bare/unused import -> ``()``.
    symbols: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportPackageMapping:
    import_name: str
    package_name: str | None
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


@dataclass
class PythonDependencyEvidence:
    repo_path: str
    python_requires: list[PythonVersionRequirement] = field(default_factory=list)
    declared_dependencies: list[PythonRequirement] = field(default_factory=list)
    constraint_dependencies: list[PythonRequirement] = field(default_factory=list)
    # Repo-relative POSIX paths of requirements files the recursive discovery
    # walk found OUTSIDE the pre-walk allowlist (repo root, or a top-level
    # requirements/tests/test/docs directory) -- see
    # `evidence._is_hard_requirements_file`. These are deliberately NOT parsed
    # into `declared_dependencies`: they never become hard resolver roots, so
    # a stray test-fixture pin (or one of many independent per-subproject
    # requirements files) can never poison the single global closure. Kept as
    # data for a later best-effort, non-blocking install
    # (`pip install -r <file> -c <closure-constraints> || true`), sorted.
    #
    # Two deliberate exceptions to what ends up here (see
    # `evidence._collect_requirements_files`): (1) a file also reachable via
    # an explicit `-r` include from a HARD file is trusted and ingested HARD
    # instead -- it is never duplicated into this list. (2) a candidate whose
    # RESOLVED (symlink-followed) location escapes the repo root is dropped
    # entirely (recorded in `collection_errors`) rather than stored as an
    # out-of-repo relative path -- every path here is guaranteed lexical
    # (leaf not resolved) AND repo-relative.
    soft_requirements_files: list[str] = field(default_factory=list)
    # The `packaging.requirements.Requirement` objects parsed from every SOFT
    # requirements file above (see `evidence._parse_requirement_lines`),
    # EXCLUDING bare `.` / `-e .` self-install and URL-only lines that carry no
    # PEP 508 name. Purely additive and unconsumed until a later repair task
    # proposes these declared dists as candidates for unresolved imports: the
    # soft/hard classification, `declared_dependencies`, and root selection are
    # all byte-unchanged by populating this. A repo with no soft files leaves
    # it empty.
    soft_declared_dependencies: list = field(default_factory=list)  # list[packaging.requirements.Requirement]
    # Repo-relative POSIX paths of the HARD requirements files
    # (`evidence._discover_hard_requirements_files`: the repo root plus the
    # top-level requirements/tests/test/docs dirs). Unlike the soft list these
    # ARE parsed into `declared_dependencies`, but their PATHS are not otherwise
    # exposed -- recorded here (mirroring `soft_requirements_files`) so a
    # downstream install-plan can `pip install -r` each one directly rather than
    # re-deriving (and narrowing) the discovery. Additive: populating this never
    # changes which requirements become declared_dependencies or their kind.
    hard_requirements_files: list[str] = field(default_factory=list)
    used_extras: set[str] = field(default_factory=set)
    imports: list[ImportFinding] = field(default_factory=list)
    import_package_mappings: list[ImportPackageMapping] = field(default_factory=list)
    project_local_modules: list[str] = field(default_factory=list)
    pydeps: dict[str, Any] = field(default_factory=dict)
    collection_errors: list[str] = field(default_factory=list)
    # Task 4 (additive, unconsumed until Task 5): the FULL `[tool.uv.sources]`
    # / `[tool.uv.workspace]` / `[[tool.uv.index]]` config, verbatim -- the
    # *where* half of a dependency declaration that a bare name/specifier
    # never carries (git commit, in-checkout workspace member, local path, or
    # private index). Keyed by canonical (PyPI-normalised) distribution name;
    # each value is a tuple because uv allows a marker-conditional LIST of
    # source tables for one name. See `graph.python.read.evidence.UvSourceConfig`.
    uv_sources: dict[str, tuple[dict, ...]] = field(default_factory=dict)
    uv_workspace_members: tuple[str, ...] = ()
    uv_indexes: tuple[dict, ...] = ()
    # Fix 1 (docs/superpowers/plans/2026-07-14-post-measurement-fixes.md): a
    # PEP 508 "direct reference" requirement line -- ``name @ <url>`` where
    # ``<url>`` is a ``git+``/``http(s)://``/``file:`` URL -- names a
    # non-PyPI source exactly like a `[tool.uv.sources]` override, just
    # through inline syntax instead of a separate TOML table. Keyed by
    # canonical (PyPI-normalised) distribution name, value is the raw URL
    # string verbatim (never discarded -- it is the evidence node's real
    # source). Kept as its own field rather than folded into ``uv_sources``
    # because a direct reference has no `[tool.uv.sources]` table
    # representation this codebase can safely rewrite/carry into the
    # synthetic pyproject; see ``build._exclude_direct_reference_roots`` for
    # the (unconditional, both-flag-states) consumer.
    direct_reference_sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        import_groups: dict[str, list[str]] = {}
        for finding in self.imports:
            import_groups.setdefault(finding.classification, []).append(finding.import_name)

        return {
            "repo_path": self.repo_path,
            "python_requires": [item.to_dict() for item in self.python_requires],
            "declared_dependencies": [item.to_dict() for item in self.declared_dependencies],
            "constraint_dependencies": [item.to_dict() for item in self.constraint_dependencies],
            "soft_requirements_files": sorted(self.soft_requirements_files),
            "hard_requirements_files": sorted(self.hard_requirements_files),
            "used_extras": sorted(self.used_extras),
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
            "uv_sources": {
                name: [dict(entry) for entry in entries]
                for name, entries in sorted(self.uv_sources.items())
            },
            "uv_workspace_members": list(self.uv_workspace_members),
            "uv_indexes": [dict(entry) for entry in self.uv_indexes],
            "direct_reference_sources": dict(sorted(self.direct_reference_sources.items())),
        }
