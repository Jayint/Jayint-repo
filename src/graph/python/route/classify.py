"""Pure static lane classifier for the two-lane collection graph.

For each scanned top-level import name, a sys.path-accurate ladder routes it:
  1. declared in a manifest        -> external (install lane); you never declare
                                       your own modules.
  2. in the TARGET interpreter's stdlib -> drop.
  3. in the repo's sys.path-accurate top-level set -> internal (config lane; a
                                       local Module node).
  4. otherwise                     -> external candidate (install lane).

The residue that is BOTH a repo module AND a real PyPI dist (``stem_collisions``)
is NOT statically decidable and is routed to the collision zone (deferred),
arbitrated only post-cure by ``arbitrate.py``. Excluded-dir-only locals
(examples/scripts/tools) also route to the collision zone, never clear-external,
because ``SKIP_WALK_DIRS`` hides them from both ``top_level_names`` and
``stem_collisions`` (review §12).

Pure: no container, no execution, no LLM. Sole sanctioned consumer of
``repo_modules``/``stem_collisions``. The ``target_stdlib`` set is injected by the
caller (a one-shot container probe) so this stays pure while using the TARGET's
stdlib, never a host fallback (review §17).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from graph.python.read.repo_modules import (
    repo_modules, stem_collisions, top_level_names,
)
from graph.python.read.scan import (
    _is_excluded_path, scan_imports,
)
from graph.model import (
    DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, import_id,
)


def module_id(top: str) -> str:
    """Node id for a first-party ``Module`` node (config-cured lane)."""
    return f"module:{top}"


@dataclass(frozen=True)
class LaneRouting:
    internal: tuple[tuple[str, str], ...]
    external: frozenset[str]
    deferred: frozenset[str]
    modules: tuple[Node, ...]
    # Spine attribution: sorted, unique ``(module_top, import_top)`` pairs — each
    # is a prospective ``module(owner) --imports--> import(name)`` edge, derived by
    # mapping every external finding's source files back to their top-level module
    # (``repo_modules``). Self-references (``owner == import_top``) are omitted (the
    # top-level import of a module's own package). ``wire_spine`` draws only the
    # pairs whose BOTH endpoints exist as nodes, so excluded/private drops never
    # produce a dangling edge.
    spine: tuple[tuple[str, str], ...] = ()


_STDLIB_PROBE = (
    "python3 -c \"import sys,json;"
    "print(json.dumps(sorted(getattr(sys,'stdlib_module_names',()) "
    "or sys.builtin_module_names)))\""
)


def probe_target_stdlib(executor) -> frozenset[str]:
    """One-shot: the TARGET container's own stdlib module names. Uses
    ``sys.stdlib_module_names`` (3.10+); falls back to ``builtin_module_names``
    on 3.9. Never a host fallback — the executor is the target."""
    result = executor.run(_STDLIB_PROBE, timeout=60)
    if not result.ok:
        return frozenset()
    try:
        return frozenset(json.loads(result.stdout.strip()))
    except (ValueError, TypeError):
        return frozenset()


def _module_node(top: str, dotted_paths: tuple[tuple[str, str], ...]) -> Node:
    """A top-level local Module node. Evidence is the tuple of (sys_path_root,
    path) pairs (JSON) so two dirs each defining ``utils`` don't collapse into a
    false single-provider node (review §14)."""
    return Node(
        id=module_id(top),
        type=NodeType.MODULE,
        name=top,
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.CLASSIFIER,
        evidence=json.dumps(sorted(dotted_paths)),
    )


def _namespace_suspect_tops(repo_path: str) -> frozenset[str]:
    """Top-level names whose sys.path root is a declared package root that has NO
    ``__init__.py`` at the intermediate level — i.e. minted by the climb stopping
    one dir too low under a PEP 420 namespace (review §6). Constrained to declared
    package roots so an ordinary flat top-level is never falsely suspected."""
    from pathlib import Path
    from graph.python.invocation_resolver import _find_project_dirs
    repo = Path(repo_path)
    roots = {repo / d for d, _ in [(r, None) for r in ("src",)] if (repo / d).is_dir()}
    project_dirs, _mono = _find_project_dirs(repo)
    for rel in project_dirs:
        p = repo if rel == "." else repo / rel
        if (p / "src").is_dir():
            roots.add(p / "src")
    suspect: set[str] = set()
    for root in roots:
        for child in root.iterdir() if root.is_dir() else ():
            # a dir with no __init__.py whose SUBDIRS contain packages == a PEP 420
            # namespace: its children are the real subpackages, not top-levels.
            if child.is_dir() and not (child / "__init__.py").is_file():
                if any((g / "__init__.py").is_file() for g in child.iterdir() if g.is_dir()):
                    suspect.update(
                        g.name for g in child.iterdir()
                        if g.is_dir() and (g / "__init__.py").is_file()
                    )
    return frozenset(suspect)


def classify(repo_path: str, *, target_stdlib: frozenset[str], declared: frozenset[str]) -> LaneRouting:
    findings, _local, _errors = scan_imports(repo_path)
    tops = top_level_names(repo_path)
    collisions = frozenset(stem_collisions(repo_path))
    declared_norm = frozenset(d.lower().replace("-", "_") for d in declared)

    # Module-node evidence: group repo modules by their top-level name, keeping
    # both the (sys_path_root, path) evidence pairs and the dotted names. The
    # ``path -> top`` map (same walk) attributes each import's source files back to
    # the owning top-level module for the ``module -> import`` spine edges.
    by_top: dict[str, list[tuple[str, str]]] = {}
    dotted_by_top: dict[str, list[str]] = {}
    path_to_top: dict[str, str] = {}
    for mod in repo_modules(repo_path):
        top = mod.dotted.split(".", 1)[0]
        by_top.setdefault(top, []).append((mod.sys_path_root, mod.path))
        dotted_by_top.setdefault(top, []).append(mod.dotted)
        path_to_top[mod.path] = top

    internal: list[tuple[str, str]] = []
    external: set[str] = set()
    deferred: set[str] = set()
    internal_tops: set[str] = set()

    for finding in findings:
        name = finding.import_name
        top = name.split(".", 1)[0]
        if top.startswith("_"):
            continue                                   # relocated drop: private/typing
        if top in collisions:
            deferred.add(top)                          # collision zone (rung 3.5)
            continue
        if top in declared_norm:
            external.add(name)                         # rung 1: declared → external
            continue
        if top in target_stdlib:
            continue                                   # rung 2: stdlib → drop
        if top in tops:
            internal_tops.add(top)                     # rung 3: sys.path-accurate → internal
            continue
        # excluded-dir-only locals are invisible to tops AND collisions: route
        # to the collision zone, never clear-external (review §12).
        in_scope = tuple(f for f in finding.source_files if not _is_excluded_path(f))
        if finding.source_files and not in_scope and top in by_top:
            deferred.add(top)
            continue
        external.add(name)                             # rung 4: external

    suspect = _namespace_suspect_tops(repo_path)
    deferred.update(internal_tops & suspect)
    internal_tops -= suspect

    for top in sorted(internal_tops):
        dotteds = dotted_by_top.get(top, [])
        internal.append((top, min(dotteds) if dotteds else top))  # lexicographically-first dotted

    # Spine attribution: map each external finding's source files back to their
    # owning top-level module (``path_to_top``). ``owner --imports--> import(name)``,
    # self-references omitted. Restricted to ``external`` findings (the classification
    # that mints an Import node); ``wire_spine`` further gates on node existence, so
    # a private/excluded-dir drop never yields a dangling edge.
    spine_pairs: set[tuple[str, str]] = set()
    for finding in findings:
        if finding.classification != "external":
            continue
        imp_top = finding.import_name
        for source_file in finding.source_files:
            owner = path_to_top.get(source_file)
            if owner is not None and owner != imp_top:
                spine_pairs.add((owner, imp_top))

    # Module nodes = the repo's top-level first-party modules that PARTICIPATE in the
    # dependency spine: the imported-internal reconciliation set (``internal_tops``)
    # PLUS every top-level module that OWNS an external import (a spine ``owner``),
    # so a flat repo whose top-level module is never itself imported (``app.py``
    # importing ``requests``) still anchors ``project -> module(app) -> import``. A
    # declared name (rung 1, external — "you never declare your own modules"), a
    # collision, or a namespace-suspect top is NOT a first-party module node.
    owner_tops = {
        owner for owner, _imp in spine_pairs
        if owner not in declared_norm and owner not in collisions and owner not in suspect
    }
    module_tops = internal_tops | owner_tops
    modules = tuple(
        _module_node(top, tuple(by_top.get(top, ()))) for top in sorted(module_tops)
    )

    return LaneRouting(
        internal=tuple(internal),
        external=frozenset(external),
        deferred=frozenset(deferred),
        modules=modules,
        spine=tuple(sorted(spine_pairs)),
    )


def apply_routing(graph, routing: LaneRouting):
    """Emit the Module nodes AND stamp first-party import nodes (route-not-drop).

    Runs BEFORE the Phase-A fixpoint: it attaches the edge-less ``Module`` nodes and
    stamps ``data['routed_provider']='module'`` on every Import node whose top-level
    name the classifier routed internal — that is the flag the lane-aware fixpoint
    filter (``fixpoint._missing_import_nodes``) reads to keep a first-party name out
    of the repair bound and away from the dist-guesser. The spine EDGES
    (``project->module``, ``module->import``) are drawn post-Project by
    :func:`wire_spine`, since they need the Project node. Additive to render: Module/
    Import nodes carry no recipe, so the emitted ``setup.sh`` is unchanged."""
    internal_tops = {top for top, _dotted in routing.internal}
    new = graph
    for node in routing.modules:
        new = new.with_node(node)
    for node in graph.nodes:
        if (
            node.type is NodeType.IMPORT
            and node.name.split(".", 1)[0] in internal_tops
            and node.data.get("routed_provider") != "module"
        ):
            new = new.with_node(node.with_data(routed_provider="module"))
    return new


def wire_spine(graph, routing: LaneRouting):
    """Draw the goal spine's config-lane edges — the flat ``Test -> Import`` hub's
    replacement — once the Project node exists.

    * ``project --requires--> module(top)`` (``origin='contains'``) for every
      first-party Module node (``routing.modules``).
    * ``module(owner) --requires--> import(name)`` (``origin='imports'``) for each
      ``routing.spine`` pair whose BOTH endpoints exist.

    ``import --requires--> module`` (internal resolution) is intentionally absent:
    findings are top-level-aggregated, so an internal import resolves to its own
    module — a top-level self-reference the spec omits. Both endpoints of every edge
    are gated on existence, so a private/excluded-dir drop never dangles. A pure
    graph transform (no ``repo_modules``); returns a NEW graph."""
    project = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
    new = graph
    if project is not None:
        for module in routing.modules:
            if new.get(module.id) is not None:
                new = new.with_edge(
                    Edge(src=project.id, dst=module.id,
                         relation=EdgeType.REQUIRES, origin="contains")
                )
    for owner, imp_top in routing.spine:
        mod_id, imp_id = module_id(owner), import_id(imp_top)
        if new.get(mod_id) is not None and new.get(imp_id) is not None:
            new = new.with_edge(
                Edge(src=mod_id, dst=imp_id,
                     relation=EdgeType.REQUIRES, origin="imports")
            )
    return new
