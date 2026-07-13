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

from python_deps.depgraph.build_deps import seed_build_deps_for
from python_deps.depgraph.schema import DepGraph, NodeType

logger = logging.getLogger(__name__)


def expand_discovery(graph: DepGraph, node_ids, executor, expanded: set[str] | None = None):
    """Resolve the system-tier prerequisites of newly discovered PACKAGE nodes.

    Returns ``(new_graph, expanded_ids)``. Pass the previous ``expanded`` back in each turn:
    the script re-runs from base every turn so the same failure recurs, and without it we
    would re-hit the network with build_dep_prior on every turn for the same node.

    GATED: only a package with a VERSION is expanded. ``build_dep_prior`` needs one
    (``build_deps.py`` skips versionless packages), and expanding an unresolved name would
    hang a whole fabricated subtree off a bad anchor. No version -> expand NOTHING.
    """
    done = set(expanded or ())
    new = graph
    for node_id in node_ids or ():
        if node_id in done:
            continue
        node = new.get(node_id)
        if node is None or node.type is not NodeType.PACKAGE or not node.version:
            continue
        # Mark it ATTEMPTED, not merely succeeded — and do it BEFORE the call. Marking only on
        # success means a node whose expansion throws is retried every single turn: the script
        # re-runs from base each turn, so the same failure recurs forever, and each retry is
        # fresh network/container work in a loop where a turn already costs a full container
        # rebuild. The prior is a best-effort enrichment, never correctness-critical, so one
        # attempt is the right budget: losing an expansion is cheap, re-paying for it every turn
        # is not.
        done.add(node_id)
        try:
            # seed_build_deps_for returns (graph, pkgs, cap_nodes, aptdep_nodes) — the
            # counters exist because seed_build_deps' log line is asserted on by an
            # existing test; expand_discovery only needs the graph.
            new, _pkgs, _cap_nodes, _aptdep_nodes = seed_build_deps_for(new, node, executor)
        except Exception as exc:               # noqa: BLE001 — must never break the run
            logger.warning("expand_discovery: %s skipped: %s", node_id, exc)
    return new, done
