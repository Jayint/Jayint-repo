# src/envstate/repair_loop.py
"""Bounded typed-patch repair loop (2b §6.3). Dependency-injected (propose + emit passed in)
so it is unit-testable with no Docker/LLM and touches no globals. The only state writers
remain certify_refresh (inside `emit`) and the graph reducer (apply_proposal inside admit)."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from python_deps.depgraph.patch_gate import admit_proposal, compose_script
from src.envstate.repair_scope import build_repair_scope


@dataclass(frozen=True)
class RepairOutcome:
    graph: object
    still_failing_id: str | None
    manual_blocks: tuple
    known_invalid: frozenset
    turns_spent: int
    budget_exhausted: bool


@dataclass(frozen=True)
class CandidateTransactionOutcome:
    committed: bool
    graph: object
    manual_blocks: tuple
    bundle: object | None
    failed_id: str | None
    transaction_id: str


def run_structured_repair(
    graph, failed_id, bundle, cycle, *,
    propose, emit,
    manual_blocks=(), known_invalid=frozenset(),
    max_repairs=5, repair_budget=10 ** 9,
    constraints=None, target_hint=None,
    scope_builder=build_repair_scope,
    plan_builder=None,
    cap_failed_id: bool = False,
    validate_candidate=None,
    review_abstain=None,
    transaction_id_factory=None,
):
    ki = set(known_invalid)
    mb = tuple(manual_blocks)
    turns = 0
    last_failed_cmd = None
    cons = dict(constraints or {})

    def _review_abstain(action, scope):
        """Keep every abstention behind the same host-review boundary.

        A proposal may abstain either on the first response or on the single
        PatchGate re-prompt.  In both cases the action is advisory only: the
        host decides whether the failure is genuinely non-environmental.
        """
        if review_abstain is None:
            return False, "Host diagnosis did not confirm non-environment"
        return review_abstain(action, scope)

    def _stop_after_abstain(action, scope, *, turns_spent):
        accepted, reason = _review_abstain(action, scope)
        if not accepted:
            ki.add(scope.failed_command or failed_id)
        return accepted, reason, RepairOutcome(
            graph, failed_id, mb, frozenset(ki), turns_spent, False
        )

    for _attempt in range(max_repairs):
        if turns >= repair_budget:
            return RepairOutcome(graph, failed_id, mb, frozenset(ki), turns, True)
        blocks = (plan_builder or compose_script)(graph, mb)
        failed_block = next((b for b in blocks if b.block_id == failed_id), None)
        if failed_block is None and target_hint:
            failed_block = next(
                (b for b in blocks if target_hint in b.target_node_ids), None
            )
        target = (failed_block.target_node_ids[0]
                  if (failed_block and failed_block.target_node_ids) else target_hint)
        scope = scope_builder(graph, target_node_id=target, failed_block=failed_block,
                              bundle=bundle, known_invalid=tuple(sorted(ki)), constraints=cons)
        # Convergence guard: never re-attempt the identical failing command.
        if scope.failed_command and scope.failed_command == last_failed_cmd:
            ki.add(scope.failed_command)
            return RepairOutcome(graph, failed_id, mb, frozenset(ki), turns, False)
        proposal = propose(scope)
        turns += 1
        from src.envstate.agent_action import AbstainAction
        if isinstance(proposal, AbstainAction):
            accepted, reason = _review_abstain(proposal, scope)
            if accepted:
                return RepairOutcome(graph, failed_id, mb, frozenset(ki), turns, False)
            proposal = propose(scope, rejection_errors=(reason,))
            turns += 1
            if isinstance(proposal, AbstainAction):
                _accepted, _reason, outcome = _stop_after_abstain(
                    proposal, scope, turns_spent=turns
                )
                return outcome
        res = (admit_proposal(graph, proposal, manual_blocks=mb,
                              known_evidence_ids=scope.known_evidence_ids)
               if proposal is not None else None)
        if res is not None and not res.accepted:                 # §8: re-prompt ONCE with errors
            proposal = propose(scope, rejection_errors=res.errors)
            turns += 1
            if isinstance(proposal, AbstainAction):
                _accepted, _reason, outcome = _stop_after_abstain(
                    proposal, scope, turns_spent=turns
                )
                return outcome
            res = (admit_proposal(graph, proposal, manual_blocks=mb,
                                  known_evidence_ids=scope.known_evidence_ids)
                   if proposal is not None else None)
        if res is None or not res.accepted:
            ki.add(scope.failed_command or failed_id)
            return RepairOutcome(graph, failed_id, mb, frozenset(ki), turns, False)
        if validate_candidate is not None:
            candidate_graph = getattr(res, "candidate_graph", res.graph)
            candidate_blocks = getattr(
                res, "candidate_manual_blocks", res.manual_blocks
            )
            transaction_id = (
                transaction_id_factory(cycle, _attempt)
                if transaction_id_factory is not None
                else f"txn-{cycle}-{_attempt + 1}-{uuid4().hex[:10]}"
            )
            candidate = validate_candidate(
                graph,
                mb,
                candidate_graph,
                candidate_blocks,
                failed_id=failed_id,
                target_hint=target,
                proposal=proposal,
                cycle=cycle,
                transaction_id=transaction_id,
            )
            if not candidate.committed:
                bundle = candidate.bundle or bundle
                failed_id = candidate.failed_id or failed_id
                candidate_items = (
                    candidate.bundle.items
                    if candidate.bundle is not None
                    else ()
                )
                failed_command = next(
                    (item.command for item in candidate_items if item.rc != 0),
                    None,
                )
                if failed_command:
                    ki.add(failed_command)
                continue
            graph, mb = candidate.graph, candidate.manual_blocks
        else:
            graph, mb = res.graph, res.manual_blocks
        last_failed_cmd = scope.failed_command
        graph, bundle, new_failed_id = emit(graph, mb)
        if new_failed_id is None:
            return RepairOutcome(graph, None, mb, frozenset(ki), turns, False)
        if cap_failed_id and new_failed_id != failed_id:
            # Stage 2: original node fixed but a different node now fails; return the
            # original id and let the outer loop re-verify from a fresh base (no silent pivot).
            return RepairOutcome(graph, failed_id, mb, frozenset(ki), turns, False)
        failed_id = new_failed_id
        ki.add(scope.failed_command or "")
    return RepairOutcome(graph, failed_id, mb, frozenset(ki), turns, False)
