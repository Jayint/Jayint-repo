"""Host-owned deterministic projection into the contract graph (spec §6). No LLM."""
from __future__ import annotations

import dataclasses
from typing import Any

from . import goals, ids
from .apply import apply_patch
from .extract import extract_blocker_subject, promote_atomic_contracts
from .graph import ContractGraph
from .patch import GraphPatch
from .validators import host_satisfied_set


def _verified_test_command_id(events: list[Any]) -> Any | None:
    """Return the step id of the latest rc=0 real pytest run (not collect-only).

    Lazy import of maintainer helpers to avoid circular imports at module load.
    """
    from src.envstate.maintainer import _shows_execution, _shows_pytest_completion  # noqa: PLC0415

    for ev in reversed(list(events)):
        if ev.rc != 0 or "pytest" not in ev.cmd or "--collect-only" in ev.cmd:
            continue
        out = getattr(ev, "stdout", "") or ""
        if _shows_execution(out) or _shows_pytest_completion(out):
            return ev.step   # any non-None value; CommandExecution nodes no longer exist
    return None


def _failure_signatures(events: list[Any]) -> list[str]:
    """Return the last 400 chars of stdout for every failing command."""
    return [(e.stdout or "")[-400:] for e in events if getattr(e, "rc", 0) != 0]


def _auto_resolve_blockers(
    graph: ContractGraph,
    installed_names: set[str],
    system_names: set[str],
) -> list[Any]:
    """Return updated Blocker nodes with active=False when their subject is confirmed present."""
    updated = []
    for b in graph.blockers():
        if not bool(b.data.get("active", True)):
            continue
        subj = (b.data.get("metadata") or {}).get("extracted_subject") or ""
        s = subj.lower()
        if s and (s in installed_names or s in system_names or s.replace("lib", "") in system_names):
            new_data = dict(b.data)
            new_data["active"] = False
            updated.append(dataclasses.replace(b, data=new_data))
    return updated


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

    # 3. blocker auto-resolve
    installed = {f.name.lower() for f in world_map.installed}
    system = {f.name.lower() for f in world_map.system_installed}
    resolved = _auto_resolve_blockers(graph, installed, system)
    if resolved:
        graph = apply_patch(graph, GraphPatch(update_blockers=tuple(resolved)))

    # 4. host_satisfied set + done-gate goal certification
    host_satisfied: set[str] = set(host_satisfied_set(graph, world_map, events))
    if world_map.done_flag and _verified_test_command_id(events) is not None:
        from .graph import depends_on_closure
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
