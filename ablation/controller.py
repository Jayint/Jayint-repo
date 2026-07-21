"""Fresh-replay controller for the ExecuteAgent-only ablation."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from src.envstate.done_gate import pytest_collection_failed

from .evidence import add_runtime_evidence
from .execute_agent import AgentExhausted
from .models import (
    AbstainAction,
    EvidenceBundle,
    FailurePacket,
    FlatPlan,
    RunResult,
    TestResult,
    merge_usage,
)
from .policy import FlatPlanGate, PolicyError
from .script import locate_failed_block, render_plan
from .trace import emit


_ENVIRONMENT_FAILURE_RE = re.compile(
    r"ModuleNotFoundError|No module named|ImportError while loading conftest|"
    r"command not found|cannot open shared object file|"
    r"pkg-config.*(?:not found|missing)|No package ['\"]?.+['\"]? found|"
    r"connection refused|could not connect to server|"
    r"unable to locate package|no matching distribution|"
    r"Cannot find module|package .* is not installed",
    re.IGNORECASE,
)
_ASSERTION_FAILURE_RE = re.compile(
    r"\bAssertionError\b",
    re.IGNORECASE | re.MULTILINE,
)
_ZERO_TESTS_RE = re.compile(
    r"\bno tests (?:ran|collected)\b|\bcollected\s+0\s+items?\b|"
    r"\bRan\s+0\s+tests?\b",
    re.IGNORECASE,
)
_TEST_FAILURE_RE = re.compile(
    r"^\s*(?:FAILED|ERROR)\s+\S+",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class _Evaluation:
    passed: bool
    evidence: EvidenceBundle
    test_result: TestResult | None = None
    failure: FailurePacket | None = None


def _test_failure_route(output: str) -> str:
    # Collection/import-path failures are not pass-rate observations. They must
    # be repaired before the setup is exported to run_rat_benchmark.py.
    if pytest_collection_failed(output or ""):
        return "environment"
    # A real assertion or a hollow zero-test run is not an environment repair
    # target, even when the assertion text happens to mention an import error.
    if _ASSERTION_FAILURE_RE.search(output or "") or _ZERO_TESTS_RE.search(output or ""):
        return "non_environment"
    if _ENVIRONMENT_FAILURE_RE.search(output or ""):
        return "environment"
    if _TEST_FAILURE_RE.search(output or ""):
        return "non_environment"
    return "ambiguous"


def _failure_signature(failure: FailurePacket) -> str:
    payload = "\n".join(
        (
            failure.kind,
            failure.failed_block_id or "",
            failure.command,
            failure.output[-2_000:],
        )
    ).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


class ExecuteOnlyController:
    def __init__(
        self,
        *,
        agent,
        host,
        evidence: EvidenceBundle,
        base_image: str,
        languages: tuple[str, ...],
        test_commands: tuple[str, ...],
        gate: FlatPlanGate | None = None,
        max_cycles: int = 12,
        max_agent_calls: int = 30,
        max_turns_per_decision: int = 50,
        completion_policy: str = "all_tests_pass",
        event_sink=None,
    ) -> None:
        if max_cycles <= 0:
            raise ValueError("max_cycles must be positive")
        if max_agent_calls <= 0:
            raise ValueError("max_agent_calls must be positive")
        if max_turns_per_decision <= 0:
            raise ValueError("max_turns_per_decision must be positive")
        if completion_policy not in {"all_tests_pass", "environment_ready"}:
            raise ValueError(
                "completion_policy must be 'all_tests_pass' or 'environment_ready'"
            )
        self.agent = agent
        self.host = host
        self.evidence = evidence
        self.base_image = base_image
        self.languages = languages
        self.test_commands = test_commands
        self.gate = gate or FlatPlanGate()
        self.max_cycles = max_cycles
        self.max_agent_calls = max_agent_calls
        self.max_turns_per_decision = max_turns_per_decision
        self.completion_policy = completion_policy
        self.event_sink = event_sink

    def _environment_ready_failure(
        self,
        failure: FailurePacket | None,
    ) -> bool:
        """Return whether a failed test is suitable for external ESSR scoring.

        In ESSR mode, a concrete repository/test failure means the environment
        executed the fixed test command far enough for RAT to measure the real
        pass rate.  Environment and ambiguous failures still go through repair.
        """

        return bool(
            self.completion_policy == "environment_ready"
            and failure is not None
            and failure.kind in {"test", "terminal_test"}
            and _test_failure_route(failure.output) == "non_environment"
        )

    def _record_failure(
        self,
        *,
        kind: str,
        cycle: int,
        command: str,
        rc: int,
        output: str,
        failed_block_id: str | None,
        plan: FlatPlan,
        evidence: EvidenceBundle,
        stage: str,
    ) -> tuple[FailurePacket, EvidenceBundle]:
        evidence_id = f"runtime:{stage}:{cycle}:{kind}"
        evidence = add_runtime_evidence(
            evidence,
            evidence_id=evidence_id,
            source=f"{stage} {kind} failure",
            content=(
                f"block={failed_block_id or 'unmapped'}\n"
                f"command={command}\nrc={rc}\n{output}"
            ),
        )
        failure = FailurePacket(
            kind=kind,  # type: ignore[arg-type]
            cycle=cycle,
            command=command,
            rc=rc,
            output=output,
            failed_block_id=failed_block_id,
            plan=plan,
            evidence_id=evidence_id,
        )
        emit(
            self.event_sink,
            "candidate_failure",
            stage=stage,
            cycle=cycle,
            kind=kind,
            failed_block_id=failed_block_id,
            command=command,
            rc=rc,
            evidence_id=evidence_id,
        )
        return failure, evidence

    def _evaluate(
        self,
        plan: FlatPlan,
        evidence: EvidenceBundle,
        *,
        cycle: int,
        terminal: bool,
    ) -> _Evaluation:
        stage = "terminal" if terminal else "search"
        rendered = render_plan(plan)
        emit(
            self.event_sink,
            "fresh_reset",
            stage=stage,
            cycle=cycle,
            plan_digest=plan.digest(),
        )
        self.host.reset_to_base()

        setup = self.host.run_setup(rendered)
        emit(
            self.event_sink,
            "setup_replay",
            stage=stage,
            cycle=cycle,
            rc=setup.rc,
            failing_command=setup.failing_command,
            lineno=setup.lineno,
        )
        if setup.rc != 0:
            block_id = locate_failed_block(
                rendered,
                output=setup.output,
                failing_command=setup.failing_command,
                lineno=setup.lineno,
            )
            kind = "terminal_setup" if terminal else "setup"
            failure, evidence = self._record_failure(
                kind=kind,
                cycle=cycle,
                command=setup.failing_command or "<unmapped setup command>",
                rc=setup.rc,
                output=setup.output,
                failed_block_id=block_id,
                plan=plan,
                evidence=evidence,
                stage=stage,
            )
            return _Evaluation(False, evidence, failure=failure)

        checks = self.host.run_checks(plan)
        emit(
            self.event_sink,
            "host_checks",
            stage=stage,
            cycle=cycle,
            passed=checks.passed,
            block_id=checks.block_id,
            command=checks.command,
            rc=checks.rc,
        )
        if not checks.passed:
            kind = "terminal_check" if terminal else "check"
            failure, evidence = self._record_failure(
                kind=kind,
                cycle=cycle,
                command=checks.command or "<unmapped check>",
                rc=checks.rc,
                output=checks.output,
                failed_block_id=checks.block_id,
                plan=plan,
                evidence=evidence,
                stage=stage,
            )
            return _Evaluation(False, evidence, failure=failure)

        test = self.host.run_tests(self.test_commands)
        emit(
            self.event_sink,
            "test_gate",
            stage=stage,
            cycle=cycle,
            passed=test.passed,
            command=test.command,
            rc=test.rc,
        )
        if not test.passed:
            kind = "terminal_test" if terminal else "test"
            failure, evidence = self._record_failure(
                kind=kind,
                cycle=cycle,
                command=test.command,
                rc=test.rc,
                output=test.output,
                failed_block_id=None,
                plan=plan,
                evidence=evidence,
                stage=stage,
            )
            return _Evaluation(
                False,
                evidence,
                test_result=test,
                failure=failure,
            )
        return _Evaluation(True, evidence, test_result=test)

    def _failed_result(
        self,
        *,
        reason: str,
        plan: FlatPlan,
        cycles: int,
        llm_calls: int,
        usage: dict[str, int],
        test_result: TestResult | None,
        failure: FailurePacket | None,
    ) -> RunResult:
        emit(
            self.event_sink,
            "run_stopped",
            status="failed",
            reason=reason,
            cycles=cycles,
            llm_calls=llm_calls,
        )
        return RunResult(
            status="failed",
            stop_reason=reason,
            plan=plan,
            setup_sh=render_plan(plan).text,
            cycles=cycles,
            llm_calls=llm_calls,
            usage=usage,
            test_result=test_result,
            final_failure=failure,
        )

    def run(self) -> RunResult:
        plan = FlatPlan()
        evidence = self.evidence
        usage: dict[str, int] = {}
        llm_calls = 0
        cycles = 0
        last_failure: FailurePacket | None = None
        last_test: TestResult | None = None
        known_invalid: list[str] = []

        try:
            initial = self.agent.generate_initial(
                evidence,
                self.host.exec_readonly,
                base_image=self.base_image,
                languages=self.languages,
                test_commands=self.test_commands,
                max_turns=min(
                    self.max_turns_per_decision,
                    self.max_agent_calls - llm_calls,
                ),
            )
        except AgentExhausted as exc:
            llm_calls += exc.llm_calls
            usage = merge_usage(usage, exc.usage)
            return self._failed_result(
                reason=f"initial_agent_exhausted: {exc}",
                plan=plan,
                cycles=cycles,
                llm_calls=llm_calls,
                usage=usage,
                test_result=None,
                failure=None,
            )

        plan = initial.plan
        evidence = initial.evidence
        llm_calls += initial.llm_calls
        usage = merge_usage(usage, initial.usage)
        validation = self.gate.validate_plan(plan, evidence.ids)
        if not validation.allowed:
            return self._failed_result(
                reason="initial_plan_rejected: " + "; ".join(validation.errors),
                plan=plan,
                cycles=cycles,
                llm_calls=llm_calls,
                usage=usage,
                test_result=None,
                failure=None,
            )

        seen_plan_digests = {plan.digest()}
        pending_failure: FailurePacket | None = None

        while True:
            if pending_failure is None:
                if cycles >= self.max_cycles:
                    return self._failed_result(
                        reason="max_cycles",
                        plan=plan,
                        cycles=cycles,
                        llm_calls=llm_calls,
                        usage=usage,
                        test_result=last_test,
                        failure=last_failure,
                    )
                cycles += 1
                evaluation = self._evaluate(
                    plan,
                    evidence,
                    cycle=cycles,
                    terminal=False,
                )
                evidence = evaluation.evidence
                last_test = evaluation.test_result or last_test
                if evaluation.passed or self._environment_ready_failure(
                    evaluation.failure
                ):
                    certificate = self._evaluate(
                        plan,
                        evidence,
                        cycle=cycles,
                        terminal=True,
                    )
                    evidence = certificate.evidence
                    last_test = certificate.test_result or last_test
                    if certificate.passed:
                        emit(
                            self.event_sink,
                            "run_stopped",
                            status="success",
                            reason="terminal_fresh_replay_passed",
                            cycles=cycles,
                            llm_calls=llm_calls,
                        )
                        return RunResult(
                            status="success",
                            stop_reason="terminal_fresh_replay_passed",
                            plan=plan,
                            setup_sh=render_plan(plan).text,
                            cycles=cycles,
                            llm_calls=llm_calls,
                            usage=usage,
                            test_result=certificate.test_result,
                            final_failure=None,
                        )
                    if self._environment_ready_failure(certificate.failure):
                        emit(
                            self.event_sink,
                            "run_stopped",
                            status="success",
                            reason="terminal_fresh_replay_environment_ready",
                            cycles=cycles,
                            llm_calls=llm_calls,
                        )
                        return RunResult(
                            status="success",
                            stop_reason="terminal_fresh_replay_environment_ready",
                            plan=plan,
                            setup_sh=render_plan(plan).text,
                            cycles=cycles,
                            llm_calls=llm_calls,
                            usage=usage,
                            test_result=certificate.test_result,
                            final_failure=None,
                        )
                    pending_failure = certificate.failure
                else:
                    pending_failure = evaluation.failure

                assert pending_failure is not None
                last_failure = pending_failure
                signature = _failure_signature(pending_failure)
                if signature not in known_invalid:
                    known_invalid.append(signature)
                    known_invalid[:] = known_invalid[-20:]

            if (
                pending_failure.kind in {"test", "terminal_test"}
                and _test_failure_route(pending_failure.output) == "non_environment"
            ):
                return self._failed_result(
                    reason="host_classified_non_environment_test_failure",
                    plan=plan,
                    cycles=cycles,
                    llm_calls=llm_calls,
                    usage=usage,
                    test_result=last_test,
                    failure=pending_failure,
                )

            # Every accepted repair creates a new candidate which still needs a
            # complete search replay before it can receive a terminal
            # certificate.  Once the last evaluation cycle is consumed, stop
            # on the last executed plan instead of spending another model call
            # and returning an unexecuted candidate as setup.sh.
            if cycles >= self.max_cycles:
                return self._failed_result(
                    reason="max_cycles",
                    plan=plan,
                    cycles=cycles,
                    llm_calls=llm_calls,
                    usage=usage,
                    test_result=last_test,
                    failure=pending_failure,
                )

            remaining_calls = self.max_agent_calls - llm_calls
            if remaining_calls <= 0:
                return self._failed_result(
                    reason="max_agent_calls",
                    plan=plan,
                    cycles=cycles,
                    llm_calls=llm_calls,
                    usage=usage,
                    test_result=last_test,
                    failure=pending_failure,
                )
            packet = replace(
                pending_failure,
                known_invalid=tuple(known_invalid),
            )
            try:
                repair = self.agent.repair(
                    packet,
                    evidence,
                    self.host.exec_readonly,
                    max_turns=min(
                        self.max_turns_per_decision,
                        remaining_calls,
                    ),
                )
            except AgentExhausted as exc:
                llm_calls += exc.llm_calls
                usage = merge_usage(usage, exc.usage)
                if exc.evidence is not None:
                    evidence = exc.evidence
                return self._failed_result(
                    reason=f"repair_agent_exhausted: {exc}",
                    plan=plan,
                    cycles=cycles,
                    llm_calls=llm_calls,
                    usage=usage,
                    test_result=last_test,
                    failure=pending_failure,
                )
            evidence = repair.evidence
            llm_calls += repair.llm_calls
            usage = merge_usage(usage, repair.usage)

            if isinstance(repair.action, AbstainAction):
                return self._failed_result(
                    reason="execute_agent_abstained_non_environment",
                    plan=plan,
                    cycles=cycles,
                    llm_calls=llm_calls,
                    usage=usage,
                    test_result=last_test,
                    failure=pending_failure,
                )

            try:
                candidate = self.gate.apply_patch(
                    plan,
                    repair.action.patch,
                    evidence.ids,
                    failed_block_id=pending_failure.failed_block_id,
                    failure_kind=pending_failure.kind,
                )
            except PolicyError as exc:
                emit(
                    self.event_sink,
                    "patch_rejected",
                    cycle=cycles,
                    errors=list(exc.errors),
                )
                invalid_patch = hashlib.sha256(
                    repr(repair.action.patch).encode("utf-8")
                ).hexdigest()[:16]
                if invalid_patch not in known_invalid:
                    known_invalid.append(invalid_patch)
                    known_invalid[:] = known_invalid[-20:]
                pending_failure = replace(
                    pending_failure,
                    rejection_errors=exc.errors,
                )
                continue

            digest = candidate.digest()
            if digest in seen_plan_digests:
                return self._failed_result(
                    reason="repeated_plan",
                    plan=plan,
                    cycles=cycles,
                    llm_calls=llm_calls,
                    usage=usage,
                    test_result=last_test,
                    failure=pending_failure,
                )
            emit(
                self.event_sink,
                "patch_accepted",
                cycle=cycles,
                op=repair.action.patch.op,
                target_block_id=repair.action.patch.target_block_id,
                old_digest=plan.digest(),
                new_digest=digest,
            )
            plan = candidate
            seen_plan_digests.add(digest)
            pending_failure = None
