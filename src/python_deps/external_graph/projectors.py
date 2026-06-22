"""Failure-type projectors + ConstraintGraph projector + SolverResult projector.

All projectors are pure projections — no network calls. They read already-resolved
diagnostics, failure, report, constraint_graph, and solver_result data.

See docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md for the full design.
"""
from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models import ConstraintGraph, DependencyFailure, DependencyReport, SolverResult

_VERIFY_TARGET_ID = "verify:pytest-collect"


def _ensure_verify_target(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    blocker_id: str,
) -> None:
    """Add VerifyTarget node + blocks edge if not already present.

    The VerifyTarget node is deduplicated at the top-level dedup step;
    this helper just appends (dedup happens later).
    """
    nodes.append({
        "id": _VERIFY_TARGET_ID,
        "kind": "VerifyTarget",
        "state": "blocked",
    })
    edges.append({
        "src": blocker_id,
        "kind": "blocks",
        "dst": _VERIFY_TARGET_ID,
    })


def project_failure_neighborhood(
    *,
    failure_type: str,
    failure: "DependencyFailure",
    report: "DependencyReport | None",
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    import_to_pkg: dict[str, dict[str, Any]],
    used_in_code_pkgs: set[str],
    declared_pkg_names: set[str],
    python_runtime_nodes: list[dict[str, Any]],
    normalize_package_name: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project failure-neighborhood nodes/edges/frontier based on failure_type.

    Dispatches on failure.failure_type (NOT failure.kind).
    Reads conflict data from report.constraints and failure.details
    (there is NO report.details, NO failure.kind).

    Eligible failure types (per spec):
      module_not_found, import_name_error, no_matching_distribution,
      dependency_conflict, syntax_requires_newer_python

    Modifies nodes, edges, frontier in-place and returns the updated (nodes, edges).
    """
    if failure_type == "module_not_found":
        _project_module_not_found(
            failure=failure,
            report=report,
            nodes=nodes,
            edges=edges,
            frontier=frontier,
            import_to_pkg=import_to_pkg,
            declared_pkg_names=declared_pkg_names,
        )
    elif failure_type == "import_name_error":
        _project_import_name_error(
            failure=failure,
            nodes=nodes,
            edges=edges,
            frontier=frontier,
            import_to_pkg=import_to_pkg,
        )
    elif failure_type == "no_matching_distribution":
        _project_no_matching_distribution(
            failure=failure,
            nodes=nodes,
            edges=edges,
            frontier=frontier,
        )
    elif failure_type == "dependency_conflict":
        _project_dependency_conflict(
            failure=failure,
            report=report,
            nodes=nodes,
            edges=edges,
            frontier=frontier,
            normalize_package_name=normalize_package_name,
        )
    elif failure_type == "syntax_requires_newer_python":
        _project_syntax_requires_newer_python(
            failure=failure,
            nodes=nodes,
            edges=edges,
            frontier=frontier,
            python_runtime_nodes=python_runtime_nodes,
        )
    # not_dependency_related and native_library_missing: no graph slice (not graph-eligible)
    return nodes, edges


def _project_module_not_found(
    *,
    failure: "DependencyFailure",
    report: "DependencyReport | None",
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    import_to_pkg: dict[str, dict[str, Any]],
    declared_pkg_names: set[str],
) -> None:
    """Handle module_not_found: add import node (if missing), VerifyTarget, blocks edge,
    and populate frontier.missing_imports.
    """
    import_name = failure.import_name or failure.package_name or ""
    if not import_name:
        return

    import_node_id = f"import:{import_name}"
    # Ensure import node exists (may already be present from static pass)
    existing_ids = {str(n.get("id", "")) for n in nodes}
    if import_node_id not in existing_ids:
        nodes.append({
            "id": import_node_id,
            "kind": "PythonImport",
            "state": "missing",
        })

    # Blocker: import node blocks pytest-collect
    _ensure_verify_target(nodes, edges, import_node_id)

    # frontier.missing_imports
    missing = frontier.setdefault("missing_imports", [])
    if import_name not in missing:
        missing.append(import_name)

    # undeclared used packages: only if the mapped package is NOT already declared
    if import_name in import_to_pkg:
        norm_pkg = import_to_pkg[import_name]["package_name"]
        if norm_pkg not in declared_pkg_names:
            if norm_pkg not in {p for p in (frontier.get("undeclared_used_packages") or [])}:
                undeclared = frontier.setdefault("undeclared_used_packages", [])
                undeclared.append(norm_pkg)

    # candidate_transactions: don't duplicate if report.recommended_commands already has it
    recommended = list(report.recommended_commands) if report else []
    already_recommended = any(
        import_name in cmd or (
            import_name in import_to_pkg and
            import_to_pkg[import_name]["package_name"] in cmd
        )
        for cmd in recommended
    )
    transactions = frontier.setdefault("candidate_transactions", [])
    if not already_recommended:
        pkg_name = (
            import_to_pkg[import_name]["package_name"]
            if import_name in import_to_pkg
            else import_name
        )
        install_cmd = f"uv pip install {shlex.quote(pkg_name)}"
        if install_cmd not in transactions:
            transactions.append(install_cmd)


def _project_import_name_error(
    *,
    failure: "DependencyFailure",
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    import_to_pkg: dict[str, dict[str, Any]],
) -> None:
    """Handle import_name_error (e.g. cv2 → opencv-python): ensure import and package nodes
    exist, add maps_to_package edge, and name the bad import in the frontier.
    """
    import_name = failure.import_name or ""
    package_name = failure.package_name or ""
    if not import_name:
        return

    import_node_id = f"import:{import_name}"
    existing_ids = {str(n.get("id", "")) for n in nodes}

    # Ensure import node
    if import_node_id not in existing_ids:
        nodes.append({
            "id": import_node_id,
            "kind": "PythonImport",
            "state": "error",
        })

    # Resolve package name (from mapping or failure.package_name)
    from ..import_mapping import normalize_package_name
    if import_name in import_to_pkg:
        norm_pkg = import_to_pkg[import_name]["package_name"]
    elif package_name:
        norm_pkg = normalize_package_name(package_name)
    else:
        norm_pkg = ""

    if norm_pkg:
        pkg_node_id = f"package:pip:{norm_pkg}"
        if pkg_node_id not in existing_ids:
            nodes.append({
                "id": pkg_node_id,
                "kind": "PythonPackage",
                "state": "candidate",
            })
        # maps_to_package edge
        edges.append({
            "src": import_node_id,
            "kind": "maps_to_package",
            "dst": pkg_node_id,
        })

    # frontier: name the bad import
    missing = frontier.setdefault("missing_imports", [])
    if import_name not in missing:
        missing.append(import_name)


def _project_no_matching_distribution(
    *,
    failure: "DependencyFailure",
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
) -> None:
    """Handle no_matching_distribution: add ResolverFailure node, VerifyTarget, blocks edge,
    and frontier.no_matching_distribution key.
    """
    pkg_name = failure.package_name or ""
    if not pkg_name:
        pkg_name = "unknown"

    rf_node_id = f"no-match:{pkg_name}"
    nodes.append({
        "id": rf_node_id,
        "kind": "ResolverFailure",
        "state": "blocked",
        "package": pkg_name,
    })

    # ResolverFailure blocks → VerifyTarget
    _ensure_verify_target(nodes, edges, rf_node_id)

    # frontier
    frontier["no_matching_distribution"] = [pkg_name]


def _project_dependency_conflict(
    *,
    failure: "DependencyFailure",
    report: "DependencyReport | None",
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    normalize_package_name: Any,
) -> None:
    """Handle dependency_conflict: add ResolverFailure/conflict node, VerifyTarget, blocks edge.

    Conflict data comes from:
      - report.constraints: tuple of DependencyConstraint (kind/target/specifier/source/hard/reason)
      - failure.details: dict with required_by/requirement/installed_package/installed_version

    There is NO report.details and NO failure.kind.
    """
    pkg_name = failure.package_name or ""

    # conflict description from failure.details
    details = failure.details or {}
    required_by = str(details.get("required_by", ""))
    requirement = str(details.get("requirement", ""))
    installed_package = str(details.get("installed_package", pkg_name))
    installed_version = str(details.get("installed_version", ""))

    # Build a conflict description
    conflict_desc_parts: list[str] = []
    if required_by and requirement:
        conflict_desc_parts.append(f"{required_by} requires {requirement}")
    if installed_package and installed_version:
        conflict_desc_parts.append(
            f"but {installed_package}=={installed_version} is installed"
        )
    elif pkg_name:
        conflict_desc_parts.append(f"package {pkg_name} has a conflict")

    # Also consume report.constraints for conflict text
    if report is not None:
        for constraint in (report.constraints or ()):
            reason = str(constraint.reason) if constraint.reason else ""
            target = str(constraint.target) if constraint.target else ""
            specifier = str(constraint.specifier) if constraint.specifier else ""
            if reason:
                conflict_desc_parts.append(reason)
            elif target and specifier:
                conflict_desc_parts.append(f"{target}{specifier} constraint")

    # Deduplicate conflict description parts (case-insensitive, insertion-ordered).
    # report.constraints[*].reason often carries the same text as failure.details,
    # e.g. 'scipy requires numpy>=1.25' can appear twice without deduplication.
    seen_parts: set[str] = set()
    deduped_parts: list[str] = [
        p for p in conflict_desc_parts
        if not (p.lower() in seen_parts or seen_parts.add(p.lower()))  # type: ignore[func-returns-value]
    ]
    conflict_desc = "; ".join(deduped_parts) if deduped_parts else f"conflict: {pkg_name}"

    # ResolverFailure node for the conflict
    conflict_node_id = f"resolver-conflict:{installed_package or pkg_name or 'unknown'}"
    nodes.append({
        "id": conflict_node_id,
        "kind": "ResolverFailure",
        "state": "blocked",
        "package": installed_package or pkg_name,
    })

    # Add Requirement nodes from report.constraints
    if report is not None:
        for constraint in (report.constraints or ()):
            target = str(constraint.target) if constraint.target else ""
            specifier = str(constraint.specifier) if constraint.specifier else ""
            if not target:
                continue
            req_id = f"requirement:{target}{specifier}"
            existing_ids = {str(n.get("id", "")) for n in nodes}
            if req_id not in existing_ids:
                nodes.append({
                    "id": req_id,
                    "kind": "Requirement",
                    "state": "conflicting",
                    "specifier": specifier,
                })
            # conflicts_with edge: package:pip:target conflicts_with requirement
            pkg_node_id = f"package:pip:{normalize_package_name(target)}"
            edges.append({
                "src": pkg_node_id,
                "kind": "conflicts_with",
                "dst": req_id,
            })

    # conflict node blocks → VerifyTarget
    _ensure_verify_target(nodes, edges, conflict_node_id)

    # frontier.conflicts
    conflicts = frontier.setdefault("conflicts", [])
    if conflict_desc not in conflicts:
        conflicts.append(conflict_desc)


def _project_syntax_requires_newer_python(
    *,
    failure: "DependencyFailure",
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    python_runtime_nodes: list[dict[str, Any]],
) -> None:
    """Handle syntax_requires_newer_python: ensure PythonRuntime nodes exist,
    add Requirement nodes and constrains edges, and populate frontier.runtime_constraints.

    Per spec §4 edge table: constrains: Requirement → PythonPackage/PythonRuntime.
    For each python_requires entry with a specifier:
      - Creates a Requirement node 'requirement:python{specifier}' (kind=Requirement,
        state=constraining).
      - Creates a PythonRuntime node 'python:{specifier}' if not already present.
      - Emits a 'constrains' edge from 'requirement:python{specifier}' → 'python:{specifier}'.

    A proper `blocks` edge (from a ResolverFailure node to VerifyTarget) will be added in
    Task 4 when the retained ConstraintGraph provides a ResolverFailure node.
    """
    # Ensure at least one PythonRuntime node exists (may already be from static pass)
    existing_ids = {str(n.get("id", "")) for n in nodes}

    runtime_added = False
    for rt_node in python_runtime_nodes:
        rt_id = str(rt_node.get("id", ""))
        specifier = str(rt_node.get("specifier", ""))
        if not rt_id:
            continue
        if rt_id not in existing_ids:
            nodes.append(rt_node)
            existing_ids.add(rt_id)

        # Add a Requirement node for the python version constraint and a constrains edge.
        # Per spec §4: constrains goes from Requirement → PythonPackage/PythonRuntime.
        if specifier:
            req_id = f"requirement:python{specifier}"
            if req_id not in existing_ids:
                nodes.append({
                    "id": req_id,
                    "kind": "Requirement",
                    "state": "constraining",
                    "specifier": specifier,
                })
                existing_ids.add(req_id)
            # constrains edge: Requirement → PythonRuntime
            edges.append({
                "src": req_id,
                "kind": "constrains",
                "dst": rt_id,
            })

        # Record runtime constraint in frontier
        source = str(rt_node.get("source", ""))
        constraint_str = f"python{specifier}"
        if source:
            constraint_str += f" (from {source})"
        runtime_constraints = frontier.setdefault("runtime_constraints", [])
        if constraint_str not in runtime_constraints:
            runtime_constraints.append(constraint_str)
        runtime_added = True

    if not runtime_added:
        # No python_requires from diagnostics — create a generic runtime node from failure message
        msg = failure.message or ""
        # Try to extract a specifier from the message
        specifier = ""
        if ">=" in msg or "<" in msg or "==" in msg:
            import re
            m = re.search(r"(>=?[\d.]+|==[\d.]+|<=?[\d.]+)", msg)
            if m:
                specifier = m.group(1)
        runtime_id = f"python:{specifier}" if specifier else "python:runtime"
        if runtime_id not in existing_ids:
            rt_node = {
                "id": runtime_id,
                "kind": "PythonRuntime",
                "state": "incompatible",
            }
            if specifier:
                rt_node["specifier"] = specifier
            nodes.append(rt_node)
            existing_ids.add(runtime_id)

        # Add Requirement node + constrains edge even in the fallback case
        if specifier:
            req_id = f"requirement:python{specifier}"
            if req_id not in existing_ids:
                nodes.append({
                    "id": req_id,
                    "kind": "Requirement",
                    "state": "constraining",
                    "specifier": specifier,
                })
                existing_ids.add(req_id)
            edges.append({
                "src": req_id,
                "kind": "constrains",
                "dst": runtime_id,
            })

        runtime_constraints = frontier.setdefault("runtime_constraints", [])
        constraint_str = f"python{specifier}" if specifier else "python runtime incompatibility"
        if constraint_str not in runtime_constraints:
            runtime_constraints.append(constraint_str)


def project_constraint_graph(
    *,
    constraint_graph: "ConstraintGraph",
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    declared_pkg_names: set[str],
    declared_info: "dict[str, dict[str, Any]]",
    normalize_package_name: Any,
    python_version: str = "3.11",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project ConstraintGraph into PackageVersion nodes, candidate_version edges,
    and requires_dist/requires_python edges (DERIVED, not from ConstraintEdge).

    HARD INVARIANT: No PyPI/network calls. Only reads constraint_graph.package_candidates.
    requires_dist edges are DERIVED from PackageCandidate.requires_dist via parse_requires_dist.
    Real ConstraintEdge kinds: declared_specifier, requires_python, include_package,
    block_candidate — there is NO requires_dist ConstraintEdge kind.

    _project_solver_result handles SolverResult separately; solver_result is NOT
    passed here to avoid confusion about which path surfaces which conflict data.

    ``python_version`` is forwarded to ``parse_requires_dist`` so that PEP 508 markers
    (e.g. ``python_version >= "3.10"``) are evaluated against the real environment Python,
    not an arbitrary hard-coded default.  The caller (``build_external_dependency_graph_slice``)
    derives the version from ``diagnostics['python_requires']`` or the caller-supplied kwarg.
    """
    from ..pypi_metadata import parse_requires_dist

    existing_ids = {str(n.get("id", "")) for n in nodes}

    for pkg_name, candidates in (constraint_graph.package_candidates or {}).items():
        # Normalize pkg_name so 'Pillow' and 'pillow' map to the same node as
        # the static-seed path (which always normalizes via normalize_package_name).
        norm_pkg_name = normalize_package_name(pkg_name)
        pkg_node_id = f"package:pip:{norm_pkg_name}"
        if pkg_node_id not in existing_ids:
            is_declared = norm_pkg_name in declared_pkg_names
            pkg_node: dict[str, Any] = {
                "id": pkg_node_id,
                "kind": "PythonPackage",
                "declared": is_declared,
                "used_in_code": False,
            }
            nodes.append(pkg_node)
            existing_ids.add(pkg_node_id)

        for candidate in candidates or []:
            _project_package_candidate(
                candidate=candidate,
                pkg_node_id=pkg_node_id,
                nodes=nodes,
                edges=edges,
                frontier=frontier,
                existing_ids=existing_ids,
                declared_info=declared_info,
                normalize_package_name=normalize_package_name,
                python_version=python_version,
                parse_requires_dist=parse_requires_dist,
            )

    # Project blocked_assignments into the conflict frontier
    for blocked in (constraint_graph.blocked_assignments or []):
        pkg = str(blocked.get("package", ""))
        version = str(blocked.get("version", ""))
        reason = str(blocked.get("reason", ""))
        if pkg or reason:
            conflicts = frontier.setdefault("conflicts", [])
            desc = reason or f"{pkg}=={version} blocked"
            if desc not in conflicts:
                conflicts.append(desc)

    return nodes, edges


def _project_package_candidate(
    *,
    candidate: Any,
    pkg_node_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    existing_ids: set[str],
    declared_info: dict[str, dict[str, Any]],
    normalize_package_name: Any,
    python_version: str,
    parse_requires_dist: Any,
) -> None:
    """Project a single PackageCandidate into version node, candidate_version edge,
    requires_python edge, and requires_dist edges. Each section is <50 lines.
    """
    # PackageVersion node: id = "package:pip:{name}=={version}"
    version_node_id = (
        f"package:pip:{normalize_package_name(candidate.name)}=={candidate.version}"
    )
    if version_node_id not in existing_ids:
        version_node: dict[str, Any] = {
            "id": version_node_id,
            "kind": "PackageVersion",
            "state": "candidate",
        }
        if candidate.requires_python:
            version_node["requires_python"] = candidate.requires_python
        nodes.append(version_node)
        existing_ids.add(version_node_id)

    # candidate_version edge: PythonPackage → PackageVersion
    edges.append({
        "src": pkg_node_id,
        "kind": "candidate_version",
        "dst": version_node_id,
    })

    # requires_python edge: PackageVersion → PythonRuntime
    if candidate.requires_python:
        _project_requires_python_edge(
            candidate=candidate,
            version_node_id=version_node_id,
            nodes=nodes,
            edges=edges,
            existing_ids=existing_ids,
        )

    # requires_dist edges: DERIVED from PackageCandidate.requires_dist
    if candidate.requires_dist:
        _project_requires_dist_edges(
            candidate=candidate,
            version_node_id=version_node_id,
            nodes=nodes,
            edges=edges,
            frontier=frontier,
            existing_ids=existing_ids,
            declared_info=declared_info,
            python_version=python_version,
            parse_requires_dist=parse_requires_dist,
        )


def _project_requires_python_edge(
    *,
    candidate: Any,
    version_node_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    existing_ids: set[str],
) -> None:
    """Add requires_python node and edge for a candidate."""
    rt_id = f"python:{candidate.requires_python}"
    if rt_id not in existing_ids:
        nodes.append({
            "id": rt_id,
            "kind": "PythonRuntime",
            "specifier": candidate.requires_python,
        })
        existing_ids.add(rt_id)
    edges.append({
        "src": version_node_id,
        "kind": "requires_python",
        "dst": rt_id,
    })


def _project_requires_dist_edges(
    *,
    candidate: Any,
    version_node_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    existing_ids: set[str],
    declared_info: dict[str, dict[str, Any]],
    python_version: str,
    parse_requires_dist: Any,
) -> None:
    """Add Requirement nodes and requires_dist edges from PackageCandidate.requires_dist.
    Also detects structural specifier conflicts via _detect_specifier_conflict.
    """
    parsed_deps = parse_requires_dist(candidate.requires_dist, python_version)
    for dep_name, dep_specifier in parsed_deps:
        req_str = f"{dep_name}{dep_specifier}" if dep_specifier else dep_name
        req_id = f"requirement:{req_str}"
        if req_id not in existing_ids:
            req_node: dict[str, Any] = {
                "id": req_id,
                "kind": "Requirement",
                "state": "declared",
            }
            if dep_specifier:
                req_node["specifier"] = dep_specifier
            nodes.append(req_node)
            existing_ids.add(req_id)
        # requires_dist edge: PackageVersion → Requirement
        edges.append({
            "src": version_node_id,
            "kind": "requires_dist",
            "dst": req_id,
        })

        # Structural conflict detection
        if dep_specifier and dep_name in declared_info:
            declared_specifier = declared_info[dep_name].get("specifier", "")
            if declared_specifier:
                detect_specifier_conflict(
                    pkg_name=dep_name,
                    declared_specifier=declared_specifier,
                    required_by=f"{candidate.name}=={candidate.version}",
                    req_specifier=dep_specifier,
                    frontier=frontier,
                )


def detect_specifier_conflict(
    *,
    pkg_name: str,
    declared_specifier: str,
    required_by: str,
    req_specifier: str,
    frontier: "dict[str, Any]",
) -> None:
    """Detect and record a version-pin conflict between a declared specifier and a
    requires_dist specifier from ConstraintGraph data.

    This runs purely on already-resolved graph data — NO network calls.
    Uses packaging.specifiers.SpecifierSet to test compatibility.

    Example: declared numpy==1.21, but scipy==1.11.0 requires numpy>=1.25.
    SpecifierSet('>=1.25').contains('1.21') → False → conflict recorded.
    """
    try:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version

        # Parse the declared specifier to extract the pinned/declared version.
        declared_clean = declared_specifier.strip()
        if not declared_clean:
            return

        # Extract the version literal from the declared specifier string.
        import re
        version_match = re.search(r"[\d][.\d\w+-]*", declared_clean)
        if not version_match:
            return
        declared_version_str = version_match.group(0)

        # Validate both version strings are parseable.
        try:
            declared_ver = Version(declared_version_str)
        except InvalidVersion:
            return

        req_spec_set = SpecifierSet(req_specifier)
        if not req_spec_set.contains(declared_ver, prereleases=True):
            desc = (
                f"{pkg_name} declared {declared_specifier} but "
                f"{required_by} requires {req_specifier}"
            )
            conflicts = frontier.setdefault("conflicts", [])
            if desc not in conflicts:
                conflicts.append(desc)
    except (ImportError, InvalidSpecifier, InvalidVersion):
        pass


def project_solver_result(
    *,
    solver_result: "SolverResult",
    frontier: dict[str, Any],
) -> None:
    """Project SolverResult into the frontier:
    - unsat_core → frontier['unsat_core']
    - install_commands → candidate_transactions (if not already present)
    """
    # Surface unsat_core in the frontier
    if solver_result.unsat_core:
        unsat_list = frontier.setdefault("unsat_core", [])
        for item in solver_result.unsat_core:
            if item not in unsat_list:
                unsat_list.append(item)

    # Surface install_commands as candidate_transactions
    if solver_result.install_commands:
        transactions = frontier.setdefault("candidate_transactions", [])
        for cmd in solver_result.install_commands:
            if cmd not in transactions:
                transactions.append(cmd)
