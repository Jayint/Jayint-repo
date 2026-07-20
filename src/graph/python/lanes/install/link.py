"""Package<->import naming + the graph relink pass (spec §4B install/link, R8).

Folded (3c-2) from relink + naming + resolve_link: normalize package/import names,
resolve import->package links against the target env, and relink the graph's
declared-root Import nodes to their installing Package. Pure over the graph +
the injected import-name map.
"""
from __future__ import annotations

import json
from dataclasses import replace

from graph.contracts.executor import Executor
from graph.model import DepGraph, Edge, EdgeType, Node, NodeType
from graph.python.lanes.install.resolve_lock import _canon, _req_name
from graph.python.util.import_mapping import (
    is_unresolved,
    map_import_to_package,
    normalize_package_name,
    top_level_import_name,
)


# === relink.py: relink declared-root Import nodes to their installing Package ===
PACKAGES_DIST_CMD = (
    'python -c "import importlib.metadata, json; '
    'print(json.dumps(importlib.metadata.packages_distributions()))"'
)


def parse_packages_distributions(stdout: str) -> dict[str, list[str]]:
    """Parse the JSON ``{import_name: [dist, ...]}`` map; ``{}`` if malformed."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, val in data.items():
        if isinstance(key, str) and isinstance(val, list):
            out[key] = [v for v in val if isinstance(v, str)]
    return out


def _module_routed_collision_names(graph: DepGraph) -> frozenset[str]:
    """Import top-level names arbitration (Stage C Task 3) routed to a local
    ``Module`` (``routing_arbitrated_local``) and that did NOT fall through — a
    relink edge onto one would LAUNDER a provisional collision (draw a certified
    Import->Package edge to a PyPI dist for a name the local module actually
    provides), so relink must SKIP it. A ``routing_fallthrough`` name is
    deliberately NOT guarded: its edge is kept and its target Package keeps the
    ``data["provisional"]`` marker minted at fallthrough time.

    Read from the Project node's durable verdict stamp. EMPTY pre-flip: the scan
    still drops first-party/local names, so a collision name has no Import node for
    relink to encounter — this guard is a hard no-op today and ACTIVATES at the
    route-not-drop flip (Task 4), when collision names finally carry Import nodes.
    """
    project = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
    if project is None:
        return frozenset()
    local = frozenset(project.data.get("routing_arbitrated_local", ()))
    fell = frozenset(project.data.get("routing_fallthrough", ()))
    return local - fell


def import_to_package_edges(
    graph: DepGraph, dist_map: dict[str, list[str]]
) -> list[Edge]:
    """Certified Import->Package edges from a packages_distributions() map.

    Module keys are real names (``PIL``, ``MySQLdb``) so match case-insensitively;
    distribution names match a Package node by canonical (PEP 503) name. A
    namespace import (multiple dists) links to every dist that is present as a
    Package node. Edges already in the graph are skipped (no duplicates).

    A module-routed collision (``routing_arbitrated_local`` minus
    ``routing_fallthrough`` — see :func:`_module_routed_collision_names`) is never
    linked: relink must not launder a provisional collision onto a PyPI dist. Empty
    pre-flip (no Import nodes for collision names), so byte-identical until the flip.
    """
    pkg_by_canon = {
        normalize_package_name(n.name): n.id
        for n in graph.nodes
        if n.type is NodeType.PACKAGE
    }
    dist_by_module = {module.lower(): dists for module, dists in dist_map.items()}
    existing = {
        (e.src, e.dst) for e in graph.edges if e.relation is EdgeType.REQUIRES
    }
    guarded = _module_routed_collision_names(graph)

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for node in graph.nodes:
        if node.type is not NodeType.IMPORT:
            continue
        if top_level_import_name(node.name) in guarded:
            continue  # module-routed collision: never launder a PyPI dist onto it
        # A first-party import routed to a local Module (``routed_provider='module'``)
        # is provided by that Module, not a PyPI dist — relink must NOT draw an
        # Import->Package edge onto it (that would launder a same-named wheel as its
        # provider). A collision that FELL THROUGH keeps ``routing_fallthrough`` and is
        # NOT stamped module-routed, so this never blocks a genuine fallthrough edge.
        if node.data.get("routed_provider") == "module":
            continue
        module = top_level_import_name(node.name).lower()
        for dist in dist_by_module.get(module, ()):
            pkg_id = pkg_by_canon.get(normalize_package_name(dist))
            if pkg_id is None:
                continue
            key = (node.id, pkg_id)
            if key in existing or key in seen:
                continue
            seen.add(key)
            edges.append(
                Edge(
                    src=node.id,
                    dst=pkg_id,
                    relation=EdgeType.REQUIRES,
                    origin="certified",
                )
            )
    return edges


def flag_unresolved_imports(graph: DepGraph) -> DepGraph:
    """Flag an IMPORT node ``unresolved`` — an honest under-declaration signal,
    NOT a fabricated root — only when it is BOTH unprovided (no outgoing REQUIRES
    edge to a Package after Tier-1 relink) AND non-optional. A guarded import
    (``data["optional"] is True``, tagged by the scan — a try/except-ImportError
    body or an ``if`` branch, e.g. a ``sys.platform``/``sys.version_info`` fork)
    is deliberate, not under-declared, so it is exempt and left unflagged. Test
    goal is separated by the scan; optional imports are exempt. Uses Node.data
    (state is the host-certification axis and does not apply to imports).

    Idempotent: an IMPORT that is now provided OR now known optional has any
    STALE ``unresolved`` flag (and the evidence this function set) cleared, so
    re-running yields the same result regardless of the flag state carried into
    the call. Only the ``unresolved``/evidence keys are touched — every other
    ``data`` key is preserved. Returns a NEW graph.
    """
    provided = _provided_imports(graph)
    new = graph
    for node in graph.nodes:
        if node.type is not NodeType.IMPORT:
            continue
        unprovided = node.id not in provided
        non_optional = node.data.get("optional") is not True
        if unprovided and non_optional:
            new = new.with_node(
                replace(
                    node,
                    data={**dict(node.data), "unresolved": True},
                    evidence=f"unresolved: no distribution provides import {node.name}",
                )
            )
            continue
        # Provided OR optional -> must NOT be flagged. Clear any STALE flag (and
        # the evidence this function set), else leave the node byte-for-byte
        # untouched so the common/first-run path never rewrites needlessly.
        if node.data.get("unresolved") is not True:
            continue
        data = dict(node.data)
        data.pop("unresolved", None)
        new = new.with_node(replace(node, data=data, evidence=None))
    return new


def flag_runtime_import_failure(
    graph: DepGraph, import_node_id: str, *, reason: str
) -> DepGraph:
    """Flag a METADATA-PRESENT Import whose run-time ``import X`` failed for a
    NON-native reason (P2.3, Correction 4) — the third failure class.

    Distinct from ``flag_unresolved_imports``' ``data["unresolved"]`` (metadata
    ABSENT: nothing provides the import name — under-declaration, Phase A): here a
    distribution DOES provide the import (there is an outgoing REQUIRES->Package
    edge, i.e. relink certified a provider), yet ``import X`` still raises for a
    reason that is neither a missing shared library (that path fabricates a
    ``SystemLib``) nor under-declaration (a broken / under-provisioned dist, a
    Python-level ``ImportError``/``RuntimeError`` at import time). Sets
    ``data["unresolved_runtime"] = True`` + a short ``data["import_error"]``
    (``reason``) on the Import node.

    Scoped to METADATA-PRESENT Imports: the flag is applied ONLY when the target is
    an Import node that is PROVIDED (has an outgoing REQUIRES->Package edge). A
    metadata-ABSENT Import is already honestly flagged ``unresolved`` (P0.3) and
    must NOT be double-flagged — an unprovided Import (including one carrying the
    ``unresolved`` flag) is left byte-for-byte untouched. Every other ``data`` key
    is preserved. Returns a NEW graph (a no-op when the target is absent, not an
    Import, or not provided).
    """
    node = graph.get(import_node_id)
    if node is None or node.type is not NodeType.IMPORT:
        return graph
    if node.id not in _provided_imports(graph):
        return graph
    data = {**dict(node.data), "unresolved_runtime": True, "import_error": reason}
    return graph.with_node(replace(node, data=data))


def _provided_imports(graph: DepGraph) -> set[str]:
    """Import-node ids with an outgoing certified REQUIRES->Package edge (a dist
    provides the import name). Mirrors the ``provided`` set in
    ``flag_unresolved_imports`` so metadata-presence is defined identically."""
    return {
        e.src
        for e in graph.edges
        if e.relation is EdgeType.REQUIRES
        and (dst := graph.get(e.dst)) is not None
        and dst.type is NodeType.PACKAGE
    }


def certified_import_links(graph: DepGraph, executor: Executor) -> DepGraph:
    """Stage 4a: add certified Import->Package edges from the container.

    Runs ``packages_distributions()`` in the (post-install) container, links every
    Import to its certified provider Package, then flags every still-unprovided
    non-optional Import ``unresolved`` (P0.3). This is the SOLE Import->Package
    source in construction: with declared-only roots (P0.1) an import never
    becomes a MISSING placeholder Package, so there are no identity-fallback
    ghosts to sweep. On command failure the graph is returned unchanged.
    """
    result = executor.run(PACKAGES_DIST_CMD)
    if not result.ok:
        return graph
    dist_map = parse_packages_distributions(result.stdout)
    edges = import_to_package_edges(graph, dist_map)
    new = graph
    for edge in edges:
        new = new.with_edge(edge)
    return flag_unresolved_imports(new)


# === naming.py: package<->import name normalization + unresolved detection ===
def package_roots(
    graph: DepGraph,
    declared_names: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Return ``(import_id, distribution_name)`` for every Import node.

    Resolution per Import node, highest precedence first:

    1. **declared manifest name** — if a declared distribution's normalized
       name equals the import's normalized name, the declared name wins
       (original manifest form preserved). Project declarations are the
       highest-trust evidence (design 4.2 / 10.3).
    2. **curated table** — ``graph.python.util.import_mapping.map_import_to_package``
       (handles ``cv2 -> opencv-python`` and friends).

    An import that matches neither is **unresolved**
    (``graph.python.util.import_mapping.is_unresolved``) and yields NO root — it is
    never guessed to be its own distribution name.

    Non-Import nodes are ignored. Output order follows graph node order, one
    pair per resolved Import node (unresolved imports are omitted).
    """
    declared = declared_names or set()
    declared_by_normalized = {normalize_package_name(name): name for name in declared}

    roots: list[tuple[str, str]] = []
    for node in graph.nodes:
        if node.type is not NodeType.IMPORT:
            continue
        normalized = normalize_package_name(node.name)
        if normalized in declared_by_normalized:
            roots.append((node.id, declared_by_normalized[normalized]))
            continue
        result = map_import_to_package(node.name, declared_package_names=declared)
        if is_unresolved(result):
            continue
        roots.append((node.id, result.package_name))
    return roots


# === resolve_link.py: resolve import->package links against the target env ===
def _stamp(
    node: Node,
    risk: dict[str, dict],
    target_python: str,
    target_platform: str,
    exclude_newer: str | None = None,
) -> Node:
    """Stamp targeting provenance + native-build risk onto a Package node."""
    changes: dict = {
        "resolved_python": target_python,
        "resolved_platform": target_platform,
        "exclude_newer": exclude_newer,
    }
    info = risk.get(node.name) or risk.get(_canon(node.name))
    if info is None:
        # Case/separator-insensitive fallback.
        for key, val in risk.items():
            if _canon(key) == _canon(node.name):
                info = val
                break
    if info is not None:
        changes["build_from_source"] = info.get("build_from_source")
        changes["artifact"] = info.get("artifact")
        changes["hash"] = info.get("hash")
        # Fix A: the resolver pinned a version with NO installable artifact for
        # this interpreter/platform (only wheels for other interpreters, no
        # sdist). Emitting `pip install --no-deps name==version` can only fail
        # (and, under `set -Eeuo pipefail`, aborts the whole setup.sh). Mark the
        # node UNRESOLVED — drop the pip fix so the renderer never ships the
        # doomed line, and leave honest evidence — so Phase-A's coverage audit
        # flags the import instead of the build dying on it.
        if info.get("installable") is False:
            changes["chosen_fix"] = None
            changes["fix_candidates"] = ()
            changes["data"] = {**node.data, "uninstallable": True}
            changes["evidence"] = (
                f"no installable artifact for python {target_python} on "
                f"{target_platform} (resolved {node.name}=={node.version})"
            )
    return replace(node, **changes)


def _import_edges(
    roots: list[tuple[str | None, str]],
    nodes: list[Node],
) -> list[Edge]:
    """Import->Package edges for each root with a resolved Package node."""
    canon_to_id = {_canon(n.name): n.id for n in nodes}
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for import_id_, dist in roots:
        if import_id_ is None:
            continue  # manifest-declared root: no Import node to attach.
        pkg_id = canon_to_id.get(_canon(_req_name(dist)))
        if pkg_id is None:
            continue
        key = (import_id_, pkg_id)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            Edge(
                src=import_id_,
                dst=pkg_id,
                relation=EdgeType.REQUIRES,
                origin="resolver",
            )
        )
    return edges


def link_imports_to_packages(graph: DepGraph) -> DepGraph:
    """Connect every Import node to its resolved Package by canonical dist name.

    Complements :func:`_import_edges`, which only links imports that were
    themselves resolver roots.  A manifest-declared dependency seeds a Package via
    a root with ``import_id=None`` (see ``roots.select_roots``), so its scanned
    Import node would otherwise be orphaned from the Package — breaking the
    symptom->owner walk.  This pass links any Import whose mapped distribution
    matches a Package node, regardless of how the root was sourced.  ``_canon``
    collapses ``_``/``-``/``.`` so e.g. ``charset_normalizer`` matches
    ``charset-normalizer`` even via the identity fallback.
    """
    canon_to_pkg = {
        _canon(n.name): n.id for n in graph.nodes if n.type is NodeType.PACKAGE
    }
    existing = {
        (e.src, e.dst) for e in graph.edges if e.relation is EdgeType.REQUIRES
    }
    new = graph
    for node in graph.nodes:
        if node.type is not NodeType.IMPORT:
            continue
        result = map_import_to_package(node.name)
        if is_unresolved(result):
            # Reconciliation-by-own-name: an unresolved import can still be linked to a
            # Package that ALREADY EXISTS under the import's own canonical name. This is
            # reconciliation against existing graph state, never fabrication — no new node
            # is created; the edge is drawn only if a matching Package is already present.
            pkg_id = canon_to_pkg.get(_canon(node.name))
        else:
            pkg_id = canon_to_pkg.get(_canon(result.package_name))
        if pkg_id is None or (node.id, pkg_id) in existing:
            continue
        new = new.with_edge(
            Edge(
                src=node.id,
                dst=pkg_id,
                relation=EdgeType.REQUIRES,
                origin="reconcile",
            )
        )
    return new


def _merge(
    primary_nodes: list[Node],
    primary_edges: list[Edge],
    extra_nodes: list[Node],
    extra_edges: list[Edge],
) -> tuple[list[Node], list[Edge]]:
    """Merge node/edge lists; primary entries win on id/edge-key collisions."""
    nodes: list[Node] = list(primary_nodes)
    have_ids = {n.id for n in nodes}
    for n in extra_nodes:
        if n.id not in have_ids:
            have_ids.add(n.id)
            nodes.append(n)

    edges: list[Edge] = list(primary_edges)
    have_keys = {e.key() for e in edges}
    for e in extra_edges:
        if e.key() not in have_keys:
            have_keys.add(e.key())
            edges.append(e)
    return nodes, edges
