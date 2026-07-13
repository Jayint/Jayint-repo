"""Observation-driven graph update for the react arm (spec Rev 3.3 §7).

Pure except for `certify_only`'s narrow executor call. The heavy lifting —
classify a log line into a Discovery, append-if-new / annotate-if-known, draw the
REQUIRES edge — is ALREADY DONE by `runtime_ingest.ingest_runtime_failures`; this
module supplies the one thing it has always been missing: the OWNER.
"""
from __future__ import annotations

from python_deps.depgraph.naming import normalize_package_name
from python_deps.depgraph.req_slice import _provider_from_command
from python_deps.depgraph.schema import DepGraph, NodeType


def owner_node_for_command(graph: DepGraph, command: str | None) -> str | None:
    """`pip install psycopg2==2.9.12` -> `pkg:psycopg2==2.9.12`, by canonical name.

    Returns None when the command names no single package — a batch install, a
    `-r`/`-c`/`-e` install, an apt command, or a name with no Package node. A None
    owner makes `ingest_runtime_failures` fall back to TEST_NODE_ID, which is a flat
    star with no depth; that is why the per-package-install directive (one `pip
    install` per package) is load-bearing and not merely tidy.

    NOTE the two id spaces: `_provider_from_command` returns a PROVIDER id
    (`pip:psycopg2`); graph nodes are keyed `pkg:psycopg2==2.9.12`. The version is on
    the NODE, not necessarily in the command, so we match on canonical NAME.

    NOTE on ambiguity: Package ids bake the version (`pkg:name==version`) and
    `DepGraph` is upsert-only (`with_node` only collapses an EXACT id match), so two
    Package nodes with the same canonical name but different ids (a version shift
    across resolve rounds) CAN coexist if a caller hands in a graph that hasn't been
    through `build.reconcile_packages`'s stale-drop pass. When more than one Package
    node shares `wanted`, the LAST one in `graph.nodes` order wins (most recently
    appended) rather than the first — the same tie-break `resolve_link.py`'s
    `canon_to_pkg` lookup already applies to this exact kind of name collision — so
    the result is deterministic and favors the freshest discovery instead of
    silently depending on insertion order.
    """
    provider = _provider_from_command(command or "")
    if provider is None or not provider.startswith("pip:"):
        return None
    wanted = normalize_package_name(provider.split(":", 1)[1])
    owner: str | None = None
    for node in graph.nodes:
        if node.type is NodeType.PACKAGE and normalize_package_name(node.name) == wanted:
            owner = node.id
    return owner
