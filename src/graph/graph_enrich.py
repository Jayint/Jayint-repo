"""Observation-driven graph update for the react arm (spec Rev 3.3 §7).

Pure except for `certify_only`'s narrow executor call. The heavy lifting —
classify a log line into a Discovery, append-if-new / annotate-if-known, draw the
REQUIRES edge — is ALREADY DONE by `runtime_ingest.ingest_runtime_failures`; this
module supplies the one thing it has always been missing: the OWNER.
"""
from __future__ import annotations

import re

from graph.certify import certify
from graph.diagnose import RepoContext, make_diagnostic_classifier
from graph.naming import normalize_package_name
from graph.req_slice import _provider_from_command
from graph.runtime_ingest import ingest_runtime_failures
from graph.schema import DepGraph, NodeType

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


# Only these pytest phases may touch the graph (spec §4.3, §7.1). "The test's own code
# decided it was wrong" IS the line between "no env fix exists" and "an env fix exists": a
# fixture raising ConnectionRefused is a Service node; an AssertionError in a test BODY is
# NEVER a node, however its message reads. This is structural, not an LLM judgement call —
# `call`/`teardown` causes never reach `ingest_runtime_failures`, full stop.
_ENV_PHASES = frozenset({"collect", "setup"})


def enrich(
    graph: DepGraph, result, causes, ctx: RepoContext
) -> tuple[DepGraph, list[str]]:
    """Append/annotate nodes from this turn's observations. Returns ``(graph, new_node_ids)``.

    Two streams, and they NEVER overlap in the same turn: `loop.py` only runs pytest once the
    build is green, so a turn is either build-stream (`causes` empty) or pytest-stream
    (`result.ok` true, `causes` populated).

      * build stdout  -> owner is EXACT (`owner_node_for_command`) -> this is where DEPTH
        comes from.
      * pytest output -> owner is TEST_NODE_ID (the `ingest_runtime_failures` default when no
        `owner_node_id` is supplied) which is CORRECT: a test-file import genuinely IS a
        direct dependency of the test goal. This is where BREADTH comes from.

    The heavy lifting is `ingest_runtime_failures` — already idempotent, already
    never-raises, already shipping in the v3 arm. We only supply the observations and the
    owner, and gate the pytest stream on phase.
    """
    before = {n.id for n in graph.nodes}
    new = graph
    classifier = make_diagnostic_classifier(ctx)

    if result is not None and not result.ok and result.failing_command:
        owner = owner_node_for_command(new, result.failing_command)
        new, _ = ingest_runtime_failures(
            new,
            [(result.failing_command, result.output or "")],
            classifiers=[classifier],
            owner_node_id=owner,
        )

    obs = [
        (f"pytest: {c.module}", f"{c.exc}: {c.detail}")
        for c in (causes or [])
        if getattr(c, "phase", "call") in _ENV_PHASES
    ]
    if obs:
        new, _ = ingest_runtime_failures(new, obs, classifiers=[classifier])

    return new, [n.id for n in new.nodes if n.id not in before]


def certify_only(
    graph: DepGraph, node_ids, executor, cycle: int = 0
) -> DepGraph:
    """Certify JUST the named nodes against the live container.

    The react loop certifies the install-tier BEFORE it runs tests / processes
    observations (`run_react.build_and_test`, `loop.py` — `g = certify(graph)` ahead of
    `run_tests()`), so anything `enrich` appends *after* that point has never been checked
    and would render `UNKNOWN` — an untested `check_command` and an unverified fix. This is
    the narrow second pass that keeps "the agent is never shown a claim we have not
    verified" honest. Cost is ``O(len(node_ids))``, which is normally zero and occasionally
    a handful. Returns ``graph`` unchanged (same object) when ``node_ids`` is empty — a
    no-op must not even construct a new `DepGraph`.
    """
    if not node_ids:
        return graph
    new = graph
    for node_id in node_ids:
        if new.get(node_id) is not None:
            new = certify(new, node_id, executor, cycle=cycle)
    return new
