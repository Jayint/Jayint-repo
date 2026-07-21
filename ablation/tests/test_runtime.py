from __future__ import annotations

from types import SimpleNamespace

import pytest

from ablation.models import FlatBlock, FlatPlan
from ablation.runtime import SandboxHost
from ablation.script import render_plan


class FakeSandbox:
    def __init__(self):
        self.setup_result = SimpleNamespace(
            rc=0,
            stderr="",
            failing_command=None,
            lineno=None,
        )
        self.readonly_results: dict[str, tuple[int, str]] = {}
        self.test_result: tuple[bool, str] = (True, "2 passed in 0.01s")
        self.calls: list[tuple[str, str]] = []

    def run_install_script(self, script):
        self.calls.append(("setup", script))
        return self.setup_result

    def exec_readonly(self, command):
        self.calls.append(("check", command))
        return self.readonly_results.get(command, (0, "ok"))

    def execute(self, command):
        self.calls.append(("test", command))
        return self.test_result


def sample_plan() -> FlatPlan:
    return FlatPlan(
        (
            FlatBlock(
                "b01",
                ("python -m pip install -e .",),
                ("python -m pip check", "which pytest"),
                ("file:pyproject.toml",),
            ),
        )
    )


def test_sandbox_host_preserves_setup_failure_metadata():
    sandbox = FakeSandbox()
    sandbox.setup_result = SimpleNamespace(
        rc=7,
        stderr="build failed",
        failing_command="python -m pip install -e .",
        lineno=9,
    )
    host = SandboxHost(sandbox)

    result = host.run_setup(render_plan(sample_plan()))

    assert result.rc == 7
    assert result.output == "build failed"
    assert result.failing_command == "python -m pip install -e ."
    assert result.lineno == 9


def test_sandbox_host_stops_checks_at_first_failure():
    sandbox = FakeSandbox()
    sandbox.readonly_results["python -m pip check"] = (1, "broken dependency")
    host = SandboxHost(sandbox)

    result = host.run_checks(sample_plan())

    assert not result.passed
    assert result.block_id == "b01"
    assert result.command == "python -m pip check"
    assert ("check", "which pytest") not in sandbox.calls


@pytest.mark.parametrize(
    "output",
    (
        "no tests ran in 0.01s",
        "3 skipped in 0.02s",
        "Ran 0 tests in 0.000s\nOK",
    ),
)
def test_sandbox_host_rejects_hollow_test_success(output):
    sandbox = FakeSandbox()
    sandbox.test_result = (True, output)
    host = SandboxHost(sandbox)

    result = host.run_tests(("python -m pytest -q",))

    assert not result.passed


def test_sandbox_host_accepts_fixed_test_only_with_real_passing_tests():
    sandbox = FakeSandbox()
    sandbox.test_result = (True, "2 passed in 0.03s")
    host = SandboxHost(sandbox)

    result = host.run_tests(("python -m pytest -q",))

    assert result.passed
    assert result.command == "python -m pytest -q"
