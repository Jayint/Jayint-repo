"""Static Python dependency evidence collection for Jayint runs."""

from .evidence import collect_python_dependency_evidence
from .failure_classifier import classify_dependency_failure
from .models import DependencyConstraint, DependencyReport, PythonDependencyEvidence
from .report import (
    append_dependency_report,
    build_dependency_failure_report,
    initialize_diagnostics_summary,
    record_dependency_report,
    solver_result_to_report,
)
from .resolver import record_solver_result, solve_dependency_problem

__all__ = [
    "DependencyReport",
    "DependencyConstraint",
    "PythonDependencyEvidence",
    "append_dependency_report",
    "build_dependency_failure_report",
    "classify_dependency_failure",
    "collect_python_dependency_evidence",
    "initialize_diagnostics_summary",
    "record_dependency_report",
    "record_solver_result",
    "solve_dependency_problem",
    "solver_result_to_report",
]
