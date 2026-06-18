"""Host-owned deterministic projection into the contract graph (spec §6). No LLM."""
from __future__ import annotations

import dataclasses
from typing import Any

from . import goals, ids
from .apply import apply_patch
from .extract import extract_blocker_subject, promote_atomic_contracts
from .graph import ContractGraph, depends_on_closure
from .patch import GraphPatch
from .validators import host_satisfied_set


def _verified_test_command_id(events: list[Any]) -> Any | None:
    """Return the step id of the latest rc=0 real test run (not collect-only).

    Generalizes to non-pytest runners (unittest, nose2, etc.) by using the same
    is_test_command() classifier as the authoritative done-gate
    (maintainer._verified_test_run_passed).  The '--collect-only' exclusion and
    the execution-evidence checks (_shows_execution / _shows_pytest_completion)
    are kept intact to preserve honesty.

    Lazy import of maintainer helpers to avoid circular imports at module load.
    """
    from src.envstate.maintainer import (  # noqa: PLC0415
        _get_detector,
        _shows_execution,
        _shows_pytest_completion,
    )

    detector = _get_detector()
    for ev in reversed(list(events)):
        if ev.rc != 0 or not detector.is_test_command(ev.cmd) or "--collect-only" in ev.cmd:
            continue
        out = getattr(ev, "stdout", "") or ""
        if _shows_execution(out) or _shows_pytest_completion(out):
            return ev.step   # any non-None value; CommandExecution nodes no longer exist
    return None


def _failure_signatures(events: list[Any]) -> list[str]:
    """Return the last 400 chars of stdout for every failing command."""
    return [(e.stdout or "")[-400:] for e in events if getattr(e, "rc", 0) != 0]


def _norm(name: str) -> str:
    """Normalise a package/subject name for cross-source matching: pip uses
    hyphens (``email-validator``), import names use underscores (``email_validator``)."""
    return str(name).strip().lower().replace("_", "-")


def _collection_passed(events: list[Any]) -> bool:
    """True iff the LATEST ``pytest --collect-only`` run in the ledger returned rc=0.

    Clean collection is host evidence that every test module imported and was
    collected — enough to satisfy ``repo_tests_collect`` and retire its subtree
    blockers, but NOT ``repo_tests_pass`` (that still needs a real execution via
    ``_verified_test_command_id`` — honesty preserved).
    """
    from src.envstate.maintainer import _get_detector  # noqa: PLC0415
    detector = _get_detector()
    for ev in reversed(list(events)):
        cmd = getattr(ev, "cmd", "") or ""
        if "--collect-only" in cmd and detector.is_test_command(cmd):
            return ev.rc == 0
    return False


def _auto_resolve_blockers(
    graph: ContractGraph,
    present: set[str],
    collection_ok: bool,
) -> tuple[list[Any], set[str]]:
    """Retire active blockers whose violated obligation is now met by FRESH HOST
    FACTS (never the signature regex).  Returns ``(updated_blocker_nodes,
    contract_ids_confirmed_satisfied)``.

    Rule A (presence): a contract the blocker violates has a ``subject`` now
        present in ``present`` (installed / imported-ok / system names).
    Rule B (collection): when ``--collect-only`` rc=0, every blocker violating a
        contract in ``repo_tests_collect``'s depends_on subtree is stale —
        collection imported and collected every test module successfully.
    """
    collect_subtree: set[str] = set()
    if collection_ok:
        collect_id = ids.goal_contract_id("repo_tests_collect")
        collect_subtree = {collect_id} | set(depends_on_closure(graph, collect_id))

    updated: list[Any] = []
    satisfied: set[str] = set()
    for b in graph.blockers():
        if not bool(b.data.get("active", True)):
            continue
        retire = False
        for e in graph.out_edges(b.id, "violates"):
            c = graph.node(e.target)
            if c is None:
                continue
            subj = _norm(c.data.get("subject", ""))
            if subj and (subj in present or subj.replace("lib", "") in present):
                retire = True
                satisfied.add(c.id)
            if collection_ok and e.target in collect_subtree:
                retire = True
                satisfied.add(c.id)
        if retire:
            new_data = dict(b.data)
            new_data["active"] = False
            updated.append(dataclasses.replace(b, data=new_data))
    return updated, satisfied


def refresh_host_graph(
    world_map: Any,
    ledger: Any,
    snapshot: Any,
    exec_readonly: Any,
    current_revision: int,
    *,
    on_error: Any = None,
) -> Any:
    """Seed backbone, promote atomics, auto-resolve blockers, compute host_satisfied.

    Returns a new WorldModelMap (immutable). Idempotent: running twice on the
    same map+ledger produces no extra nodes.
    """
    graph: ContractGraph = world_map.contract_graph
    events = list(ledger.events())

    # 1. seed backbone (idempotent)
    seed_nodes, seed_edges = goals.seed_backbone()
    add_nodes = [n for n in seed_nodes if not graph.has_node(n.id)]
    existing_edges = {(e.source, e.type, e.target) for e in graph.edges}
    add_edges = [e for e in seed_edges if (e.source, e.type, e.target) not in existing_edges]

    # 2. deterministic atomic promotion from failure signatures
    sigs = _failure_signatures(events)
    # Pass graph with backbone already added so promote_atomic_contracts can dedup
    pre_graph = apply_patch(graph, GraphPatch(add_contracts=tuple(add_nodes)))
    promoted = promote_atomic_contracts(pre_graph, sigs)
    add_nodes += [n for n in promoted if not graph.has_node(n.id)]

    graph = apply_patch(graph, GraphPatch(add_contracts=tuple(add_nodes), add_edges=tuple(add_edges)))

    # 3. blocker auto-resolve against fresh host evidence (installed / imported / system)
    installed = {_norm(f.name) for f in world_map.installed}
    system = {_norm(f.name) for f in world_map.system_installed}
    import_ok = {_norm(n) for (n, ok) in (world_map.import_results or ()) if ok}
    present = installed | system | import_ok
    collection_ok = _collection_passed(events)
    resolved, evidence_satisfied = _auto_resolve_blockers(graph, present, collection_ok)
    if resolved:
        graph = apply_patch(graph, GraphPatch(update_blockers=tuple(resolved)))

    # 4. host_satisfied set + done-gate goal certification
    host_satisfied: set[str] = set(host_satisfied_set(graph, world_map, events))
    # contracts confirmed present by fresh host facts this cycle (mirrors the blocker
    # retirements above, so the projected status advances violated -> satisfied).
    host_satisfied |= evidence_satisfied
    # collection success satisfies repo_tests_collect and its whole dep subtree
    # (imports / deps / test-runner) — but NOT repo_tests_pass, which still requires
    # a real execution via the done-gate below.
    if collection_ok:
        collect_id = ids.goal_contract_id("repo_tests_collect")
        host_satisfied.add(collect_id)
        for dep_id in depends_on_closure(graph, collect_id):
            host_satisfied.add(dep_id)
    if world_map.done_flag and _verified_test_command_id(events) is not None:
        host_satisfied.add(goals.GOAL_TESTS_PASS)
        # a real pass implies ALL transitive deps of the goal are satisfied.
        # Use the graph's depends_on closure to cover every dep without
        # hard-coding a partial list (avoids regressions when new deps are added).
        for dep_id in depends_on_closure(graph, goals.GOAL_TESTS_PASS):
            host_satisfied.add(dep_id)

    # Lazy import to break the circular: world_model imports contracts.graph
    # (as a submodule), which triggers contracts/__init__.py, which eagerly loads
    # projection.py — so world_model is only partially initialized at that point.
    from ..world_model import derive_open_problems, merge_map  # noqa: PLC0415
    return merge_map(
        world_map,
        contract_graph=graph,
        host_satisfied=frozenset(host_satisfied),
        open_problems=derive_open_problems(graph),
    )
