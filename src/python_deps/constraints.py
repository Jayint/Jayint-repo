from __future__ import annotations

import re

from .models import DependencyConstraint, DependencyFailure


def infer_rule_based_constraints(
    failure: DependencyFailure,
    *,
    import_name: str | None = None,
    package_name: str | None = None,
    project_local: bool = False,
) -> tuple[DependencyConstraint, ...]:
    if failure.failure_type == "module_not_found":
        return _missing_module_constraints(
            failure,
            import_name=import_name,
            package_name=package_name,
            project_local=project_local,
        )
    if failure.failure_type == "dependency_conflict":
        return _dependency_conflict_constraints(failure)
    if failure.failure_type == "no_matching_distribution":
        return _no_matching_distribution_constraints(failure)
    if failure.failure_type == "syntax_requires_newer_python":
        return _syntax_version_constraints(failure)
    if failure.failure_type == "import_name_error":
        return _import_name_error_constraints(failure, package_name=package_name)
    return ()


def render_constraint(constraint: DependencyConstraint) -> str:
    prefix = "HARD" if constraint.hard else "SOFT"
    target = constraint.target
    if constraint.specifier:
        target = f"{target}{constraint.specifier}"
    reason = f" ({constraint.reason})" if constraint.reason else ""
    return f"{prefix} {constraint.kind}: {target}{reason}"


def _missing_module_constraints(
    failure: DependencyFailure,
    *,
    import_name: str | None,
    package_name: str | None,
    project_local: bool,
) -> tuple[DependencyConstraint, ...]:
    name = (import_name or failure.import_name or "").split(".", 1)[0]
    if project_local:
        return (
            DependencyConstraint(
                kind="local_project_import",
                target=name,
                source="module_not_found",
                trust="high",
                hard=False,
                reason="missing import is project-local; use editable install or PYTHONPATH",
            ),
        )
    if not package_name:
        return ()
    return (
        DependencyConstraint(
            kind="include_package",
            target=package_name,
            source="module_not_found",
            trust="high",
            hard=False,
            reason=f"missing import {name} must be provided by an installable distribution",
        ),
    )


def _dependency_conflict_constraints(
    failure: DependencyFailure,
) -> tuple[DependencyConstraint, ...]:
    requirement = str(failure.details.get("requirement") or "").strip()
    if not requirement:
        return ()
    parsed = _parse_requirement(requirement)
    if not parsed:
        return ()
    package, specifier = parsed
    required_by = failure.details.get("required_by")
    reason = (
        f"observed pip conflict from {required_by}"
        if required_by
        else "observed pip dependency conflict"
    )
    return (
        DependencyConstraint(
            kind="version_specifier",
            target=package,
            specifier=specifier,
            source="pip_conflict",
            trust="high",
            hard=True,
            reason=reason,
        ),
    )


def _no_matching_distribution_constraints(
    failure: DependencyFailure,
) -> tuple[DependencyConstraint, ...]:
    package_spec = str(failure.details.get("package_spec") or failure.package_name or "").strip()
    parsed = _parse_requirement(package_spec)
    if parsed:
        package, specifier = parsed
    else:
        package, specifier = failure.package_name or package_spec, ""
    if not package:
        return ()
    return (
        DependencyConstraint(
            kind="block_unavailable_candidate",
            target=package,
            specifier=specifier,
            source="pip_no_matching_distribution",
            trust="high",
            hard=True,
            reason="pip reported no installable distribution for this candidate",
        ),
    )


def _syntax_version_constraints(
    failure: DependencyFailure,
) -> tuple[DependencyConstraint, ...]:
    specifier = str(failure.details.get("python_specifier") or "").strip()
    feature = failure.details.get("syntax_feature") or "observed syntax"
    if not specifier:
        return (
            DependencyConstraint(
                kind="python_version",
                target="python",
                source="syntax_error",
                trust="medium",
                hard=False,
                reason=f"{feature} requires checking interpreter compatibility",
            ),
        )
    return (
        DependencyConstraint(
            kind="python_version",
            target="python",
            specifier=specifier,
            source="syntax_error",
            trust="medium",
            hard=False,
            reason=f"{feature} requires {specifier}",
        ),
    )


def _import_name_error_constraints(
    failure: DependencyFailure,
    *,
    package_name: str | None,
) -> tuple[DependencyConstraint, ...]:
    target = package_name or failure.import_name
    if not target:
        return ()
    return (
        DependencyConstraint(
            kind="api_compatibility",
            target=target,
            source="import_name_error",
            trust="medium",
            hard=False,
            reason="package imports but requested symbol is missing; likely API/version mismatch",
        ),
    )


def _parse_requirement(requirement: str) -> tuple[str, str] | None:
    match = re.match(r"^\s*([A-Za-z0-9_.-]+)(.*)$", requirement)
    if not match:
        return None
    package = match.group(1).strip()
    specifier = match.group(2).strip()
    return package, specifier
