"""Graph-linked incremental execution with semantic checkpoint invalidation.

The v3 graph remains the source of truth and ``setup.sh`` remains the final
replay artifact.  During search, however, replaying the entire artifact after
every accepted patch repeats already-certified work.  ``IncrementalPlanExecutor``
executes structured graph blocks, checkpoints verified prefixes, and restores
the longest prefix whose block signatures still match after a graph patch.

The executor never certifies from command success.  All state changes still go
through ``certify_refresh`` and therefore through host-run check commands.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable

from python_deps.depgraph.emit import _is_reciped
from python_deps.depgraph.execution_plan import (
    block_signature,
    compile_execution_plan,
    execution_plan_hash,
)
from python_deps.depgraph.schema import EdgeType, NodeType, State
from src.envstate.depgraph_live import certify_refresh, certify_targets
from src.envstate.done_gate import verified_test_command_passed
from src.sandbox import InstallResult


@dataclass(frozen=True)
class PlanCheckpoint:
    name: str
    prefix_len: int
    prefix_hash: str
    wave: str


@dataclass(frozen=True)
class IncrementalExecutionResult:
    graph: object
    install_result: InstallResult
    failed_block_id: str | None
    failed_node_id: str | None
    plan_hash: str
    total_blocks: int
    reused_blocks: int
    executed_block_ids: tuple[str, ...]
    restored_checkpoint: str | None
    created_checkpoints: tuple[str, ...]


@dataclass(frozen=True)
class CandidateCheckResult:
    node_id: str | None
    command: str
    rc: int
    output: str


@dataclass(frozen=True)
class CandidateValidationResult:
    transaction_id: str
    committed: bool
    graph: object
    install_result: InstallResult
    base_checkpoint: str
    base_prefix_len: int
    validation_prefix_len: int
    executed_block_ids: tuple[str, ...]
    checks: tuple[CandidateCheckResult, ...]
    failed_block_id: str | None
    failed_node_id: str | None
    created_checkpoint: str | None = None


def _prefix_hash(signatures: tuple[str, ...], prefix_len: int) -> str:
    blob = "\n".join(signatures[:prefix_len])
    return hashlib.sha256(blob.encode()).hexdigest()


def _common_prefix(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def _block_script(block) -> str:
    lines = [
        "set -Eeuo pipefail",
        f"#@block {block.block_id}  targets={','.join(block.target_node_ids) or '-'}",
    ]
    lines.extend(block.commands)
    return "\n".join(lines) + "\n"


class IncrementalPlanExecutor:
    """Stateful executor for one repository/container run.

    Checkpoint callbacks are deliberately dependency-injected.  The production
    path supplies ``Sandbox`` named-checkpoint methods; tests use small in-memory
    fakes.  If checkpoint creation is unavailable, correctness is preserved by
    restoring the base and replaying a longer prefix.
    """

    def __init__(
        self,
        *,
        run_install_script: Callable[[str], InstallResult],
        exec_readonly: Callable[[str], tuple[int, str]],
        restore_base: Callable[[], None],
        create_checkpoint: Callable[[str], object] | None = None,
        restore_checkpoint: Callable[[str], None] | None = None,
        drop_checkpoint: Callable[[str], None] | None = None,
        create_candidate: Callable[[str, str | None], object] | None = None,
        candidate_run_install_script: Callable[[object, str], InstallResult] | None = None,
        candidate_exec_readonly: Callable[[object, str], tuple[int, str]] | None = None,
        promote_candidate: Callable[[object], None] | None = None,
        abort_candidate: Callable[[object], None] | None = None,
        checkpoint_interval: int = 8,
        expensive_block_seconds: float = 30.0,
    ) -> None:
        self._run_install_script = run_install_script
        self._exec_readonly = exec_readonly
        self._restore_base = restore_base
        self._create_checkpoint_cb = create_checkpoint
        self._restore_checkpoint_cb = restore_checkpoint
        self._drop_checkpoint_cb = drop_checkpoint
        self._create_candidate_cb = create_candidate
        self._candidate_run_install_script_cb = candidate_run_install_script
        self._candidate_exec_readonly_cb = candidate_exec_readonly
        self._promote_candidate_cb = promote_candidate
        self._abort_candidate_cb = abort_candidate
        self._checkpoint_interval = max(int(checkpoint_interval), 1)
        self._expensive_block_seconds = max(float(expensive_block_seconds), 0.0)

        self._plan_signatures: tuple[str, ...] = ()
        self._executed_prefix = 0
        self._dirty = False
        self._checkpoints: list[PlanCheckpoint] = []

    @property
    def checkpoints(self) -> tuple[PlanCheckpoint, ...]:
        return tuple(self._checkpoints)

    def _drop(self, checkpoint: PlanCheckpoint) -> None:
        if self._drop_checkpoint_cb is not None:
            try:
                self._drop_checkpoint_cb(checkpoint.name)
            except Exception:
                pass

    def _retain_valid_checkpoints(
        self,
        signatures: tuple[str, ...],
        valid_prefix: int,
    ) -> None:
        kept: list[PlanCheckpoint] = []
        stale: list[PlanCheckpoint] = []
        for checkpoint in self._checkpoints:
            valid = (
                checkpoint.prefix_len <= valid_prefix
                and checkpoint.prefix_hash
                == _prefix_hash(signatures, checkpoint.prefix_len)
            )
            if valid:
                kept.append(checkpoint)
            else:
                stale.append(checkpoint)
        # Checkpoint images may form a parent/child chain. Remove the longest
        # suffix first so Docker can release every ancestor afterward.
        for checkpoint in sorted(stale, key=lambda item: item.prefix_len, reverse=True):
            self._drop(checkpoint)
        self._checkpoints = kept

    def _restore(self, signatures: tuple[str, ...], valid_prefix: int) -> tuple[int, str | None]:
        self._retain_valid_checkpoints(signatures, valid_prefix)
        checkpoint = max(
            self._checkpoints,
            key=lambda item: item.prefix_len,
            default=None,
        )
        if checkpoint is not None and self._restore_checkpoint_cb is not None:
            self._restore_checkpoint_cb(checkpoint.name)
            self._executed_prefix = checkpoint.prefix_len
            self._dirty = False
            return checkpoint.prefix_len, checkpoint.name

        self._restore_base()
        self._executed_prefix = 0
        self._dirty = False
        return 0, None

    def _checkpoint(
        self,
        signatures: tuple[str, ...],
        *,
        prefix_len: int,
        wave: str,
    ) -> str | None:
        if self._create_checkpoint_cb is None:
            return None
        prefix_hash = _prefix_hash(signatures, prefix_len)
        existing = next(
            (
                checkpoint
                for checkpoint in self._checkpoints
                if checkpoint.prefix_len == prefix_len
                and checkpoint.prefix_hash == prefix_hash
            ),
            None,
        )
        if existing is not None:
            return existing.name

        stale = [c for c in self._checkpoints if c.prefix_len == prefix_len]
        for checkpoint in stale:
            self._drop(checkpoint)
            self._checkpoints.remove(checkpoint)

        name = f"exec-{prefix_len}-{prefix_hash[:12]}"
        try:
            self._create_checkpoint_cb(name)
        except Exception:
            return None
        self._checkpoints.append(
            PlanCheckpoint(name=name, prefix_len=prefix_len, prefix_hash=prefix_hash, wave=wave)
        )
        return name

    def _should_checkpoint(self, blocks, index: int, elapsed: float) -> bool:
        prefix_len = index + 1
        if prefix_len == len(blocks):
            return True
        if blocks[index + 1].wave != blocks[index].wave:
            return True
        if prefix_len % self._checkpoint_interval == 0:
            return True
        return elapsed >= self._expensive_block_seconds

    def _check_block(self, graph, block, exec_readonly=None) -> InstallResult | None:
        exec_readonly = exec_readonly or self._exec_readonly
        target_checks = {
            node.check_command
            for node_id in block.target_node_ids
            if (node := graph.get(node_id)) is not None and node.check_command
        }
        for command in block.check_commands:
            # Graph-node checks have just run through certify_targets, which is
            # the only component allowed to mutate node truth.  Only execute
            # additional block-level checks here.
            if command in target_checks:
                continue
            rc, output = exec_readonly(command)
            if rc != 0:
                detail = output or (
                    f"host check failed for block {block.block_id}: {command} (rc={rc})"
                )
                return InstallResult(
                    rc=rc if rc != 0 else 1,
                    failing_command=command,
                    lineno=None,
                    stderr=detail,
                )
        return None

    @staticmethod
    def _failed_target(graph, block) -> str | None:
        for node_id in block.target_node_ids:
            node = graph.get(node_id) if graph is not None else None
            if (
                node is not None
                and node.check_command
                and node.state is not State.SATISFIED
            ):
                return node_id
        return None

    @staticmethod
    def _block_for_node(blocks, node_id: str) -> str | None:
        return next(
            (block.block_id for block in blocks if node_id in block.target_node_ids),
            None,
        )

    @staticmethod
    def _unsatisfied_prefix_target(graph, blocks, prefix_len: int) -> str | None:
        target_ids = {
            node_id
            for block in blocks[:prefix_len]
            for node_id in block.target_node_ids
        }
        return next(
            (
                node_id
                for node_id in target_ids
                if (node := graph.get(node_id)) is not None
                and node.check_command
                and node.state is not State.SATISFIED
            ),
            None,
        )

    def _candidate_checkpoint(
        self,
        signatures: tuple[str, ...],
        valid_prefix: int,
    ) -> tuple[str | None, int]:
        checkpoint = max(
            (
                item for item in self._checkpoints
                if item.prefix_len <= valid_prefix
                and item.prefix_hash == _prefix_hash(signatures, item.prefix_len)
            ),
            key=lambda item: item.prefix_len,
            default=None,
        )
        if checkpoint is None:
            return None, 0
        return checkpoint.name, checkpoint.prefix_len

    @staticmethod
    def _affected_satisfied_ids(
        official_graph,
        candidate_graph,
        blocks,
        *,
        validation_prefix_len: int,
        changed_target_ids: set[str],
    ) -> tuple[str, ...]:
        affected = set(changed_target_ids)
        changed = True
        while changed:
            changed = False
            for edge in candidate_graph.edges:
                if (
                    edge.relation is EdgeType.REQUIRES
                    and edge.dst in affected
                    and edge.src not in affected
                ):
                    affected.add(edge.src)
                    changed = True

        prefix_targets = {
            node_id
            for block in blocks[:validation_prefix_len]
            for node_id in block.target_node_ids
        }
        # A candidate fork may come from an older semantic checkpoint.  Nodes
        # owned by later blocks are therefore not materialized in that
        # candidate yet, even when the official graph remembers them as
        # SATISFIED.  Rechecking those nodes here turns an unexecuted suffix
        # into a false regression (the normal executor will replay that suffix
        # after promotion).
        future_targets = {
            node_id
            for block in blocks[validation_prefix_len:]
            for node_id in block.target_node_ids
        }
        # Goal nodes such as ``import:fakeredis`` have no block themselves, but
        # they are just as unavailable when their provider package lives in the
        # unreplayed suffix.  Carry the future set backwards over REQUIRES so
        # those indirect obligations are deferred together with their provider.
        unmaterialized = set(future_targets)
        changed = True
        while changed:
            changed = False
            for edge in candidate_graph.edges:
                if (
                    edge.relation is EdgeType.REQUIRES
                    and edge.dst in unmaterialized
                    and edge.src not in unmaterialized
                ):
                    unmaterialized.add(edge.src)
                    changed = True
        out: list[str] = []
        for node in official_graph.nodes:
            candidate = candidate_graph.get(node.id)
            if (
                node.state is State.SATISFIED
                and candidate is not None
                and candidate.check_command
                and (node.id in affected or node.id in prefix_targets)
                and node.id not in unmaterialized
            ):
                out.append(node.id)
        for node_id in changed_target_ids:
            candidate = candidate_graph.get(node_id)
            if candidate is not None and candidate.check_command and node_id not in out:
                out.append(node_id)
        return tuple(out)

    def validate_candidate(
        self,
        official_graph,
        official_manual_blocks,
        candidate_graph,
        candidate_manual_blocks,
        *,
        failed_block_id: str,
        target_node_id: str | None,
        cycle: int,
        transaction_id: str,
    ) -> CandidateValidationResult:
        """Validate and commit one graph patch in an isolated checkpoint fork.

        No executor state or working-container state changes on an aborted result.
        A successful result promotes the candidate container first, then advances
        the official semantic prefix so the normal executor continues after the
        already-validated repair without replaying it.
        """
        callbacks = (
            self._create_candidate_cb,
            self._candidate_run_install_script_cb,
            self._candidate_exec_readonly_cb,
            self._promote_candidate_cb,
            self._abort_candidate_cb,
        )
        if any(callback is None for callback in callbacks):
            result = InstallResult(
                rc=1,
                failing_command=None,
                lineno=None,
                stderr="candidate transaction callbacks are not configured",
            )
            return CandidateValidationResult(
                transaction_id, False, candidate_graph, result, "base", 0, 0,
                (), (), failed_block_id, target_node_id,
            )

        official_blocks = compile_execution_plan(
            official_graph, tuple(official_manual_blocks or ())
        )
        candidate_blocks = compile_execution_plan(
            candidate_graph, tuple(candidate_manual_blocks or ())
        )
        official_signatures = tuple(block_signature(block) for block in official_blocks)
        candidate_signatures = tuple(block_signature(block) for block in candidate_blocks)
        baseline_signatures = self._plan_signatures or official_signatures
        common = _common_prefix(baseline_signatures, candidate_signatures)
        valid_prefix = min(common, self._executed_prefix)
        checkpoint_name, base_prefix_len = self._candidate_checkpoint(
            candidate_signatures, valid_prefix
        )
        base_checkpoint = checkpoint_name or "base"

        # Goal obligations (most notably Import nodes) often have no execution
        # block of their own.  Validate them only after every materialized
        # provider in their hard REQUIRES closure has been replayed in the
        # candidate.  Otherwise a fork from an old checkpoint can report the
        # goal itself missing merely because its package block is still in the
        # unexecuted suffix.
        validation_target_ids: set[str] = set()
        if target_node_id is not None and candidate_graph.get(target_node_id) is not None:
            validation_target_ids.add(target_node_id)
            changed = True
            while changed:
                changed = False
                for edge in candidate_graph.edges:
                    if (
                        edge.relation is EdgeType.REQUIRES
                        and edge.src in validation_target_ids
                        and edge.dst not in validation_target_ids
                    ):
                        validation_target_ids.add(edge.dst)
                        changed = True

        target_indices = [
            index for index, block in enumerate(candidate_blocks)
            if block.block_id == failed_block_id
            or any(
                node_id in validation_target_ids
                for node_id in block.target_node_ids
            )
        ]
        if target_indices:
            validation_prefix_len = max(target_indices) + 1
        elif common < len(candidate_blocks):
            validation_prefix_len = common + 1
        else:
            result = InstallResult(
                rc=1,
                failing_command=None,
                lineno=None,
                stderr=(
                    "candidate patch produced no executable block for "
                    f"{target_node_id or failed_block_id}"
                ),
            )
            return CandidateValidationResult(
                transaction_id, False, candidate_graph, result,
                base_checkpoint, base_prefix_len, base_prefix_len,
                (), (), failed_block_id, target_node_id,
            )
        validation_prefix_len = max(validation_prefix_len, base_prefix_len)

        handle = None
        executed: list[str] = []
        checks: list[CandidateCheckResult] = []

        def candidate_exec(command: str) -> tuple[int, str]:
            rc, output = self._candidate_exec_readonly_cb(handle, command)
            node_id = next(
                (
                    node.id for node in candidate_graph.nodes
                    if node.check_command == command
                ),
                None,
            )
            checks.append(CandidateCheckResult(node_id, command, rc, output or ""))
            return rc, output

        def abort(
            result: InstallResult,
            failed_block: str | None,
            failed_node: str | None,
        ) -> CandidateValidationResult:
            if handle is not None:
                try:
                    self._abort_candidate_cb(handle)
                except Exception:
                    pass
            return CandidateValidationResult(
                transaction_id=transaction_id,
                committed=False,
                graph=candidate_graph,
                install_result=result,
                base_checkpoint=base_checkpoint,
                base_prefix_len=base_prefix_len,
                validation_prefix_len=validation_prefix_len,
                executed_block_ids=tuple(executed),
                checks=tuple(checks),
                failed_block_id=failed_block,
                failed_node_id=failed_node,
            )

        try:
            handle = self._create_candidate_cb(transaction_id, checkpoint_name)
        except Exception as exc:
            return abort(
                InstallResult(1, None, None, f"candidate container creation failed: {exc}"),
                failed_block_id,
                target_node_id,
            )

        for index in range(base_prefix_len, validation_prefix_len):
            block = candidate_blocks[index]
            try:
                result = self._candidate_run_install_script_cb(handle, _block_script(block))
            except Exception as exc:
                return abort(
                    InstallResult(1, None, None, f"candidate block execution failed: {exc}"),
                    block.block_id,
                    block.target_node_ids[0] if block.target_node_ids else target_node_id,
                )
            if result.rc != 0:
                return abort(
                    result,
                    block.block_id,
                    block.target_node_ids[0] if block.target_node_ids else target_node_id,
                )
            executed.append(block.block_id)
            try:
                candidate_graph = certify_targets(
                    candidate_graph, candidate_exec, cycle, block.target_node_ids,
                    certify_tests=False,
                )
                check_failure = self._check_block(
                    candidate_graph, block, exec_readonly=candidate_exec
                )
            except Exception as exc:
                return abort(
                    InstallResult(1, None, None, f"candidate check execution failed: {exc}"),
                    block.block_id,
                    block.target_node_ids[0] if block.target_node_ids else target_node_id,
                )
            failed_node = self._failed_target(candidate_graph, block)
            if check_failure is not None or failed_node is not None:
                result = check_failure or InstallResult(
                    1,
                    candidate_graph.get(failed_node).check_command if failed_node else None,
                    None,
                    f"candidate host certification failed for {failed_node or block.block_id}",
                )
                return abort(result, block.block_id, failed_node or target_node_id)

        changed_target_ids = {
            node_id
            for block in candidate_blocks[base_prefix_len:validation_prefix_len]
            for node_id in block.target_node_ids
        }
        # Import/config goals often have no executable block of their own.  A
        # provider repair must still prove the original failing obligation in
        # the candidate container; otherwise a provider can be promoted after
        # checking only itself while the goal remains broken.
        if target_node_id is not None and candidate_graph.get(target_node_id) is not None:
            changed_target_ids.add(target_node_id)
        affected_ids = self._affected_satisfied_ids(
            official_graph,
            candidate_graph,
            candidate_blocks,
            validation_prefix_len=validation_prefix_len,
            changed_target_ids=changed_target_ids,
        )
        try:
            candidate_graph = certify_targets(
                candidate_graph, candidate_exec, cycle, affected_ids,
                certify_tests=False,
            )
        except Exception as exc:
            return abort(
                InstallResult(1, None, None, f"candidate affected-node check failed: {exc}"),
                failed_block_id,
                target_node_id,
            )
        revoked = next(
            (
                node_id for node_id in affected_ids
                if (node := candidate_graph.get(node_id)) is not None
                and node.check_command
                and node.state is not State.SATISFIED
            ),
            None,
        )
        if revoked is not None:
            node = candidate_graph.get(revoked)
            detail = next(
                (
                    check.output for check in reversed(checks)
                    if check.node_id == revoked and check.rc != 0
                ),
                f"candidate affected-node check failed for {revoked}",
            )
            return abort(
                InstallResult(1, node.check_command if node else None, None, detail),
                self._block_for_node(candidate_blocks, revoked) or failed_block_id,
                revoked,
            )

        try:
            self._promote_candidate_cb(handle)
        except Exception as exc:
            return abort(
                InstallResult(1, None, None, f"candidate promotion failed: {exc}"),
                failed_block_id,
                target_node_id,
            )

        self._retain_valid_checkpoints(candidate_signatures, valid_prefix)
        self._plan_signatures = candidate_signatures
        self._executed_prefix = validation_prefix_len
        self._dirty = False
        created_checkpoint = None
        if validation_prefix_len:
            created_checkpoint = self._checkpoint(
                candidate_signatures,
                prefix_len=validation_prefix_len,
                wave=candidate_blocks[validation_prefix_len - 1].wave,
            )
        return CandidateValidationResult(
            transaction_id=transaction_id,
            committed=True,
            graph=candidate_graph,
            install_result=InstallResult(0, None, None, ""),
            base_checkpoint=base_checkpoint,
            base_prefix_len=base_prefix_len,
            validation_prefix_len=validation_prefix_len,
            executed_block_ids=tuple(executed),
            checks=tuple(checks),
            failed_block_id=None,
            failed_node_id=None,
            created_checkpoint=created_checkpoint,
        )

    def execute(self, graph, manual_blocks, cycle: int) -> IncrementalExecutionResult:
        blocks = compile_execution_plan(graph, tuple(manual_blocks or ()))
        signatures = tuple(block_signature(block) for block in blocks)
        plan_hash = execution_plan_hash(blocks)

        common = _common_prefix(self._plan_signatures, signatures)
        valid_prefix = min(common, self._executed_prefix)
        plan_changed_before_live_prefix = valid_prefix < self._executed_prefix
        appended_only = (
            not self._dirty
            and self._executed_prefix == len(self._plan_signatures)
            and common == len(self._plan_signatures)
        )

        restored_checkpoint: str | None = None
        if self._dirty or plan_changed_before_live_prefix:
            reused, restored_checkpoint = self._restore(signatures, valid_prefix)
            graph = certify_refresh(
                graph, self._exec_readonly, cycle, certify_tests=False
            )
        elif self._plan_signatures and signatures != self._plan_signatures and not appended_only:
            reused, restored_checkpoint = self._restore(signatures, valid_prefix)
            graph = certify_refresh(
                graph, self._exec_readonly, cycle, certify_tests=False
            )
        else:
            reused = self._executed_prefix
            self._retain_valid_checkpoints(signatures, min(common, self._executed_prefix))

        self._plan_signatures = signatures
        executed: list[str] = []
        created: list[str] = []

        for index in range(self._executed_prefix, len(blocks)):
            block = blocks[index]
            started = time.monotonic()
            result = self._run_install_script(_block_script(block))
            elapsed = time.monotonic() - started
            if result.rc != 0:
                if result.failing_command and verified_test_command_passed(
                    result.failing_command,
                    result.rc,
                    result.stderr,
                ):
                    for node_id in block.target_node_ids:
                        node = graph.get(node_id) if graph is not None else None
                        if node is not None and node.type is NodeType.TEST:
                            graph = graph.with_node(
                                node.with_state(
                                    State.SATISFIED,
                                    evidence=result.stderr[-1000:],
                                    cycle=cycle,
                                )
                            )
                    executed.append(block.block_id)
                    self._executed_prefix = index + 1
                    self._dirty = False
                    if self._should_checkpoint(blocks, index, elapsed):
                        checkpoint_name = self._checkpoint(
                            signatures, prefix_len=index + 1, wave=block.wave
                        )
                        if checkpoint_name is not None:
                            created.append(checkpoint_name)
                    continue
                self._executed_prefix = index
                self._dirty = True
                return IncrementalExecutionResult(
                    graph=graph,
                    install_result=result,
                    failed_block_id=block.block_id,
                    failed_node_id=block.target_node_ids[0] if block.target_node_ids else None,
                    plan_hash=plan_hash,
                    total_blocks=len(blocks),
                    reused_blocks=reused,
                    executed_block_ids=tuple(executed),
                    restored_checkpoint=restored_checkpoint,
                    created_checkpoints=tuple(created),
                )

            graph = certify_targets(
                graph, self._exec_readonly, cycle, block.target_node_ids,
                certify_tests=False,
            )
            check_failure = self._check_block(graph, block)
            failed_node = self._failed_target(graph, block)
            if check_failure is not None or failed_node is not None:
                failure = check_failure or InstallResult(
                    rc=1,
                    failing_command=(graph.get(failed_node).check_command if failed_node else None),
                    lineno=None,
                    stderr=f"host certification failed for {failed_node or block.block_id}",
                )
                self._executed_prefix = index
                self._dirty = True
                return IncrementalExecutionResult(
                    graph=graph,
                    install_result=failure,
                    failed_block_id=block.block_id,
                    failed_node_id=failed_node or (
                        block.target_node_ids[0] if block.target_node_ids else None
                    ),
                    plan_hash=plan_hash,
                    total_blocks=len(blocks),
                    reused_blocks=reused,
                    executed_block_ids=tuple(executed),
                    restored_checkpoint=restored_checkpoint,
                    created_checkpoints=tuple(created),
                )

            executed.append(block.block_id)
            self._executed_prefix = index + 1
            self._dirty = False
            if self._should_checkpoint(blocks, index, elapsed):
                # A checkpoint is reusable only after the host has revalidated
                # every target in its prefix. This catches revocation caused by
                # a later package mutation without paying an all-graph scan per
                # block.
                graph = certify_refresh(
                    graph, self._exec_readonly, cycle, certify_tests=False
                )
                revoked = self._unsatisfied_prefix_target(
                    graph, blocks, self._executed_prefix
                )
                if revoked is not None:
                    self._dirty = True
                    node = graph.get(revoked)
                    return IncrementalExecutionResult(
                        graph=graph,
                        install_result=InstallResult(
                            rc=1,
                            failing_command=node.check_command if node is not None else None,
                            lineno=None,
                            stderr=f"checkpoint certification revoked {revoked}",
                        ),
                        failed_block_id=self._block_for_node(blocks, revoked) or revoked,
                        failed_node_id=revoked,
                        plan_hash=plan_hash,
                        total_blocks=len(blocks),
                        reused_blocks=reused,
                        executed_block_ids=tuple(executed),
                        restored_checkpoint=restored_checkpoint,
                        created_checkpoints=tuple(created),
                    )
                checkpoint_name = self._checkpoint(
                    signatures, prefix_len=index + 1, wave=block.wave
                )
                if checkpoint_name is not None:
                    created.append(checkpoint_name)

        graph = certify_refresh(
            graph, self._exec_readonly, cycle, certify_tests=False
        )
        governed_target_ids = {
            node_id for block in blocks for node_id in block.target_node_ids
        }
        unsatisfied = next(
            (
                node.id
                for node in graph.nodes
                if (_is_reciped(node) or node.id in governed_target_ids)
                and node.check_command
                and node.state is not State.SATISFIED
            ),
            None,
        )
        if unsatisfied is not None:
            block_id = self._block_for_node(blocks, unsatisfied) or unsatisfied
            node = graph.get(unsatisfied)
            self._dirty = True
            result = InstallResult(
                rc=1,
                failing_command=node.check_command if node is not None else None,
                lineno=None,
                stderr=f"host certification failed for {unsatisfied}",
            )
            return IncrementalExecutionResult(
                graph=graph,
                install_result=result,
                failed_block_id=block_id,
                failed_node_id=unsatisfied,
                plan_hash=plan_hash,
                total_blocks=len(blocks),
                reused_blocks=reused,
                executed_block_ids=tuple(executed),
                restored_checkpoint=restored_checkpoint,
                created_checkpoints=tuple(created),
            )

        return IncrementalExecutionResult(
            graph=graph,
            install_result=InstallResult(rc=0, failing_command=None, lineno=None, stderr=""),
            failed_block_id=None,
            failed_node_id=None,
            plan_hash=plan_hash,
            total_blocks=len(blocks),
            reused_blocks=reused,
            executed_block_ids=tuple(executed),
            restored_checkpoint=restored_checkpoint,
            created_checkpoints=tuple(created),
        )
