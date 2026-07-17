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

from python_deps.depgraph.repo_modules import (
    repo_modules, stem_collisions, top_level_names,
)
from python_deps.depgraph.scan import (
    _is_excluded_path, scan_imports,
)
from python_deps.depgraph.schema import (
    DiscoveredBy, Layer, Node, NodeType,
)


@dataclass(frozen=True)
class LaneRouting:
    internal: tuple[tuple[str, str], ...]
    external: frozenset[str]
    deferred: frozenset[str]
    modules: tuple[Node, ...]


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
        id=f"module:{top}",
        type=NodeType.MODULE,
        name=top,
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.CLASSIFIER,
        evidence=json.dumps(sorted(dotted_paths)),
    )


def classify(repo_path: str, *, target_stdlib: frozenset[str], declared: frozenset[str]) -> LaneRouting:
    findings, _local, _errors = scan_imports(repo_path)
    tops = top_level_names(repo_path)
    collisions = frozenset(stem_collisions(repo_path))
    declared_norm = frozenset(d.lower().replace("-", "_") for d in declared)

    # Module-node evidence: group repo modules by their top-level name, keeping
    # both the (sys_path_root, path) evidence pairs and the dotted names.
    by_top: dict[str, list[tuple[str, str]]] = {}
    dotted_by_top: dict[str, list[str]] = {}
    for mod in repo_modules(repo_path):
        top = mod.dotted.split(".", 1)[0]
        by_top.setdefault(top, []).append((mod.sys_path_root, mod.path))
        dotted_by_top.setdefault(top, []).append(mod.dotted)

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

    for top in sorted(internal_tops):
        dotteds = dotted_by_top.get(top, [])
        internal.append((top, min(dotteds) if dotteds else top))  # lexicographically-first dotted
    modules = tuple(_module_node(top, tuple(by_top.get(top, ()))) for top in sorted(internal_tops))
    return LaneRouting(
        internal=tuple(internal),
        external=frozenset(external),
        deferred=frozenset(deferred),
        modules=modules,
    )


def apply_routing(graph, routing: LaneRouting):
    """Emit the Module nodes onto a graph. The spine wiring (project→module→
    import replacing the flat Test→Import hub) is the Stage C flip; here we only
    add the Module nodes so the shadow pass can measure them."""
    new = graph
    for node in routing.modules:
        new = new.with_node(node)
    return new
