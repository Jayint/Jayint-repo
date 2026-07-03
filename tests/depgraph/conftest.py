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
from dataclasses import replace
from pathlib import Path

# Put <worktree>/src on the path: this file is tests/depgraph/conftest.py.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

from python_deps.depgraph.executor import CommandResult  # noqa: E402
from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)


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


class SequencedFakeExecutor:
    """Executor whose canned results can DIFFER across successive calls.

    Like :class:`FakeExecutor`, ``run(command)`` matches ``command`` against the
    substring keys (longest wins), but each key maps to a QUEUE of results: the
    first is popped per matching call and the LAST one repeats once the queue
    drains. This lets the Phase-A fixpoint's per-round commands (``uv lock`` /
    ``uv pip compile`` / ``pip install`` / ``packages_distributions``) return
    different output on round 1 vs round 2. ``default`` and the ``.calls`` /
    ``.timeouts`` bookkeeping mirror ``FakeExecutor`` exactly.

    Bonus for the ``uv lock`` path: when a matched ``uv lock`` command's result is
    ``ok`` and carries stdout, that stdout is written to the temp project's
    ``uv.lock`` (parsed from the leading ``cd <workdir> &&``), so a test can drive
    the real lock-read + drop-retry path (not just the degraded pip-compile
    fallback). A non-ok ``uv lock`` writes nothing, so the resolve falls back.
    """

    def __init__(
        self,
        responses: dict[str, list[CommandResult]] | None = None,
        default: CommandResult | None = None,
    ) -> None:
        self.responses: dict[str, list[CommandResult]] = {
            key: list(queue) for key, queue in (responses or {}).items()
        }
        self.default: CommandResult | None = default
        self.calls: list[str] = []
        self.timeouts: list[int] = []

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        self.calls.append(command)
        self.timeouts.append(timeout)
        matches = [key for key in self.responses if key in command]
        if matches:
            best = max(matches, key=len)
            queue = self.responses[best]
            if queue:
                result = queue.pop(0) if len(queue) > 1 else queue[0]
                self._maybe_write_lock(command, result)
                return result
        if self.default is not None:
            return self.default
        return CommandResult(
            command=command,
            returncode=127,
            stdout="",
            stderr="no fake response",
        )

    @staticmethod
    def _maybe_write_lock(command: str, result: CommandResult) -> None:
        import os
        import shlex

        if "uv lock" not in command or not result.ok or not result.stdout:
            return
        head = command.split("&&", 1)[0].strip()
        try:
            parts = shlex.split(head)
        except ValueError:
            return
        if len(parts) >= 2 and parts[0] == "cd":
            workdir = parts[1]
            try:
                with open(os.path.join(workdir, "uv.lock"), "w", encoding="utf-8") as fh:
                    fh.write(result.stdout)
            except OSError:
                pass


@pytest.fixture
def make_result_fixture():
    return make_result


@pytest.fixture
def fake_executor():
    """An empty FakeExecutor; tests populate ``.responses`` as needed."""
    return FakeExecutor()


# ---------------------------------------------------------------------------
# Slice B: shared graph / node fixtures
# ---------------------------------------------------------------------------

def _syslib_missing(node_id="syslib:libpq", name="libpq",
                    check="pkg-config --exists libpq", chosen_fix=None):
    n = Node(id=node_id, type=NodeType.SYSTEM_LIB, name=name, layer=Layer.SYSTEM,
             discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
             check_command=check, evidence="not found")
    if chosen_fix is not None:
        n = replace(n, chosen_fix=chosen_fix)
    return n


@pytest.fixture
def _graph_with_evidence():
    """(graph with one MISSING SystemLib, a known evidence id string)."""
    return DepGraph().with_node(_syslib_missing()), "ev.1.0"


@pytest.fixture
def _graph_with_missing_syslib():
    """A SystemLib node MISSING with a (wrong) chosen_fix, for apply/override tests."""
    return DepGraph().with_node(_syslib_missing(chosen_fix="apt:libpqdev"))
