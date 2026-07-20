"""The shadow config-lane pass: classify -> cure -> arbitrate, MEASURED, graph
effect DISCARDED. Same code Stage C flips to wired (route-not-drop). Behind a
flag; real construction unaffected."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from graph.python.route.arbitrate import arbitrate
from graph.python.route.classify import classify, probe_target_stdlib
from graph.python.lanes.config.cure import run_cure
from graph.python.invocation_resolver import resolve


@dataclass(frozen=True)
class ShadowRecord:
    repo: str
    n_internal: int
    n_external: int
    n_deferred: int
    cure_ok: bool
    cure_rung: str
    collect_ok: bool
    resolves_local: tuple[str, ...]
    fallthrough: tuple[str, ...]
    unresolved: tuple[str, ...]
    provisional_flags: tuple[str, ...]
    # True when the TARGET stdlib probe was UNAVAILABLE: the pass short-circuited
    # WITHOUT classifying (no empty-set classification), so the partition sizes below
    # are meaningless zeros. The Gate B aggregator excludes such records from every
    # numeric aggregate (``_is_errored``), so they can never silently report garbage.
    probe_unavailable: bool = False


def run_shadow_config_lane(graph, repo_path, container_executor, *, declared) -> ShadowRecord:
    # probe_target_stdlib returns None when unavailable. The invariant "no code path
    # classifies against an empty stdlib set" holds here too: rather than degrade to an
    # empty set (which would misroute a real stdlib name to external and report a garbage
    # partition), the shadow pass short-circuits with a probe_unavailable record the
    # aggregator excludes.
    stdlib = probe_target_stdlib(container_executor)
    if stdlib is None:
        return ShadowRecord(
            repo=repo_path, n_internal=0, n_external=0, n_deferred=0,
            cure_ok=False, cure_rung="", collect_ok=False,
            resolves_local=(), fallthrough=(), unresolved=(), provisional_flags=(),
            probe_unavailable=True,
        )
    routing = classify(repo_path, target_stdlib=stdlib, declared=declared)
    plan = resolve(repo_path)
    cure = run_cure(container_executor, plan)
    arb = arbitrate(container_executor, plan, cure, routing.deferred)
    # a fallthrough is exactly the false-green flag: we would install the PyPI
    # namesake of a name that ALSO exists as a local module.
    return ShadowRecord(
        repo=repo_path,
        n_internal=len(routing.internal),
        n_external=len(routing.external),
        n_deferred=len(routing.deferred),
        cure_ok=cure.ok, cure_rung=cure.rung, collect_ok=cure.collect_ok,
        resolves_local=tuple(sorted(arb.resolves_local)),
        fallthrough=tuple(sorted(arb.fallthrough)),
        unresolved=tuple(sorted(arb.unresolved)),
        provisional_flags=tuple(sorted(arb.fallthrough)),
    )


def _write_shadow_record(record: ShadowRecord) -> None:
    path = os.environ.get("V3_SHADOW_RECORD_PATH")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.__dict__) + "\n")
