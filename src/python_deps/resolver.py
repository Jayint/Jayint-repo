from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .graph import build_constraint_graph
from .models import ConstraintGraph, DependencyReport, SolverResult
from .z3_adapter import Z3DependencySolver


def solve_dependency_problem(
    diagnostics: Mapping[str, object],
    latest_report: DependencyReport | Mapping[str, object] | None,
    *,
    metadata_client: Any | None = None,
    max_packages: int = 20,
    max_versions_per_package: int = 8,
    enable_network: bool = True,
    enable_z3: bool = True,
) -> tuple[SolverResult, ConstraintGraph | None]:
    start = time.time()
    try:
        graph = build_constraint_graph(
            diagnostics,
            latest_report=latest_report,
            metadata_client=metadata_client,
            max_packages=max_packages,
            max_versions_per_package=max_versions_per_package,
            enable_network=enable_network,
        )
    except Exception as error:
        return (
            SolverResult(
                status="solver_error",
                verification_commands=("pip check", "pytest --collect-only -q --disable-warnings"),
                errors=(f"constraint graph build failed: {error}",),
            ),
            None,
        )

    if not graph.python_candidates:
        return (
            SolverResult(
                status="unsat",
                verification_commands=("pip check", "pytest --collect-only -q --disable-warnings"),
                explanation=("No Python candidate satisfied the declared requires-python constraints.",),
            ),
            graph,
        )

    has_conditional_required_package = any(
        edge.kind == "include_package"
        for edge in graph.edges
    )
    if not graph.required_packages and not has_conditional_required_package:
        return (
            SolverResult(
                status="unsat",
                verification_commands=("pip check", "pytest --collect-only -q --disable-warnings"),
                explanation=("No package candidates were available for the scoped dependency problem.",),
            ),
            graph,
        )

    if not enable_z3:
        return (
            SolverResult(
                status="solver_unavailable",
                verification_commands=("pip check", "pytest --collect-only -q --disable-warnings"),
                errors=("Z3 solving is disabled",),
            ),
            graph,
        )

    solver = Z3DependencySolver()
    result = solver.solve(graph)
    elapsed_ms = int((time.time() - start) * 1000)
    result = SolverResult(
        status=result.status,
        selected_python=result.selected_python,
        selected_packages=result.selected_packages,
        install_commands=result.install_commands,
        verification_commands=result.verification_commands,
        relaxed_soft_constraints=result.relaxed_soft_constraints,
        explanation=result.explanation + (f"solver elapsed {elapsed_ms}ms",),
        errors=result.errors,
        unsat_core=result.unsat_core,
    )
    return result, graph


def record_solver_result(
    diagnostics: dict[str, object] | None,
    result: SolverResult,
    graph: ConstraintGraph | None,
) -> None:
    if diagnostics is None:
        return
    diagnostics["solver_invocations"] = int(diagnostics.get("solver_invocations") or 0) + 1
    reports = diagnostics.setdefault("solver_reports", [])
    if isinstance(reports, list):
        payload = result.to_dict()
        if graph is not None:
            payload["candidate_counts"] = {
                package: len(candidates)
                for package, candidates in sorted(graph.package_candidates.items())
            }
            payload["required_packages"] = list(graph.required_packages)
        reports.append(payload)
    if result.errors:
        errors = diagnostics.setdefault("solver_errors", [])
        if isinstance(errors, list):
            errors.extend(result.errors)
    if result.relaxed_soft_constraints:
        relaxed = diagnostics.setdefault("relaxed_soft_constraints", [])
        if isinstance(relaxed, list):
            relaxed.extend(result.relaxed_soft_constraints)
