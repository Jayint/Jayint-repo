"""Offline 'reality' model for the arm-C mechanics eval.

A node installs iff its REAL requirements (which the graph does NOT initially know) are
present; a check passes iff the node's capability is present. This reality is the ground
truth the agent must make the graph match. Uses the REAL ``certify_all`` + REAL ``DepGraph``
— only the container (install + check) is faked. No Docker, no LLM. See spec 2026-07-08."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.schema import DepGraph, Node, NodeType
from src.envstate.repair_types import ReplayResult


@dataclass(frozen=True)
class RealNode:
    provides: str                    # capability key this node yields once installed
    requires: frozenset              # capabilities that must ALREADY be present to install
    check_command: str               # the read-only check that verifies ``provides``


class FakeWorld:
    """Simulates install + check against a reality model. No Docker."""

    def __init__(self, reality: dict[str, RealNode], base=("python",)):
        self.reality = reality
        self.base = frozenset(base)
        self.present: set[str] = set(self.base)
        self.check_map = {rn.check_command: rn.provides for rn in reality.values()}

    def _installable(self, n: Node) -> bool:
        if n.type is NodeType.PACKAGE:
            return bool(n.version)
        if n.type in (NodeType.SYSTEM_LIB, NodeType.TOOL):
            return bool(n.chosen_fix)
        return False

    def replay_from_base(self, graph: DepGraph, manual_blocks=()) -> ReplayResult:
        """Fresh from base: install installable nodes in tier order; fail at the first
        node whose REAL requirement is missing (the gap the graph does not yet capture)."""
        self.present = set(self.base)
        for n in sorted((n for n in graph.nodes if self._installable(n)), key=lambda n: n.tier):
            r = self.reality.get(n.id)
            if r is None:                            # unknown node: installs, tracks nothing
                continue
            missing = [req for req in sorted(r.requires) if req not in self.present]
            if missing:
                cmd = n.chosen_fix or f"pip install {n.name}"
                return ReplayResult(False, n.id, missing[0], cmd, f"{missing[0]}: not found")
            self.present.add(r.provides)
        return ReplayResult(True)

    def _executor(self):
        world = self

        class _Ex:
            def run(self, command, *, timeout=300):
                cap = world.check_map.get(command)
                ok = cap is not None and cap in world.present
                return CommandResult(command, 0 if ok else 1, "", "" if ok else "not found")

        return _Ex()

    def certify(self, graph: DepGraph) -> DepGraph:
        """REAL certify_all against the faked host — only a passing check flips state."""
        return certify_all(graph, self._executor())

    def readonly(self, command) -> tuple[int, str]:
        cap = self.check_map.get(command)
        ok = cap is not None and cap in self.present
        return (0 if ok else 1, "present" if ok else "absent")
