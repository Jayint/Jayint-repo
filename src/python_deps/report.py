from __future__ import annotations

from collections.abc import Mapping

from .constraints import infer_rule_based_constraints, render_constraint
from .failure_classifier import classify_dependency_failure
from .import_mapping import map_import_to_package, mapping_source_label
from .models import DependencyFailure, DependencyReport, PythonDependencyEvidence, SolverResult


def evidence_to_report(evidence: PythonDependencyEvidence) -> dict[str, object]:
    """Return the JSON-serializable report stored in agent run summaries."""
    return evidence.to_dict()


def build_dependency_failure_report(
    command: str,
    observation: str,
    diagnostics: Mapping[str, object] | None = None,
) -> DependencyReport | None:
    failure = classify_dependency_failure(command, observation)
    if not failure.is_dependency_shaped:
        return None

    diagnostics = diagnostics or {}
    if failure.failure_type == "module_not_found":
        return _module_not_found_report(failure, diagnostics)
    if failure.failure_type == "import_name_error":
        return _import_name_error_report(failure, diagnostics)
    if failure.failure_type == "dependency_conflict":
        return _dependency_conflict_report(failure)
    if failure.failure_type == "no_matching_distribution":
        return _no_matching_distribution_report(failure)
    if failure.failure_type == "syntax_requires_newer_python":
        return _syntax_version_report(failure)
    if failure.failure_type == "native_library_missing":
        return _native_library_report(failure)
    return _generic_dependency_report(failure)


def append_dependency_report(observation: str, report: DependencyReport) -> str:
    rendered = render_dependency_report(report)
    if not rendered:
        return observation
    if observation and observation.endswith("\n"):
        return f"{observation}\n{rendered}"
    if observation:
        return f"{observation}\n\n{rendered}"
    return rendered


def render_dependency_report(report: DependencyReport) -> str:
    lines = [
        "[Python Dependency Solver Report]",
        f"Status: {report.status}",
        f"Failure type: {report.failure_type}",
    ]
    if report.import_name:
        label = "Missing import" if report.failure_type == "module_not_found" else "Import"
        lines.append(f"{label}: {report.import_name}")
    if report.package_name:
        lines.append(f"Package: {report.package_name}")
    if report.confidence:
        lines.append(f"Confidence: {report.confidence}")
    if report.package_mapper:
        lines.append("Package Mapper:")
        lines.extend(f"- {item}" for item in report.package_mapper)
    if report.constraints:
        lines.append("Rule-based constraints:")
        lines.extend(f"- {render_constraint(item)}" for item in report.constraints)
    if report.evidence:
        lines.append("Evidence:")
        lines.extend(f"- {item}" for item in report.evidence)
    if report.recommended_commands:
        label = (
            "Recommended command"
            if len(report.recommended_commands) == 1
            else "Recommended commands"
        )
        lines.append(f"{label}:")
        lines.extend(report.recommended_commands)
    if report.verification_commands:
        lines.append("Verify:")
        lines.extend(report.verification_commands)
    if report.notes:
        lines.append("Notes:")
        lines.extend(f"- {item}" for item in report.notes)
    return "\n".join(lines)


def solver_result_to_report(
    result: SolverResult,
    *,
    command: str = "",
) -> DependencyReport:
    confidence = "high" if result.status in {"sat", "sat_soft_relaxed"} else "medium"
    notes = []
    if result.relaxed_soft_constraints:
        notes.append("Soft constraints were relaxed to produce this solution.")
    if result.errors:
        notes.extend(result.errors)
    evidence = list(result.explanation)
    if result.selected_python:
        evidence.insert(0, f"Selected Python: {result.selected_python}")
    if result.selected_packages:
        evidence.append(
            "Selected packages: "
            + ", ".join(
                f"{name}=={version}"
                for name, version in sorted(result.selected_packages.items())
            )
        )
    return DependencyReport(
        status=result.status,
        failure_type="dependency_solver",
        command=command,
        confidence=confidence,
        evidence=tuple(evidence),
        recommended_commands=tuple(result.install_commands),
        verification_commands=tuple(result.verification_commands),
        notes=tuple(notes),
    )


def initialize_diagnostics_summary(evidence: PythonDependencyEvidence) -> dict[str, object]:
    diagnostics = evidence.to_dict()
    diagnostics.setdefault("failure_reports", [])
    diagnostics["static_evidence_collected"] = True
    diagnostics["reports_appended"] = 0
    diagnostics["solver_invocations"] = 0
    diagnostics["solver_reports"] = []
    diagnostics["solver_errors"] = []
    diagnostics["candidate_counts"] = []
    diagnostics["blocked_assignments"] = []
    diagnostics["relaxed_soft_constraints"] = []
    diagnostics["verified_solver_recommendations"] = 0
    return diagnostics


def record_dependency_report(
    diagnostics: dict[str, object] | None,
    report: DependencyReport,
) -> None:
    if diagnostics is None:
        return
    reports = diagnostics.setdefault("failure_reports", [])
    if isinstance(reports, list):
        reports.append(report.to_dict())
    diagnostics["reports_appended"] = int(diagnostics.get("reports_appended") or 0) + 1
    diagnostics.setdefault("solver_invocations", 0)


def _module_not_found_report(
    failure: DependencyFailure,
    diagnostics: Mapping[str, object],
) -> DependencyReport:
    import_name = failure.import_name or ""
    project_local_modules = set(_string_list(diagnostics.get("project_local_modules")))
    top_level = import_name.split(".", 1)[0]

    if top_level in project_local_modules:
        constraints = infer_rule_based_constraints(
            failure,
            import_name=import_name,
            project_local=True,
        )
        return DependencyReport(
            status="rule_based",
            failure_type=failure.failure_type,
            command=failure.command,
            import_name=import_name,
            confidence="high",
            constraints=constraints,
            evidence=(
                f"{top_level} is project-local, so this is probably an editable install or PYTHONPATH issue.",
            ),
            recommended_commands=(
                "pip install -e .",
            ),
            verification_commands=(
                f'python -c "import {top_level}"',
                "pytest --collect-only -q --disable-warnings",
            ),
        )

    mapping = _mapping_from_diagnostics(import_name, diagnostics)
    if mapping is None:
        declared_names = {
            item.get("name", "")
            for item in diagnostics.get("declared_dependencies", [])
            if isinstance(item, dict)
        }
        mapping_result = map_import_to_package(import_name, declared_names)
        mapping = {
            "import_name": mapping_result.import_name,
            "package_name": mapping_result.package_name,
            "source": mapping_result.source,
            "trust": mapping_result.trust,
        }

    package_name = str(mapping.get("package_name") or top_level)
    mapping_source = str(mapping.get("source") or "unknown")
    source_label = mapping_source_label(mapping_source)
    confidence = "high" if str(mapping.get("trust")) == "high" else "medium"
    package_mapper = (
        f"{source_label} mapped {top_level} -> {package_name}",
    )
    constraints = infer_rule_based_constraints(
        failure,
        import_name=import_name,
        package_name=package_name,
        project_local=False,
    )
    evidence = (
        f"{top_level} is not classified as project-local",
        f"mapping source: {source_label}",
    )
    return DependencyReport(
        status="rule_based",
        failure_type=failure.failure_type,
        command=failure.command,
        import_name=import_name,
        package_name=package_name,
        confidence=confidence,
        package_mapper=package_mapper,
        constraints=constraints,
        evidence=evidence,
        recommended_commands=(f"pip install {package_name}",),
        verification_commands=(
            f'python -c "import {top_level}"',
            "pytest --collect-only -q --disable-warnings",
        ),
    )


def _import_name_error_report(failure: DependencyFailure, diagnostics: Mapping[str, object]) -> DependencyReport:
    import_name = failure.import_name or ""
    mapping = _mapping_from_diagnostics(import_name, diagnostics)
    package_name = str(mapping.get("package_name")) if mapping else None
    evidence = [
        "The package appears importable, but the requested symbol is unavailable.",
        "This often indicates an API/version mismatch rather than a completely missing package.",
    ]
    commands = ["pip check"]
    if package_name:
        evidence.append(f"mapped import package: {package_name}")
    constraints = infer_rule_based_constraints(
        failure,
        package_name=package_name,
    )
    return DependencyReport(
        status="rule_based",
        failure_type=failure.failure_type,
        command=failure.command,
        import_name=import_name or None,
        package_name=package_name,
        confidence="medium",
        constraints=constraints,
        evidence=tuple(evidence),
        recommended_commands=tuple(commands),
        verification_commands=("pytest --collect-only -q --disable-warnings",),
    )


def _dependency_conflict_report(failure: DependencyFailure) -> DependencyReport:
    details = failure.details
    evidence = ["pip reported a dependency conflict."]
    if details.get("required_by") and details.get("requirement"):
        evidence.append(
            f"{details['required_by']} requires {details['requirement']}"
        )
    if details.get("installed_package") and details.get("installed_version"):
        evidence.append(
            f"installed package involved: {details['installed_package']}=={details['installed_version']}"
        )
    constraints = infer_rule_based_constraints(failure)
    recommended_commands = ["pip check"]
    for constraint in constraints:
        if constraint.kind == "version_specifier" and constraint.specifier:
            recommended_commands.insert(0, f'pip install "{constraint.target}{constraint.specifier}"')
    return DependencyReport(
        status="rule_based",
        failure_type=failure.failure_type,
        command=failure.command,
        package_name=failure.package_name,
        confidence="high",
        constraints=constraints,
        evidence=tuple(evidence),
        recommended_commands=tuple(recommended_commands),
        verification_commands=("pytest --collect-only -q --disable-warnings",),
        notes=("Prefer repairing the conflicting version pins before adding unrelated packages.",),
    )


def _no_matching_distribution_report(failure: DependencyFailure) -> DependencyReport:
    package_spec = failure.details.get("package_spec") or failure.package_name or ""
    constraints = infer_rule_based_constraints(failure)
    return DependencyReport(
        status="rule_based",
        failure_type=failure.failure_type,
        command=failure.command,
        package_name=failure.package_name,
        confidence="high",
        constraints=constraints,
        evidence=(f"pip could not find an installable distribution for {package_spec}.",),
        verification_commands=("pip index versions " + failure.package_name if failure.package_name else "pip index versions <package>",),
        notes=("Check package spelling, Python version compatibility, and whether the name is an import name rather than a PyPI distribution.",),
    )


def _syntax_version_report(failure: DependencyFailure) -> DependencyReport:
    constraints = infer_rule_based_constraints(failure)
    return DependencyReport(
        status="rule_based",
        failure_type=failure.failure_type,
        command=failure.command,
        confidence="medium",
        constraints=constraints,
        evidence=("Python raised a SyntaxError consistent with interpreter-version incompatibility.",),
        verification_commands=("python --version", "pytest --collect-only -q --disable-warnings"),
        notes=("Inspect requires-python, tox, CI, and syntax features before changing dependencies.",),
    )


def _native_library_report(failure: DependencyFailure) -> DependencyReport:
    library = failure.details.get("library")
    evidence = (
        f"Missing native shared library: {library}",
    ) if library else ("Missing native shared library.",)
    return DependencyReport(
        status="diagnosed",
        failure_type=failure.failure_type,
        command=failure.command,
        confidence="medium",
        evidence=evidence,
        verification_commands=("pytest --collect-only -q --disable-warnings",),
        notes=("This is not necessarily fixable with pip alone; inspect apt/system package requirements.",),
    )


def _generic_dependency_report(failure: DependencyFailure) -> DependencyReport:
    return DependencyReport(
        status="diagnosed",
        failure_type=failure.failure_type,
        command=failure.command,
        import_name=failure.import_name,
        package_name=failure.package_name,
        confidence="low",
        evidence=(failure.message,) if failure.message else (),
        verification_commands=("pytest --collect-only -q --disable-warnings",),
    )


def _mapping_from_diagnostics(import_name: str, diagnostics: Mapping[str, object]) -> dict[str, object] | None:
    top_level = import_name.split(".", 1)[0]
    for mapping in diagnostics.get("import_package_mappings", []) or []:
        if not isinstance(mapping, dict):
            continue
        if mapping.get("import_name") == top_level:
            return mapping
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
