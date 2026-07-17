"""Expand a runtime discovery through the SAME oracles that built the graph (spec §7.2).

The governing principle, and the line this project has already paid to learn: the deleted
import->dist identity fallback took wrong-guesses from 6 to 0 by replacing INFERENCE with a
typed `unresolved`. We do NOT reintroduce guessing.

    A runtime discovery is a new DECLARED ROOT. Feed it back through construction.

We never guess what a discovered node needs — we RESOLVE it, with `build_dep_prior` (the
Debian build-deps table + PEP 725 + curated priors, via `seed_build_deps_for`). That is a
resolver, not a guesser.

WHY it pays: every turn is a FULL CONTAINER REBUILD. A serial discovery chain (turn 2:
psycopg2 fails -> turn 3: learn pg_config) costs one rebuild per hop. Resolving the
prerequisites at discovery time collapses the chain into one turn.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from graph.build_deps import seed_build_deps_for
from graph.os_resolver import ObservedNeed, resolve
from graph.schema import DepGraph, NodeType

logger = logging.getLogger(__name__)

# A capability node's id space IS its resolver kind (`binary:pg_config` -> kind "binary").
_NEED_KIND: dict[NodeType, str] = {
    NodeType.TOOL: "binary",
    NodeType.SYSTEM_LIB: "soname",
}


def _resolve_fix(graph: DepGraph, node, executor) -> DepGraph:
    """Give a runtime-discovered CAPABILITY node its OS-package fix.

    Runtime ingest can only report what the log said: "pg_config is missing". It cannot know
    that `libpq-dev` is the Debian package that ships it — that is `os_resolver.resolve`'s job,
    and construction already does exactly this (`build_deps._capability_node` resolves
    `chosen_fix` before it ever emits the node).

    Without this the renderer hands the agent a root with a `check` and a `why` but NO `fix` —
    it says WHERE the build is broken and stays silent on HOW to repair it, which is most of the
    value. The agent then falls back to guessing an apt name from the error text, which is the
    exact behaviour the graph exists to replace. (Spec §6.2 puts `fix` in the record; the demo
    harness is what caught its absence — every unit test hand-built its nodes with a fix already
    set.)

    Never overwrites an existing fix: construction's resolution wins over ours.
    """
    kind = _NEED_KIND.get(node.type)
    if kind is None or node.chosen_fix:
        return graph
    cands = resolve(ObservedNeed(kind, node.name, context="build",
                                 evidence=node.evidence or ""), executor)
    if not cands:
        return graph                      # a table+apt-file miss stays UNRESOLVED, never guessed
    fixes = tuple(f"apt:{c.package}" for c in cands)
    return graph.with_node(replace(
        node,
        fix_candidates=fixes,
        chosen_fix=fixes[0],
        check_command=node.check_command or cands[0].check_command,
    ))


def expand_discovery(graph: DepGraph, node_ids, executor, expanded: set[str] | None = None):
    """Feed each new discovery back through construction's oracles. Two shapes, two oracles:

      a PACKAGE  -> `seed_build_deps_for`: what does this package NEED? (the Debian build-deps
                    prior). Gated on a VERSION: `build_dep_prior` needs one, and expanding an
                    unresolved name would hang a fabricated subtree off a bad anchor.
      a CAPABILITY (TOOL / SystemLib)
                 -> `_resolve_fix`: which OS package PROVIDES this? Ingest knows only that
                    `pg_config` is missing; `libpq-dev` is the resolver's answer, not the log's.

    Returns ``(new_graph, expanded_ids)``. Pass the previous ``expanded`` back in each turn: the
    script re-runs from base every turn so the same failure recurs, and without it we would
    re-hit the network for the same node on every turn.
    """
    done = set(expanded or ())
    new = graph
    for node_id in node_ids or ():
        if node_id in done:
            continue
        node = new.get(node_id)
        if node is None:
            continue
        if node.type is NodeType.PACKAGE:
            if not node.version:
                continue                       # never expandable -> not "expanded", just skipped
        elif node.type not in _NEED_KIND:
            continue                           # not a shape either oracle knows what to do with

        # Mark it ATTEMPTED, not merely succeeded — and do it BEFORE the call. Marking only on
        # success means a node whose expansion throws is retried every single turn: the script
        # re-runs from base each turn, so the same failure recurs forever, and each retry is
        # fresh network/container work in a loop where a turn already costs a full container
        # rebuild. The prior is a best-effort enrichment, never correctness-critical, so one
        # attempt is the right budget: losing an expansion is cheap, re-paying for it every turn
        # is not.
        done.add(node_id)
        try:
            if node.type is NodeType.PACKAGE:
                # seed_build_deps_for returns (graph, pkgs, cap_nodes, aptdep_nodes) — the
                # counters exist because seed_build_deps' log line is asserted on by an
                # existing test; expand_discovery only needs the graph.
                new, _pkgs, _cap_nodes, _aptdep_nodes = seed_build_deps_for(new, node, executor)
            else:
                new = _resolve_fix(new, node, executor)
        except Exception as exc:               # noqa: BLE001 — must never break the run
            logger.warning("expand_discovery: %s skipped: %s", node_id, exc)
    return new, done
