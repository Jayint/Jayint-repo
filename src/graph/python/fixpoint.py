"""Phase-A repair fixpoint: bounded resolve -> install -> audit -> repair.

Split (3c-5, Rule A) from the former ``core/build.py``: the ``_phase_a_fixpoint``
loop and its pure ``_missing_import_nodes`` helper. Audits the runtime import set
against the RESOLVED closure's RECORD-union coverage each round and repairs an
under-declared import by grounding a candidate dist + adding an AUDIT root, until
coverage is stable. The pipeline (Phase 1) calls this; the ``resolve_closure`` /
coverage / candidate-provider seams it patches in tests are module-level imports
here so a ``monkeypatch.setattr(fixpoint, ...)`` reaches the call site.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import MappingProxyType

from graph.contracts.executor import Executor
from graph.model import DepGraph, NodeType
from graph.python.lanes.install.closure import install_closure
from graph.python.lanes.install.ground import (
    DistGuesser,
    RecordProvider,
    Verdict,
    choose_provider,
    declared_candidates,
    declared_coverage,
    generate_candidates,
    resolved_record_coverage,
)
from graph.python.lanes.install.resolve import _req_name, resolve_closure
from graph.python.skeleton import _stamp_audit, reconcile_packages
from graph.python.util.import_mapping import normalize_package_name, top_level_import_name

logger = logging.getLogger(__name__)

# Numeric backstop on Phase-A repair rounds (Correction 2b): the attempted-set is
# the honest terminator; this caps pathological non-convergence.
_MAX_REPAIR_ROUNDS = 5


@dataclass
class _FixpointResumeState:
    """Mutable carry that lets a fallthrough RE-ENTRY (Stage C Task 3) resume the
    Phase-A fixpoint instead of restarting it. The first Phase-A run POPULATES it;
    a re-entry passes the SAME object so it:

    * seeds ``prev_pkg_ids`` from ``resolved_pkg_ids`` — the ids of the LAST resolve
      round's closure ONLY, never the whole graph — so ``reconcile_packages`` cannot
      drop a pre-existing ``State.MISSING`` placeholder the resolver deliberately
      excluded (a ``[tool.uv.sources]`` / direct-reference node): those were added
      OUTSIDE the fixpoint and are absent from every round's closure, so they must
      never enter the prior-id set that reconcile prunes against (F3); and

    * seeds ``attempted`` with every ``(import, candidate)`` pair the first run
      already tried, so re-entry does NOT re-attempt a name Phase A gave up on at
      its bound / no-new-candidate exit (F2).

    Mutated in place (the fixpoint already threads mutable ``repaired``/``attempted``
    sets internally); ``None`` at the call site keeps the from-scratch run
    byte-identical (empty prior set drops nothing, empty attempted set re-tries).
    """

    attempted: set[tuple[str, str]] = field(default_factory=set)
    resolved_pkg_ids: set[str] = field(default_factory=set)

# PEP-503 canonicalizer, aliased to its util-natured owner (one shared
# canonicalizer, not a build-local copy) — see the split note in git history.
_canon = normalize_package_name


def _missing_import_nodes(graph, *, provided: frozenset[str], deferred: frozenset[str]):
    """Non-optional IMPORT nodes no resolved dist provides — LANE-AWARE: also
    excludes Module-routed imports and deferred-collision names so first-party
    names never inflate the repair bound nor reach the dist-guesser. Vacuous when
    no node is Module-routed and ``deferred`` is empty (today's real construction)."""
    return [
        n for n in graph.nodes
        if n.type is NodeType.IMPORT
        and n.data.get("optional") is not True
        and n.data.get("routed_provider") != "module"
        and n.name.split(".", 1)[0] not in deferred
        and top_level_import_name(n.name).lower() not in provided
    ]


def _phase_a_fixpoint(
    graph: DepGraph,
    roots: list[tuple[str | None, str]],
    host_executor: Executor,
    container_executor: Executor,
    record_provider: RecordProvider,
    *,
    target_env,
    exclude_newer: str | None,
    needed_extras: frozenset[str],
    declared_package_names: frozenset[str] = frozenset(),
    declared_dists: frozenset[str] = frozenset(),
    uv_sourced_names: frozenset[str] = frozenset(),
    uv_sources=MappingProxyType({}),
    uv_indexes: tuple[dict, ...] = (),
    workspace_members: tuple[str, ...] = (),
    repo_path: str | None = None,
    llm: DistGuesser | None = None,
    deferred: frozenset[str] = frozenset(),
    resume: "_FixpointResumeState | None" = None,
) -> DepGraph:
    """Bounded resolve -> install -> look -> repair fixpoint (Phase A).

    Each round resolves the current roots, reconciles the Package layer (dropping
    stale nodes/edges — Correction 2c), install-probes the closure, then audits
    the FULL runtime import set against the resolved closure's RECORD-union
    coverage (Correction 3 — never ``packages_distributions``). A non-optional
    import that no resolved dist provides is repaired by grounding candidate dists
    (repo-DECLARED soft-requirement dists that RECORD-cover the import first, then
    the vendored pipreqs map, else an injected LLM guesser on a map miss, each
    RECORD-grounded via ``record_provider``) and, on an unambiguous ACCEPT,
    adding the dist as an AUDIT root
    (``audit_root_names`` threads the repaired set into the resolve retry so a
    declared root is never evicted — Correction 2a) and re-resolving.

    Terminates when coverage is complete, when no new ``(import, candidate)`` pair
    can be proposed (attempted-set / fixpoint — Correction 2b), or at the numeric
    bound ``min(initial_missing, 5)``; residue is left for P0.3 to flag
    unresolved downstream — construction is NEVER aborted. Every round returns a
    NEW graph; the orchestrator only rebinds ``graph``.

    ``resume`` (Stage C Task 3, default ``None``) is the :class:`_FixpointResumeState`
    a fallthrough RE-ENTRY threads so it resumes rather than restarts: it seeds the
    prior-round Package-id set from the LAST closure only (so a version-shift
    reconcile drops the stale ``pkg:name==old`` WITHOUT touching a pre-existing
    ``State.MISSING`` uv-source/direct-reference placeholder — F3) and seeds the
    attempted ``(import, candidate)`` set (so re-entry never re-attempts a name the
    first run gave up on at its bound / no-new-candidate exit — F2). The first run
    POPULATES the same object (attempted pairs + the final closure ids). ``None``
    keeps the from-scratch run byte-identical.
    """
    state = resume if resume is not None else _FixpointResumeState()
    root_dists = {_canon(_req_name(dist)) for _imp, dist in roots}
    repaired: set[str] = set()
    attempted: set[tuple[str, str]] = state.attempted
    prev_pkg_ids: set[str] = set(state.resolved_pkg_ids)
    bound: int | None = None
    iteration = 0
    # Declared-rung coverage: module -> declared dists whose RECORD ships it. Built
    # LAZILY (at most once, cached) and ONLY after a round finds missing imports, so
    # a HEALTHY repo makes ZERO ``record_provider`` calls for it — the provider
    # fetches candidate wheels (network), and an eager pre-loop build would hit the
    # network even when nothing is missing, breaking the "purely additive on healthy
    # repos / byte-identical" guarantee.
    _declared_cov: dict[str, list[str]] | None = None

    while True:
        pkg_nodes, pkg_edges = resolve_closure(
            roots,
            host_executor,
            target_env=target_env,
            exclude_newer=exclude_newer,
            extras=needed_extras,
            audit_root_names=frozenset(repaired),
            uv_sources=uv_sources,
            uv_indexes=uv_indexes,
            workspace_members=workspace_members,
            repo_path=repo_path,
        )
        graph = reconcile_packages(graph, pkg_nodes, pkg_edges, prev_pkg_ids)
        graph = _stamp_audit(graph, repaired)
        prev_pkg_ids = {n.id for n in pkg_nodes}
        # Expose the LATEST closure's ids so a subsequent re-entry reconciles
        # against the resolver-managed set only (never the MISSING placeholders).
        state.resolved_pkg_ids = set(prev_pkg_ids)
        graph = install_closure(graph, container_executor)

        # Correction 3: the coverage oracle is RECORD-union over the RESOLVED
        # closure, NOT a post-install packages_distributions() snapshot.
        provided = resolved_record_coverage(pkg_nodes, record_provider)
        missing = _missing_import_nodes(graph, provided=frozenset(provided), deferred=deferred)
        if bound is None:
            bound = min(len(missing), _MAX_REPAIR_ROUNDS)
        if not missing:
            break

        # Demand-gated declared-rung coverage: build on the FIRST round that finds
        # missing imports, then cache (an empty map when nothing was declared).
        if _declared_cov is None:
            _declared_cov = (
                declared_coverage(declared_dists, record_provider) if declared_dists else {}
            )

        new_pair = False
        for imp in missing:
            # Candidate generation, highest trust first: repo-DECLARED dists whose
            # RECORD covers this import (the soft-requirements rung) are PREPENDED
            # ahead of the vendored pipreqs import->dist table (deterministic) and,
            # ONLY on a pipreqs miss, the injected ``llm`` guesser fed this import's
            # used-symbols (``data["symbols"]``). When ``llm`` is None (the default)
            # and nothing was declared, the path stays purely deterministic. Every
            # candidate -- declared included -- is still RECORD-grounded by
            # ``choose_provider`` (this only proposes names, never accepts them), so a
            # lone declared confirm ACCEPTs while a declared + a differing pipreqs
            # confirm stays AMBIGUOUS: a declaration must NOT break a variant tie.
            candidates = declared_candidates(imp.name, _declared_cov) + generate_candidates(
                imp.name, symbols=tuple(imp.data.get("symbols", ())), llm=llm
            )
            decision = choose_provider(imp.name, candidates, record_provider)
            if (
                decision.verdict is Verdict.ACCEPT
                and decision.dist is not None
                and (imp.name, decision.dist) not in attempted
                and _canon(decision.dist) not in root_dists
                # Gate/Also-fix 2: `generate_candidates` proposes the pipreqs-mapped
                # (or LLM-guessed) dist purely from the import name/symbols, with no
                # knowledge of `_declared_package_names_for_repair`'s uv-source
                # exclusion, so an unactivated optional uv-sourced dependency whose
                # import happens to map to its own dist name could still be
                # RECORD-confirmed and re-enter as a repaired root, silently
                # resolving the unrelated public PyPI package in its place. It must
                # therefore be rejected HERE, at acceptance, as well.
                and _canon(decision.dist) not in uv_sourced_names
            ):
                roots = roots + [(None, decision.dist)]
                repaired.add(_canon(decision.dist))
                root_dists.add(_canon(decision.dist))
                new_pair = True
            # Remember every candidate tried for this import (Correction 2b), so a
            # re-proposal of an already-attempted pair cannot re-add / oscillate.
            attempted |= {(imp.name, candidate.dist) for candidate in candidates}
        if not new_pair:
            logger.warning(
                "phase-A stopped: no new repair candidate; residue left unresolved "
                "(fixpoint/oscillation): %s",
                sorted(n.name for n in missing),
            )
            break
        iteration += 1
        if iteration > bound:
            logger.warning(
                "phase-A hit bound=%d; residue left unresolved (honest), not aborting",
                bound,
            )
            break
    return graph
