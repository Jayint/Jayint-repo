"""Shared test scaffolding for the depgraph package.

Provides:
  * a sys.path shim so ``python_deps.depgraph.*`` imports without installation;
  * ``FakeExecutor`` — an in-memory ``Executor`` returning canned results keyed
    by command substring (longest matching key wins).  Downstream tasks (resolve,
    probe, certify, build) depend on this exact API;
  * ``make_result`` — a terse ``CommandResult`` constructor.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Put <worktree>/src on the path: this file is tests/depgraph/conftest.py.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

from python_deps.depgraph.executor import CommandResult  # noqa: E402


def make_result(
    stdout: str = "",
    returncode: int = 0,
    stderr: str = "",
    command: str = "",
) -> CommandResult:
    """Build a CommandResult quickly for canned FakeExecutor responses."""
    return CommandResult(
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class FakeExecutor:
    """Deterministic Executor for unit tests (no Docker/network/uv).

    ``run(command)`` returns the canned ``CommandResult`` whose key is a substring
    of ``command`` (longest matching key wins); else ``default``; else a
    ``CommandResult(command, 127, "", "no fake response")``.  Every command is
    appended to ``self.calls``.
    """

    def __init__(
        self,
        responses: dict[str, CommandResult] | None = None,
        default: CommandResult | None = None,
    ) -> None:
        self.responses: dict[str, CommandResult] = dict(responses or {})
        self.default: CommandResult | None = default
        self.calls: list[str] = []
        # Per-call timeouts, parallel to ``calls`` (lets tests assert that a slow
        # stage like the bulk closure install asks for enough headroom).
        self.timeouts: list[int] = []

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        self.calls.append(command)
        self.timeouts.append(timeout)
        matches = [key for key in self.responses if key in command]
        if matches:
            best = max(matches, key=len)
            return self.responses[best]
        if self.default is not None:
            return self.default
        return CommandResult(
            command=command,
            returncode=127,
            stdout="",
            stderr="no fake response",
        )


@pytest.fixture
def make_result_fixture():
    return make_result


@pytest.fixture
def fake_executor():
    """An empty FakeExecutor; tests populate ``.responses`` as needed."""
    return FakeExecutor()
