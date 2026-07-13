"""Observation-driven graph update for the react arm (spec Rev 3.3 §7).

Pure except for `certify_only`'s narrow executor call. The heavy lifting —
classify a log line into a Discovery, append-if-new / annotate-if-known, draw the
REQUIRES edge — is ALREADY DONE by `runtime_ingest.ingest_runtime_failures`; this
module supplies the one thing it has always been missing: the OWNER.
"""
from __future__ import annotations

import re

from python_deps.depgraph.naming import normalize_package_name
from python_deps.depgraph.req_slice import _provider_from_command
from python_deps.depgraph.schema import DepGraph, NodeType

# `_provider_from_command` deliberately DROPS the version (`req_slice.py:52` does
# `toks[0].split("==")[0]`), because a provider is identified by name alone. We need it back:
# `pip install psycopg2==2.9.12` names one exact node, and picking a same-named node at a
# different version would hang the discovery off the wrong owner.
_PINNED = re.compile(r"\bpip3?\s+install\b.*?(?:^|\s)([A-Za-z0-9._-]+)==([^\s]+)")


def _pinned_version(command: str) -> str | None:
    m = _PINNED.search(command)
    return m.group(2) if m else None


def owner_node_for_command(graph: DepGraph, command: str | None) -> str | None:
    """`pip install psycopg2==2.9.12` -> `pkg:psycopg2==2.9.12`.

    Returns None when the command names no single package — a batch install, a `-r`/`-c`/`-e`
    install, an apt command, or a name with no Package node. A None owner makes
    `ingest_runtime_failures` fall back to TEST_NODE_ID, which is a flat star with no depth;
    that is why the per-package-install directive (one `pip install` per package) is
    load-bearing and not merely tidy.

    NOTE the two id spaces: `_provider_from_command` returns a PROVIDER id (`pip:psycopg2`);
    graph nodes are keyed `pkg:psycopg2==2.9.12`. So we match on canonical PEP 503 name.

    AMBIGUITY. Package ids bake the version, and `with_node` only collapses an EXACT id match,
    so two Package nodes with the same canonical name but different versions CAN coexist when a
    caller hands in a graph that has not been through `build.reconcile_packages`'s stale-drop
    pass. We never GUESS between them:

      * the command is pinned -> the owner is the node at THAT version. A pinned command names
        exactly one node; resolving it to a different version is simply wrong.
      * the command is unpinned and one node matches -> that node.
      * anything else -> None, and the discovery falls back to the Test node. Losing depth is
        recoverable; attaching a discovery to the wrong package version is not.
    """
    provider = _provider_from_command(command or "")
    if provider is None or not provider.startswith("pip:"):
        return None
    wanted = normalize_package_name(provider.split(":", 1)[1])
    candidates = [
        n for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.name
        and normalize_package_name(n.name) == wanted
    ]
    if not candidates:
        return None

    version = _pinned_version(command or "")
    if version is not None:
        exact = [n for n in candidates if n.version == version]
        if len(exact) == 1:
            return exact[0].id
        # The node may simply not record a version (`package_id(name, None)` -> `pkg:name`).
        # That is still the one node the command names — but only when it is unambiguous.
        if not exact and len(candidates) == 1 and candidates[0].version is None:
            return candidates[0].id
        return None

    return candidates[0].id if len(candidates) == 1 else None
