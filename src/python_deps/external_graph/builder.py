"""build_external_dependency_graph_slice — thin pipeline of named per-section helpers.

This is a PURE PROJECTION: no PyPI/network fetching in the builder.
It only reads diagnostics + retained ConstraintGraph + SolverResult + classified failure.

See docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md for the full design.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .dto import ExternalDependencyGraphSlice, _MAX_EDGES, _MAX_NODES

if TYPE_CHECKING:
    from ..models import ConstraintGraph, DependencyFailure, DependencyReport, SolverResult


def _derive_python_version(
    diagnostics: "Mapping[str, object]",
    *,
    default: str = "3.11",
) -> str:
    """Derive a bare Python version string (e.g. '3.10') from diagnostics.

    Reads ``diagnostics['python_requires']`` entries whose ``specifier`` field
    contains a lower-bound constraint (``>=`` or ``==``).  Returns the version
    digits from the first usable entry, or ``default`` if none is found.
    """
    python_requires_raw = list(diagnostics.get("python_requires") or [])
    for py_req in python_requires_raw:
        specifier = str(py_req.get("specifier", ""))
        m = re.search(r"(?:>=|==|~=)\s*(\d+\.\d+)", specifier)
        if m:
            return m.group(1)
    return default


def _seed_declared_packages(
    diagnostics: Mapping[str, Any],
    normalize_package_name: Any,
) -> tuple[set[str], dict[str, dict[str, Any]]]:
    """Step 1: Collect declared dependency names (normalized) for 'declared' flag.

    Returns (declared_pkg_names, declared_info).
    """
    declared_raw: list[dict[str, Any]] = list(diagnostics.get("declared_dependencies") or [])
    declared_pkg_names: set[str] = {
        normalize_package_name(str(dep.get("name", "")))
        for dep in declared_raw
        if dep.get("name")
    }
    declared_info: dict[str, dict[str, Any]] = {}
    for dep in declared_raw:
        raw_name = str(dep.get("name", ""))
        if not raw_name:
            continue
        norm = normalize_package_name(raw_name)
        declared_info[norm] = {
            "specifier": str(dep.get("specifier", "")),
            "source": str(dep.get("source", "")),
            "trust": str(dep.get("trust", "high")),
        }
    return declared_pkg_names, declared_info


def _seed_import_mappings(
    diagnostics: Mapping[str, Any],
    normalize_package_name: Any,
) -> dict[str, dict[str, Any]]:
    """Step 2: Collect import → package mappings.

    Returns import_to_pkg: {import_name → {package_name_normalized, source, trust}}.
    """
    mappings_raw: list[dict[str, Any]] = list(diagnostics.get("import_package_mappings") or [])
    import_to_pkg: dict[str, dict[str, Any]] = {}
    for mapping in mappings_raw:
        import_name = str(mapping.get("import_name", ""))
        pkg_name = str(mapping.get("package_name", ""))
        if not import_name or not pkg_name:
            continue
        import_to_pkg[import_name] = {
            "package_name": normalize_package_name(pkg_name),
            "source": str(mapping.get("source", "")),
            "trust": str(mapping.get("trust", "low")),
        }
    return import_to_pkg


def _seed_imports_and_files(
    diagnostics: Mapping[str, Any],
    import_to_pkg: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """Step 3: Process external imports: File nodes, PythonImport nodes, edges.

    Returns (nodes, edges, used_in_code_pkgs).
    """
    imports_raw: list[dict[str, Any]] = list(diagnostics.get("imports") or [])
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    used_in_code_pkgs: set[str] = set()
    seen_files: set[str] = set()
    seen_import_nodes: set[str] = set()

    for imp in imports_raw:
        if str(imp.get("classification", "")) != "external":
            continue
        import_name = str(imp.get("import_name", ""))
        if not import_name:
            continue
        source_files = imp.get("source_files") or []
        if not isinstance(source_files, (list, tuple)):
            source_files = []
        has_source_files = len(source_files) > 0

        import_node_id = f"import:{import_name}"
        if import_node_id not in seen_import_nodes:
            seen_import_nodes.add(import_node_id)
            nodes.append({"id": import_node_id, "kind": "PythonImport"})

        for src_file in source_files:
            src_file = str(src_file)
            if not src_file:
                continue
            if src_file not in seen_files:
                seen_files.add(src_file)
                nodes.append({"id": src_file, "kind": "File"})
            edges.append({"src": src_file, "kind": "imports", "dst": import_node_id})

        if import_name in import_to_pkg:
            norm_pkg = import_to_pkg[import_name]["package_name"]
            if has_source_files:
                used_in_code_pkgs.add(norm_pkg)
            pkg_node_id = f"package:pip:{norm_pkg}"
            edges.append({"src": import_node_id, "kind": "maps_to_package", "dst": pkg_node_id})

    return nodes, edges, used_in_code_pkgs


def _seed_package_nodes(
    import_to_pkg: dict[str, dict[str, Any]],
    declared_pkg_names: set[str],
    declared_info: dict[str, dict[str, Any]],
    used_in_code_pkgs: set[str],
) -> list[dict[str, Any]]:
    """Step 4: Build PythonPackage nodes (union of mapped packages and declared)."""
    all_pkg_names: set[str] = set()
    for info in import_to_pkg.values():
        all_pkg_names.add(info["package_name"])
    for norm in declared_pkg_names:
        all_pkg_names.add(norm)

    pkg_nodes: list[dict[str, Any]] = []
    for norm_pkg in all_pkg_names:
        is_used = norm_pkg in used_in_code_pkgs
        is_declared = norm_pkg in declared_pkg_names
        node: dict[str, Any] = {
            "id": f"package:pip:{norm_pkg}",
            "kind": "PythonPackage",
            "used_in_code": is_used,
            "declared": is_declared,
        }
        if is_declared and norm_pkg in declared_info:
            info = declared_info[norm_pkg]
            if info["specifier"]:
                node["specifier"] = info["specifier"]
            if info["source"]:
                node["source"] = info["source"]
            if info["trust"]:
                node["trust"] = info["trust"]
        pkg_nodes.append(node)
    return pkg_nodes


def _seed_manifests(
    diagnostics: Mapping[str, Any],
    normalize_package_name: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Step 5: Manifest nodes + 'declares' edges from declared_dependencies."""
    declared_raw: list[dict[str, Any]] = list(diagnostics.get("declared_dependencies") or [])
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_manifests: set[str] = set()

    for dep in declared_raw:
        raw_name = str(dep.get("name", ""))
        if not raw_name:
            continue
        norm = normalize_package_name(raw_name)
        source = str(dep.get("source", ""))
        manifest_path = source.split(":")[0] if source else ""
        if not manifest_path:
            continue
        manifest_id = f"manifest:{manifest_path}"
        if manifest_id not in seen_manifests:
            seen_manifests.add(manifest_id)
            nodes.append({"id": manifest_id, "kind": "Manifest"})
        edges.append({"src": manifest_id, "kind": "declares", "dst": f"package:pip:{norm}"})

    return nodes, edges


def _seed_python_runtimes(
    diagnostics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Step 6: PythonRuntime nodes from python_requires entries (static)."""
    python_requires_raw: list[dict[str, Any]] = list(diagnostics.get("python_requires") or [])
    python_runtime_nodes: list[dict[str, Any]] = []
    for py_req in python_requires_raw:
        specifier = str(py_req.get("specifier", ""))
        source = str(py_req.get("source", ""))
        if not specifier:
            continue
        runtime_id = f"python:{specifier}"
        runtime_node: dict[str, Any] = {
            "id": runtime_id,
            "kind": "PythonRuntime",
            "specifier": specifier,
        }
        if source:
            runtime_node["source"] = source
        python_runtime_nodes.append(runtime_node)
    return python_runtime_nodes


def _dedup_and_cap(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    provenance: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Deduplicate nodes/edges and apply caps (≤40 nodes, ≤60 edges).

    When truncation occurs, records a cap_trim provenance note.
    Priority-aware sort: if any node has '_priority', sorts by (_priority, id) ascending.
    """
    # Deduplicate edges
    seen_edge_keys: set[tuple[str, str, str]] = set()
    deduped_edges: list[dict[str, Any]] = []
    for edge in edges:
        key = (str(edge.get("src", "")), str(edge.get("kind", "")), str(edge.get("dst", "")))
        if key not in seen_edge_keys:
            seen_edge_keys.add(key)
            deduped_edges.append(edge)

    # Deduplicate nodes by id (keep first occurrence)
    seen_node_ids: set[str] = set()
    deduped_nodes: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id", ""))
        if node_id not in seen_node_ids:
            seen_node_ids.add(node_id)
            deduped_nodes.append(node)

    # Priority-aware sort before capping
    if any("_priority" in n for n in deduped_nodes):
        deduped_nodes.sort(key=lambda n: (n.get("_priority", 9999), str(n.get("id", ""))))

    dropped_nodes = max(0, len(deduped_nodes) - _MAX_NODES)
    dropped_edges = max(0, len(deduped_edges) - _MAX_EDGES)
    capped_nodes = deduped_nodes[:_MAX_NODES]
    capped_edges = deduped_edges[:_MAX_EDGES]

    if dropped_nodes > 0 or dropped_edges > 0:
        provenance.append({
            "event": "cap_trim",
            "dropped_nodes": dropped_nodes,
            "dropped_edges": dropped_edges,
            "note": (
                f"dropped {dropped_nodes} node(s) and {dropped_edges} edge(s) at "
                f"node/edge cap ({_MAX_NODES} nodes, {_MAX_EDGES} edges)"
            ),
        })

    return capped_nodes, capped_edges, provenance


def _run_static_seed(
    diagnostics: Mapping[str, Any],
    normalize_package_name: Any,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[str],
    set[str],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    """Steps 1-6: Run all static seed phases. Returns (nodes, edges, used_in_code_pkgs,
    declared_pkg_names, declared_info, import_to_pkg, python_runtime_nodes).
    """
    declared_pkg_names, declared_info = _seed_declared_packages(
        diagnostics, normalize_package_name
    )
    import_to_pkg = _seed_import_mappings(diagnostics, normalize_package_name)
    nodes, edges, used_in_code_pkgs = _seed_imports_and_files(diagnostics, import_to_pkg)
    nodes.extend(_seed_package_nodes(
        import_to_pkg, declared_pkg_names, declared_info, used_in_code_pkgs
    ))
    manifest_nodes, manifest_edges = _seed_manifests(diagnostics, normalize_package_name)
    nodes.extend(manifest_nodes)
    edges.extend(manifest_edges)
    python_runtime_nodes = _seed_python_runtimes(diagnostics)
    nodes.extend(python_runtime_nodes)
    return (
        nodes, edges, used_in_code_pkgs,
        declared_pkg_names, declared_info, import_to_pkg, python_runtime_nodes,
    )


def _run_projections(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    frontier: dict[str, Any],
    failure: "DependencyFailure | None",
    report: "DependencyReport | None",
    solver_result: "SolverResult | None",
    constraint_graph: "ConstraintGraph | None",
    import_to_pkg: dict[str, dict[str, Any]],
    used_in_code_pkgs: set[str],
    declared_pkg_names: set[str],
    declared_info: dict[str, dict[str, Any]],
    python_runtime_nodes: list[dict[str, Any]],
    normalize_package_name: Any,
    effective_python_version: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Steps 7a-7c: Project failure neighborhood, constraint graph, solver result."""
    from .projectors import (
        project_constraint_graph,
        project_failure_neighborhood,
        project_solver_result,
    )
    if failure is not None:
        nodes, edges = project_failure_neighborhood(
            failure_type=failure.failure_type,
            failure=failure, report=report,
            nodes=nodes, edges=edges, frontier=frontier,
            import_to_pkg=import_to_pkg, used_in_code_pkgs=used_in_code_pkgs,
            declared_pkg_names=declared_pkg_names, python_runtime_nodes=python_runtime_nodes,
            normalize_package_name=normalize_package_name,
        )
    if constraint_graph is not None:
        nodes, edges = project_constraint_graph(
            constraint_graph=constraint_graph,
            nodes=nodes, edges=edges, frontier=frontier,
            declared_pkg_names=declared_pkg_names, declared_info=declared_info,
            normalize_package_name=normalize_package_name,
            python_version=effective_python_version,
        )
    if solver_result is not None:
        project_solver_result(solver_result=solver_result, frontier=frontier)
    return nodes, edges


def build_external_dependency_graph_slice(  # noqa: PLR0913
    *, diagnostics: Mapping[str, object] | None = None,
    failure: "DependencyFailure | None" = None,
    report: "DependencyReport | None" = None,
    solver_result: "SolverResult | None" = None,
    constraint_graph: "ConstraintGraph | None" = None,
    python_version: str = "3.11",
    python_version_fallback: str | None = None,
) -> ExternalDependencyGraphSlice:
    """Thin pipeline: seed → project → dedup+cap → budget-trim. No network access."""
    from ..import_mapping import normalize_package_name
    if diagnostics is None:
        return ExternalDependencyGraphSlice()
    _fb = python_version_fallback if python_version_fallback is not None else python_version
    effective_python_version = _derive_python_version(diagnostics, default=_fb)
    (
        nodes, edges, used_in_code_pkgs,
        declared_pkg_names, declared_info, import_to_pkg, python_runtime_nodes,
    ) = _run_static_seed(diagnostics, normalize_package_name)
    frontier: dict[str, Any] = {
        "missing_imports": [], "undeclared_used_packages": [],
        "conflicts": [], "runtime_constraints": [], "candidate_transactions": [],
    }
    nodes, edges = _run_projections(
        nodes=nodes, edges=edges, frontier=frontier,
        failure=failure, report=report, solver_result=solver_result,
        constraint_graph=constraint_graph,
        import_to_pkg=import_to_pkg, used_in_code_pkgs=used_in_code_pkgs,
        declared_pkg_names=declared_pkg_names, declared_info=declared_info,
        python_runtime_nodes=python_runtime_nodes,
        normalize_package_name=normalize_package_name,
        effective_python_version=effective_python_version,
    )
    provenance: list[dict[str, Any]] = []
    capped_nodes, capped_edges, provenance = _dedup_and_cap(nodes, edges, provenance)
    final_frontier = {
        k: v for k, v in frontier.items()
        if not (isinstance(v, list) and len(v) == 0)
    }
    return ExternalDependencyGraphSlice(
        nodes=tuple(capped_nodes), edges=tuple(capped_edges),
        frontier=final_frontier if final_frontier else {}, provenance=tuple(provenance),
    ).trim_to_budget()
