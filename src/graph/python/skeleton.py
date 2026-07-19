"""Static dependency-graph skeleton: node construction + discovered-cycle stamps.

Split (3c-5) from the former ``core/build.py``: the pure graph/node builders and
the ``discovered_cycle`` restamp vocabulary the two-phase pipeline drives. No
resolve / install / probe orchestration lives here (that is ``fixpoint`` /
``pipeline`` / ``orchestrate``).
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

try:  # tomllib is stdlib on 3.11+; fall back to the tomli backport on 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib

from graph.contracts.executor import Executor
from graph.model import TEST_NODE_ID, project_id
from graph.model import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from graph.python.read.evidence import collect_python_dependency_evidence
from graph.python.util.import_mapping import normalize_package_name

# discovered_cycle stamps, one per discovery stage (design 5.2 example uses 3 for
# probe-discovered SystemLibs). Consumed by the pipeline/orchestrate callers of
# :func:`_restamp`.
_SCAN_CYCLE = 1
_RESOLVER_CYCLE = 2
_PROBE_CYCLE = 3
_CERTIFY_CYCLE = 4

# PEP-503 canonicalizer, aliased to its util-natured owner (one shared
# canonicalizer, not a build-local copy) — see the split note in git history.
_canon = normalize_package_name


def _restamp(graph: DepGraph, node_ids: set[str], cycle: int) -> DepGraph:
    """Return a new graph with ``discovered_cycle = cycle`` on the named nodes."""
    new = graph
    for node_id in node_ids:
        node = new.get(node_id)
        if node is not None:
            new = new.with_node(replace(node, discovered_cycle=cycle))
    return new


def _project_name(repo_path: str) -> str:
    """Project name from ``[project].name`` in pyproject.toml, else dir basename."""
    pyproject = os.path.join(repo_path, "pyproject.toml")
    try:
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        name = (data.get("project") or {}).get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return os.path.basename(repo_path.rstrip("/\\")) or "project"


def _project_build_manifest(repo_path: str) -> str | None:
    """The build manifest that makes the repo editable-installable, or None.

    A ``pip install -e .`` needs DECLARED packaging intent, not merely a file
    named ``pyproject.toml``: many repos ship a ``pyproject.toml`` purely for tool
    config (``[tool.black]``, ``[tool.ruff]``, …) alongside a flat multi-module
    layout that ``pip install -e .`` cannot build (setuptools aborts with
    "Multiple top-level modules discovered in a flat-layout"). Under the rendered
    script's ``set -Eeuo pipefail`` that one line would abort the whole setup.sh —
    a NEW first-pass failure on a repo that never needed the editable install.

    So a ``pyproject.toml`` counts only when it declares ``[project]`` (PEP 621
    metadata) or ``[build-system]`` (PEP 517 backend); a bare ``setup.py`` is the
    legacy installable signal. A tool-config-only pyproject with no ``setup.py``,
    or an unparseable pyproject we cannot confirm, yields None — the renderer then
    emits no editable install (never a command we cannot stand behind).
    """
    pyproject = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        if "project" in data or "build-system" in data:
            return pyproject
    setup_py = os.path.join(repo_path, "setup.py")
    if os.path.isfile(setup_py):
        return setup_py
    return None


def _project_import_target(project_name: str, evidence) -> str | None:
    """The project's own top-level import module to certify-by-import, or None.

    Maps the distribution name to an import name (dash->underscore, lowercased)
    and returns it ONLY when that exact name is one of the repo's own top-level
    modules (``evidence.project_local_modules``). When the import name differs
    from the dist name (``scikit-learn`` -> ``sklearn``) there is no
    tripwire-safe static match, so we return None and leave the Project UNKNOWN
    rather than certify against a guess -- the relink-based mapping (config lane,
    a later task) covers that case with a certified source. ``build.py`` must not
    import ``repo_modules`` (construction-boundary tripwire), so the source here
    is the already-collected ``project_local_modules``.
    """
    canon = project_name.lower().replace("-", "_")
    return canon if canon in set(evidence.project_local_modules) else None


def _add_project_node(graph: DepGraph, repo_path: str) -> DepGraph:
    """Add a Project hub node and connect declared direct deps to it.

    The repo under test is otherwise only reachable through the Test->Import
    chain, so its declared direct dependencies have no shared parent (e.g.
    ``certifi`` had no incoming Package->Package edge).  This node makes "what
    does the project directly require" a single explorable subtree:

    * ``Test --requires--> Project``
    * ``Project --requires--> <runtime declared dep Package>``  (kind=dependency)
    * ``Test --requires--> <test/optional declared dep Package>`` (kind=optional)

    Runtime vs test classification reuses ``evidence`` (kind ``dependency`` vs
    ``optional_dependency``); no new parsing.  Transitive deps still hang off
    their parents, and Import->Package reconciliation is unchanged.
    """
    name = _project_name(repo_path)
    proj_id = project_id(name)
    manifest = _project_build_manifest(repo_path)
    # Collected BEFORE node construction (not after, as before) so
    # soft_requirements_files is available for the node's data at creation time.
    evidence = collect_python_dependency_evidence(repo_path)
    import_target = _project_import_target(name, evidence)
    project_check = f'python -c "import {import_target}"' if import_target else None
    graph = graph.with_node(
        Node(
            id=proj_id,
            type=NodeType.PROJECT,
            name=name,
            layer=Layer.PIP,
            discovered_by=DiscoveredBy.STATIC_SCAN,
            state=State.UNKNOWN,
            check_command=project_check,
            provenance=manifest or repo_path,
            data={
                # installable => the renderer emits `pip install -e .` as the final,
                # post-dependency step (populate/build_script read this flag).
                "installable": manifest is not None,
                # Nested (non-hard-root) requirements files the recursive walk found
                # (see evidence.py / models.PythonDependencyEvidence). Rendered as
                # best-effort, closure-constrained installs by build_script.py — a
                # tuple because Node.data is immutable (MappingProxyType).
                "soft_requirements_files": tuple(evidence.soft_requirements_files),
            },
        )
    )
    graph = graph.with_edge(
        Edge(src=TEST_NODE_ID, dst=proj_id, relation=EdgeType.REQUIRES, origin="project")
    )

    canon_to_pkg = {
        _canon(n.name): n.id for n in graph.nodes if n.type is NodeType.PACKAGE
    }
    for req in evidence.declared_dependencies:
        kind = getattr(req, "kind", "dependency")
        if kind == "constraint":
            continue
        pkg_id = canon_to_pkg.get(_canon(normalize_package_name(req.name)))
        if pkg_id is None:
            continue
        # Declaration is provenance, re-homed to node data (NOT an edge): `direct`
        # for runtime deps, `optional` for test/optional. The compiler installs by
        # node type, and the renders read this flag (see advise.declared_anchor).
        declared = "optional" if kind == "optional_dependency" else "direct"
        graph = graph.with_node(graph.get(pkg_id).with_data(declared=declared))
    return graph


def _pad_python_full(target_python: str) -> str:
    """``"3.13"`` -> ``"3.13.0"`` (padding for a caller-supplied override).

    Mirrors the padding ``resolve_lock._target_env_for`` applies so an
    overridden ``target_python`` still produces a valid ``python_full_version``
    for marker evaluation (``python_full_version < '3.12'`` style forks).
    """
    parts = [p for p in target_python.split(".") if p]
    return ".".join((parts + ["0", "0"])[:3]) if parts else target_python


# Minor-version token in a ``Python 3.13.14`` banner.
_PY_VER_RE = re.compile(r"(\d+\.\d+)")


# Last-resort interpreter version when the container probe yields nothing.
_DEFAULT_TARGET_PYTHON = "3.11"


def _detect_target_python(
    container_executor: Executor, default: str = _DEFAULT_TARGET_PYTHON
) -> str:
    """Probe the container's interpreter minor version (e.g. ``"3.13"``).

    The resolve MUST target the python the container actually runs, or it pins
    versions that have no wheel for that interpreter (observed: a
    3.11-resolved ``pyarrow==2.0.0`` cannot build on a 3.13 container). Tries
    ``python3`` then ``python``, reading both streams (``--version``
    historically printed to stderr). Falls back to ``default`` when nothing
    parses, so a fake/empty executor preserves the legacy 3.11 target.

    Superseded in :func:`build_dep_graph` by :func:`target_env.detect_target_env`
    (Task 7, one combined probe covering python + platform); kept standalone
    (directly unit-tested) as it captures a slightly different signal (a
    ``--version`` banner rather than ``sys.version``) that some callers may
    still want in isolation.
    """
    for cmd in ("python3 --version", "python --version"):
        result = container_executor.run(cmd)
        if not result.ok:
            continue
        m = _PY_VER_RE.search((result.stdout or "") + " " + (result.stderr or ""))
        if m:
            return m.group(1)
    return default


def reconcile_packages(
    graph: DepGraph,
    pkg_nodes: list[Node],
    pkg_edges: list[Edge],
    prev_pkg_ids: set[str],
) -> DepGraph:
    """Merge a fresh resolve round's Package nodes/edges, dropping the prior
    round's stale ones (Phase-A Correction 2c).

    Package ids bake the version (``pkg:name==version``) and ``DepGraph`` is
    upsert-only, so a version shift between rounds would otherwise leave the old
    ``pkg:name==v_old`` node (and its edges) orphaned. Before merging the new
    nodes/edges, remove every Package node the PRIOR round produced
    (``prev_pkg_ids``) that the NEW resolve no longer emits (``without_node`` also
    drops that node's dangling edges), and every prior Package->Package
    ``requires`` edge among still-surviving nodes the new resolve no longer emits
    (``without_edge``). Conflict advisory edges are left untouched. Returns a NEW
    graph; net effect: no stale Package orphan survives a version change.
    """
    new_ids = {n.id for n in pkg_nodes}
    new_edge_keys = {e.key() for e in pkg_edges}
    new = graph
    for stale_id in prev_pkg_ids - new_ids:
        new = new.without_node(stale_id)
    for edge in list(new.edges):
        if (
            edge.relation is EdgeType.REQUIRES
            and edge.src in prev_pkg_ids
            and edge.dst in prev_pkg_ids
            and edge.key() not in new_edge_keys
        ):
            new = new.without_edge(edge)
    for node in pkg_nodes:
        new = new.with_node(node)
    for edge in pkg_edges:
        new = new.with_edge(edge)
    return new


def _stamp_audit(graph: DepGraph, repaired: set[str]) -> DepGraph:
    """Stamp ``discovered_by=AUDIT`` on Package nodes whose canon dist was repaired.

    Each resolve round re-emits a repaired dist's Package as ``RESOLVER`` (fresh
    from the lock), so this runs every round after the merge to keep the AUDIT
    provenance; declared/transitive packages keep ``RESOLVER``.
    """
    new = graph
    for node in graph.nodes:
        if (
            node.type is NodeType.PACKAGE
            and _canon(node.name) in repaired
            and node.discovered_by is not DiscoveredBy.AUDIT
        ):
            new = new.with_node(replace(node, discovered_by=DiscoveredBy.AUDIT))
    return new
