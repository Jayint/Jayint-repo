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
    # mapping every finding's source files back to their top-level module
    # (``repo_modules``). Self-references (``owner == import_top``) are omitted (the
    # top-level import of a module's own package). ``wire_spine`` draws only the
    # pairs whose BOTH endpoints exist as nodes, so excluded/private drops never
    # produce a dangling edge.
    spine: tuple[tuple[str, str], ...] = ()
    # Internal-import top-level names to draw an ``import(X) -> module(X)`` resolution
    # edge for (X internal, imported by a different module).
    resolution: frozenset[str] = frozenset()


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

    # Ladder order is a SAFETY property (review §, F4 adjudication):
    #   0. ``_``-prefix       -> drop (classifier owns it; private/typing, never a dist)
    #   1. declared           -> external  ("you never declare your own modules")
    #   2. stdlib             -> drop  (TARGET probe OR the host-scan classification as a
    #                                    backstop, so an empty/failed probe cannot leak a
    #                                    stdlib name into the install lane)
    #   3. repo top-level     -> internal (sys.path-accurate)
    #   collision / excluded-dir / project-local-not-a-top -> collision zone (deferred),
    #                          gating only names NOT already routed declared/stdlib/internal,
    #                          so a name is NEVER clear-external unless the ladder proved it so.
    #   4. otherwise          -> external (clear external candidate)
    for finding in findings:
        name = finding.import_name
        top = name.split(".", 1)[0]
        if top.startswith("_"):
            continue                                   # rung 0: private/typing drop (classifier)
        if top in declared_norm:
            external.add(name)                         # rung 1: declared wins -> external
            continue
        if top in target_stdlib or finding.classification == "stdlib":
            continue                                   # rung 2: stdlib -> drop (target OR host backstop)
        if top in tops:
            internal_tops.add(top)                     # rung 3: sys.path-accurate -> internal
            continue
        if top in collisions:
            deferred.add(top)                          # collision zone (non-declared, non-top)
            continue
        # excluded-dir-ONLY imports (seen only under examples/docs/scripts/tools).
        # A locally-shadowed one routes to the collision zone (the §12 false-green:
        # a conftest sys.path.insert + a local shadow); a pure external there is out
        # of "run the repo properly" scope and is DROPPED — never clear-external, so
        # it never installs (pre-flip parity; keeps setup.sh byte-identical).
        in_scope = tuple(f for f in finding.source_files if not _is_excluded_path(f))
        if finding.source_files and not in_scope:
            if top in by_top:
                deferred.add(top)
            continue
        # A name the host scan flagged project-local but the sys.path-accurate tops
        # rung did NOT catch (the two walks disagreed) must NEVER be treated as a
        # clear external dist — defer it (arbitration decides), never install it.
        if finding.classification == "project_local":
            deferred.add(top)
            continue
        external.add(name)                             # rung 4: clear external

    suspect = _namespace_suspect_tops(repo_path)
    deferred.update(internal_tops & suspect)
    internal_tops -= suspect

    for top in sorted(internal_tops):
        dotteds = dotted_by_top.get(top, [])
        internal.append((top, min(dotteds) if dotteds else top))  # lexicographically-first dotted

    # Spine attribution: map EVERY finding's source files back to their owning
    # top-level module (``path_to_top``). ``owner --imports--> import(name)``, top-level
    # self-references (an import of the owner module's OWN top) omitted. Covers both
    # external imports (``module -> import -> package``) and internal cross-module
    # imports (``module -> import -> module``); ``wire_spine`` gates each edge on node
    # existence, so a stdlib/private/excluded drop (its Import node removed by
    # ``apply_routing``) never yields a dangling edge.
    spine_pairs: set[tuple[str, str]] = set()
    for finding in findings:
        imp_top = finding.import_name
        for source_file in finding.source_files:
            owner = path_to_top.get(source_file)
            if owner is not None and owner != imp_top:
                spine_pairs.add((owner, imp_top))

    # Module nodes = the repo's top-level first-party modules that PARTICIPATE in the
    # dependency spine: the imported-internal set (``internal_tops``) PLUS every
    # top-level module that OWNS an import (a spine ``owner``), so a flat repo whose
    # top-level module is never itself imported (``app.py`` importing ``requests``)
    # still anchors ``project -> module(app) -> import``. A declared name (rung 1,
    # external), a collision, or a namespace-suspect top is NOT a first-party module.
    owner_tops = {
        owner for owner, _imp in spine_pairs
        if owner not in declared_norm and owner not in collisions and owner not in suspect
    }
    module_tops = internal_tops | owner_tops
    modules = tuple(
        _module_node(top, tuple(by_top.get(top, ()))) for top in sorted(module_tops)
    )

    # Internal resolution: ``import(X) --requires--> module(X)`` for an internal name X
    # imported by a DIFFERENT module (a non-self spine pair). The top-level self-ref
    # (module app importing its own top ``app``) is already omitted from ``spine_pairs``,
    # so an X here is imported by some owner != X (review F3).
    resolution = frozenset(
        imp for _owner, imp in spine_pairs if imp in internal_tops
    )

    return LaneRouting(
        internal=tuple(internal),
        external=frozenset(external),
        deferred=frozenset(deferred),
        modules=modules,
        spine=tuple(sorted(spine_pairs)),
        resolution=resolution,
    )


def _keep_tops(routing: LaneRouting) -> frozenset[str]:
    """Top-level import names the classifier ROUTED (external | internal | deferred).

    THE safety key: an Import node survives ``apply_routing`` only if its name is in
    this set. Anything the ladder dropped (stdlib, ``_``-prefix, an unroutable name)
    is REMOVED, and an Import node reaches the Phase-A fixpoint ONLY if its name is in
    ``external`` — so the classifier is the sole authority on what installs."""
    return (
        frozenset(n.split(".", 1)[0] for n in routing.external)
        | {top for top, _dotted in routing.internal}
        | routing.deferred
    )


def apply_routing(graph, routing: LaneRouting):
    """Route the raw scan graph: DROP unroutable Import nodes, STAMP first-party ones,
    ATTACH Module nodes (route-not-drop; the four scan drops now land here).

    Runs BEFORE the Phase-A fixpoint. The classifier owns every drop the pre-flip
    ``scan`` did: an Import node whose name the ladder did not route (stdlib,
    ``_``-prefix, an unroutable local) is REMOVED here, and every internal-routed
    Import node is stamped ``data['routed_provider']='module'`` — the flag the
    lane-aware fixpoint filter (``fixpoint._missing_import_nodes``) reads to keep a
    first-party name out of the repair bound and away from the dist-guesser. The spine
    EDGES (``project->module``, ``module->import``, ``import->module``) are drawn
    post-Project by :func:`wire_spine`. Additive to render: Module/Import nodes carry
    no recipe, so the emitted ``setup.sh`` is unchanged."""
    internal_tops = {top for top, _dotted in routing.internal}
    keep = _keep_tops(routing)
    new = graph
    for node in routing.modules:
        new = new.with_node(node)
    for node in graph.nodes:
        if node.type is not NodeType.IMPORT:
            continue
        top = node.name.split(".", 1)[0]
        if top not in keep:
            new = new.without_node(node.id)            # ladder-dropped: never installs
            continue
        if top in internal_tops and node.data.get("routed_provider") != "module":
            new = new.with_node(node.with_data(routed_provider="module"))
    return new


def wire_spine(graph, routing: LaneRouting):
    """Draw the goal spine's config-lane edges — the flat ``Test -> Import`` hub's
    replacement — once the Project node exists.

    * ``project --requires--> module(top)`` (``origin='contains'``) for every
      first-party Module node (``routing.modules``).
    * ``module(owner) --requires--> import(name)`` (``origin='imports'``) for each
      ``routing.spine`` pair whose BOTH endpoints exist.
    * ``import(X) --requires--> module(X)`` (``origin='resolves'``) for each internal
      name in ``routing.resolution`` — an internal import of ANOTHER module resolves to
      its providing Module. Top-level self-references (an import of the owner module's
      own top) are already excluded upstream, per the spec.

    Both endpoints of every edge are gated on existence, so a stdlib/private/excluded
    drop (its Import node removed by ``apply_routing``) never dangles. A pure graph
    transform (no ``repo_modules``); returns a NEW graph."""
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
    for imp_top in routing.resolution:
        imp_id, mod_id = import_id(imp_top), module_id(imp_top)
        if new.get(imp_id) is not None and new.get(mod_id) is not None:
            new = new.with_edge(
                Edge(src=imp_id, dst=mod_id,
                     relation=EdgeType.REQUIRES, origin="resolves")
            )
    return new
