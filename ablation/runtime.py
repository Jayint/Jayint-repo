"""Adapters from the flat ablation controller to the project's generic Sandbox."""
from __future__ import annotations

from src.envstate.done_gate import verified_test_command_passed

from .integrity import verify_source_manifest
from .models import CheckResult, FlatPlan, SetupResult, TestResult
from .script import RenderedScript


class SandboxHost:
    """Host-authoritative execution surface used by the ablation controller."""

    def __init__(
        self,
        sandbox,
        *,
        source_manifest: dict[str, str] | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.source_manifest = (
            None if source_manifest is None else dict(source_manifest)
        )

    def reset_to_base(self) -> None:
        self.sandbox.reset_to_base()

    def exec_readonly(self, command: str) -> tuple[int, str]:
        return self.sandbox.exec_readonly(command)

    def run_setup(self, rendered: RenderedScript) -> SetupResult:
        result = self.sandbox.run_install_script(rendered.text)
        return SetupResult(
            rc=int(result.rc),
            output=result.stderr or "",
            failing_command=result.failing_command,
            lineno=result.lineno,
        )

    def run_checks(self, plan: FlatPlan) -> CheckResult:
        if self.source_manifest is not None:
            passed, output = verify_source_manifest(
                self.sandbox,
                self.source_manifest,
            )
            if not passed:
                return CheckResult(
                    passed=False,
                    block_id=None,
                    command="host:source_integrity",
                    rc=1,
                    output=output,
                )
        for block in plan.blocks:
            for command in block.checks:
                rc, output = self.sandbox.exec_readonly(command)
                if rc != 0:
                    return CheckResult(
                        passed=False,
                        block_id=block.block_id,
                        command=command,
                        rc=int(rc),
                        output=output or "",
                    )
        return CheckResult(passed=True)

    def run_tests(self, test_commands: tuple[str, ...]) -> TestResult:
        command = " && ".join(test_commands)
        ok, output = self.sandbox.execute(command)
        rc = 0 if ok else 1
        passed = verified_test_command_passed(command, rc, output or "")
        return TestResult(
            passed=passed,
            command=command,
            rc=rc,
            output=output or "",
        )

    def close(self) -> None:
        self.sandbox.close()
