"""Command Executor abstraction — the agnostic execution seam (protocol only).

The Executor is an interface so scan/resolve/probe/certify are testable with a
``FakeExecutor`` (no Docker, no network). The concrete host + Docker
implementations live in ``graph/executors.py`` (``LocalSubprocessExecutor``,
``DockerExecutor``), split out in Phase 2 T6 so this seam file stays a pure
protocol — no ``subprocess``, no ``docker``.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass

# returncode used when a command exceeds its timeout.
TIMEOUT_RC = 124


@dataclass(frozen=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@typing.runtime_checkable
class Executor(typing.Protocol):
    def run(self, command: str, *, timeout: int = 300) -> CommandResult: ...
