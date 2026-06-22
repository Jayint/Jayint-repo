from __future__ import annotations

import re
from typing import Any, NamedTuple

from .graph import candidate_satisfies_specifier
from .models import ConstraintEdge, ConstraintGraph, PackageCandidate, PythonCandidate, SolverResult
from .pypi_metadata import marker_applies_to_python, parse_requires_dist, python_satisfies_specifier


_EXPLAIN_TIMEOUT_MS = 5_000  # best-effort cap for the unsat-explanation solve (review 3A)


class _Clause(NamedTuple):
    expr: Any
    reason: str


class Z3DependencySolver:
    def __init__(self, *, timeout_ms: int = 30_000) -> None:
        self.timeout_ms = timeout_ms
        self._z3 = self._import_z3()

    @property
    def available(self) -> bool:
        return self._z3 is not None

    def solve(self, graph: ConstraintGraph) -> SolverResult:
        if self._z3 is None:
            return SolverResult(
                status="solver_unavailable",
                verification_commands=("pip check", "pytest --collect-only -q --disable-warnings"),
                errors=("z3-solver is not installed",),
            )

        errors: list[str] = []
        for python_candidate in _sorted_python_candidates(graph.python_candidates):
            result = self._solve_for_python(graph, python_candidate, relax_soft=False)
            if result.status == "sat":
                return result
            errors.extend(result.errors)

        for python_candidate in _sorted_python_candidates(graph.python_candidates):
            result = self._solve_for_python(graph, python_candidate, relax_soft=True)
            if result.status == "sat":
                return SolverResult(
                    status="sat_soft_relaxed",
                    selected_python=result.selected_python,
                    selected_packages=result.selected_packages,
                    install_commands=result.install_commands,
                    verification_commands=result.verification_commands,
                    relaxed_soft_constraints=tuple(_edge_name(edge) for edge in graph.soft_edges),
                    explanation=result.explanation + ("soft constraints were relaxed",),
                    errors=result.errors,
                )
            errors.extend(result.errors)

        return SolverResult(
            status="unsat",
            verification_commands=("pip check", "pytest --collect-only -q --disable-warnings"),
            explanation=("No candidate assignment satisfied the hard constraints.",),
            errors=tuple(errors),
            unsat_core=self._explain_unsat(graph),
        )

    def _build_variables(self, graph: ConstraintGraph) -> dict[tuple[str, str], Any]:
        z3 = self._z3
        variables: dict[tuple[str, str], Any] = {}
        for package_name, candidates in sorted(graph.package_candidates.items()):
            for candidate in _sorted_candidates(candidates):
                variables[(package_name, candidate.version)] = z3.Bool(
                    _var_name(package_name, candidate.version)
                )
        return variables

    def _collect_clauses(
        self,
        graph: ConstraintGraph,
        python_candidate: PythonCandidate,
        *,
        relax_soft: bool,
    ) -> tuple[dict[tuple[str, str], Any], list[_Clause]]:
        z3 = self._z3
        python_version = python_candidate.version
        variables = self._build_variables(graph)
        clauses: list[_Clause] = []

        for package_name, candidates in sorted(graph.package_candidates.items()):
            package_vars = [
                variables[(package_name, candidate.version)]
                for candidate in _sorted_candidates(candidates)
            ]
            if not package_vars:
                continue
            if _package_required_for_python(graph, package_name, python_version):
                clauses.append(_Clause(z3.Or(*package_vars), f"package '{package_name}' must be installed"))
            clauses.append(
                _Clause(z3.AtMost(*package_vars, 1), f"at most one version of '{package_name}' may be installed")
            )

        for edge in graph.edges + ([] if relax_soft else graph.soft_edges):
            self._collect_edge_clauses(clauses, variables, graph, edge, python_version)

        for package_name, candidates in graph.package_candidates.items():
            for candidate in candidates:
                self._collect_requires_dist_clauses(
                    clauses, variables, graph, package_name, candidate, python_version
                )

        self._collect_no_floating_clauses(clauses, variables, graph, python_version)

        for assignment in graph.blocked_assignments:
            assignment_vars = []
            for package_name, version in sorted(assignment.items()):
                var = variables.get((package_name, version))
                if var is not None:
                    assignment_vars.append(var)
            if assignment_vars:
                clauses.append(
                    _Clause(z3.Not(z3.And(*assignment_vars)), "previously blocked package assignment")
                )

        return variables, clauses

    def _solve_for_python(
        self,
        graph: ConstraintGraph,
        python_candidate: PythonCandidate,
        *,
        relax_soft: bool,
    ) -> SolverResult:
        z3 = self._z3
        solver = z3.Solver()
        solver.set("timeout", self.timeout_ms)

        variables, clauses = self._collect_clauses(graph, python_candidate, relax_soft=relax_soft)
        for clause in clauses:
            solver.add(clause.expr)

        check = solver.check()
        if check != z3.sat:
            return SolverResult(
                status="unsat",
                selected_python=python_candidate.version,
                errors=(f"z3 returned {check} for Python {python_candidate.version}",),
            )

        model = solver.model()
        selected: dict[str, str] = {}
        for (package_name, version), var in sorted(variables.items()):
            value = model.eval(var, model_completion=False)
            if z3.is_true(value):
                selected[package_name] = version

        return SolverResult(
            status="sat",
            selected_python=python_candidate.version,
            selected_packages=selected,
            install_commands=(_install_command(selected),) if selected else (),
            verification_commands=("pip check", "pytest --collect-only -q --disable-warnings"),
            explanation=_explain_selection(graph, selected, python_candidate.version),
        )

    def _explain_unsat(self, graph: ConstraintGraph) -> tuple[str, ...]:
        z3 = self._z3
        if z3 is None or not graph.python_candidates:
            return ()
        # Terminal unsat is only reached after the relax_soft=True pass ALSO
        # failed, so the HARD constraints alone are unsatisfiable. Explain that
        # irreducible hard conflict (relax_soft=True) -- never cite a soft,
        # LLM-imputed edge as the culprit. (review CQ)
        python_candidate = _sorted_python_candidates(graph.python_candidates)[0]
        _variables, clauses = self._collect_clauses(graph, python_candidate, relax_soft=True)

        solver = z3.Solver()
        solver.set(unsat_core=True)
        # Explanation is best-effort: cap the extra solve so the Phase 4 hot path
        # never stalls. check() returns 'unknown' on timeout -> the guard below
        # returns () and the generic explanation string remains the fallback. (review 3A)
        solver.set("timeout", _EXPLAIN_TIMEOUT_MS)

        reasons_by_label: dict[str, str] = {}
        for index, clause in enumerate(clauses):
            label = f"c{index}"
            reasons_by_label[label] = clause.reason
            solver.assert_and_track(clause.expr, z3.Bool(label))

        if solver.check() != z3.unsat:
            return ()

        conflicting: list[str] = []
        for tracked in solver.unsat_core():
            reason = reasons_by_label.get(str(tracked))
            if reason and reason not in conflicting:
                conflicting.append(reason)
        if not conflicting:
            return ()

        header = (
            f"unsatisfiable under Python {python_candidate.version}; "
            "minimal conflicting constraints:"
        )
        return tuple([header] + conflicting[:12])

    def _collect_edge_clauses(
        self,
        clauses: list[_Clause],
        variables: dict[tuple[str, str], Any],
        graph: ConstraintGraph,
        edge: ConstraintEdge,
        python_version: str,
    ) -> None:
        z3 = self._z3
        if edge.marker and not marker_applies_to_python(edge.marker, python_version):
            return
        reason = edge.reason or _edge_name(edge)

        if edge.kind == "include_package":
            target_vars = [
                variables[(edge.target_package, candidate.version)]
                for candidate in graph.package_candidates.get(edge.target_package, [])
                if (edge.target_package, candidate.version) in variables
            ]
            clauses.append(_Clause(z3.Or(*target_vars) if target_vars else z3.BoolVal(False), reason))
            return

        if edge.kind == "declared_specifier":
            for candidate in graph.package_candidates.get(edge.target_package, []):
                var = variables.get((edge.target_package, candidate.version))
                if var is not None and not candidate_satisfies_specifier(candidate, edge.target_specifier):
                    clauses.append(_Clause(z3.Not(var), reason))
            return

        if edge.kind == "block_candidate":
            for candidate in graph.package_candidates.get(edge.target_package, []):
                var = variables.get((edge.target_package, candidate.version))
                if var is not None and candidate_satisfies_specifier(candidate, edge.target_specifier):
                    clauses.append(_Clause(z3.Not(var), reason))
            return

        source = _parse_node(edge.source_node)
        if source is None:
            return
        source_package, source_version = source
        source_var = variables.get((source_package, source_version))
        if source_var is None:
            return

        if edge.kind == "requires_python":
            if not python_satisfies_specifier(python_version, edge.target_specifier):
                clauses.append(_Clause(z3.Not(source_var), reason))
            return

        if edge.kind == "requires_any":
            target_vars = [
                variables[(edge.target_package, candidate.version)]
                for candidate in graph.package_candidates.get(edge.target_package, [])
                if (edge.target_package, candidate.version) in variables
                and candidate_satisfies_specifier(candidate, edge.target_specifier)
            ]
            if target_vars:
                clauses.append(_Clause(z3.Implies(source_var, z3.Or(*target_vars)), reason))
            else:
                clauses.append(_Clause(z3.Not(source_var), reason))

    def _collect_requires_dist_clauses(
        self,
        clauses: list[_Clause],
        variables: dict[tuple[str, str], Any],
        graph: ConstraintGraph,
        package_name: str,
        candidate: PackageCandidate,
        python_version: str,
    ) -> None:
        z3 = self._z3
        source_var = variables.get((package_name, candidate.version))
        if source_var is None:
            return
        for dependency_name, specifier in parse_requires_dist(candidate.requires_dist, python_version):
            target_vars = [
                variables[(dependency_name, dep_candidate.version)]
                for dep_candidate in graph.package_candidates.get(dependency_name, [])
                if (dependency_name, dep_candidate.version) in variables
                and candidate_satisfies_specifier(dep_candidate, specifier)
            ]
            reason = f"{candidate.node_id} requires {dependency_name}{specifier}"
            if target_vars:
                clauses.append(_Clause(z3.Implies(source_var, z3.Or(*target_vars)), reason))
            else:
                clauses.append(_Clause(z3.Not(source_var), reason))

    def _collect_no_floating_clauses(
        self,
        clauses: list[_Clause],
        variables: dict[tuple[str, str], Any],
        graph: ConstraintGraph,
        python_version: str,
    ) -> None:
        z3 = self._z3
        for target_package, target_candidates in graph.package_candidates.items():
            if _package_required_for_python(graph, target_package, python_version):
                continue
            for target_candidate in target_candidates:
                target_var = variables.get((target_package, target_candidate.version))
                if target_var is None:
                    continue
                source_vars = []
                for source_package, source_candidates in graph.package_candidates.items():
                    for source_candidate in source_candidates:
                        source_var = variables.get((source_package, source_candidate.version))
                        if source_var is None:
                            continue
                        for dependency_name, specifier in parse_requires_dist(
                            source_candidate.requires_dist,
                            python_version,
                        ):
                            if dependency_name == target_package and candidate_satisfies_specifier(
                                target_candidate,
                                specifier,
                            ):
                                source_vars.append(source_var)
                                break
                reason = f"'{target_candidate.node_id}' would be installed but nothing requires it"
                if source_vars:
                    clauses.append(_Clause(z3.Implies(target_var, z3.Or(*source_vars)), reason))
                else:
                    clauses.append(_Clause(z3.Not(target_var), reason))

    @staticmethod
    def _import_z3() -> Any | None:
        try:
            import z3  # type: ignore
        except Exception:
            return None
        return z3


def _sorted_python_candidates(candidates: list[PythonCandidate]) -> list[PythonCandidate]:
    return sorted(candidates, key=lambda candidate: (candidate.rank, candidate.version))


def _sorted_candidates(candidates: list[PackageCandidate]) -> list[PackageCandidate]:
    return sorted(candidates, key=lambda candidate: (candidate.rank, candidate.version))


def _var_name(package_name: str, version: str) -> str:
    return "pkg_" + re.sub(r"[^A-Za-z0-9_]", "_", f"{package_name}_{version}")


def _parse_node(node_id: str) -> tuple[str, str] | None:
    if "==" not in node_id:
        return None
    package_name, version = node_id.split("==", 1)
    return package_name, version


def _package_required_for_python(graph: ConstraintGraph, package_name: str, python_version: str) -> bool:
    if package_name in graph.required_packages:
        return True
    for edge in graph.edges:
        if (
            edge.kind == "include_package"
            and edge.target_package == package_name
            and marker_applies_to_python(edge.marker, python_version)
        ):
            return True
    return False


def _install_command(selected: dict[str, str]) -> str:
    packages = " ".join(
        f'"{package_name}=={version}"'
        for package_name, version in sorted(selected.items())
    )
    return f"pip install {packages}"


def _explain_selection(graph: ConstraintGraph, selected: dict[str, str], python_version: str) -> tuple[str, ...]:
    explanation = [f"selected Python {python_version}"]
    selected_nodes = {f"{package}=={version}" for package, version in selected.items()}
    for edge in graph.edges:
        if edge.source_node in selected_nodes or edge.target_package in selected:
            if edge.reason:
                explanation.append(edge.reason)
        if len(explanation) >= 8:
            break
    for package_name, version in sorted(selected.items()):
        for candidate in graph.package_candidates.get(package_name, []):
            if candidate.version != version:
                continue
            for dependency_name, specifier in parse_requires_dist(candidate.requires_dist, python_version):
                if dependency_name in selected:
                    explanation.append(f"{candidate.node_id} requires {dependency_name}{specifier}")
            if len(explanation) >= 8:
                break
        if len(explanation) >= 8:
            break
    return tuple(dict.fromkeys(explanation))


def _edge_name(edge: ConstraintEdge) -> str:
    return f"{edge.kind}:{edge.source_node}:{edge.target_package}{edge.target_specifier}"
