"""Phase-0 advisory render: a ``DepGraph`` -> the string the planner reads.

This is the *single integration surface* for the dep-graph "shadow" stage
(``docs/superpowers/plans/2026-06-23-phase0-depgraph-advisory.md``): the graph is
built once in a scratch container, rendered here, and spliced into the planner
prompt.  Advisory only — nothing here mutates the graph, the map, or the loop.

The render is relevance-gated (``docs/DESIGN-tiered-layer-execution-strategy.md``
§3): full detail on the unsatisfied **frontier** (the nodes the planner can act
on) and a one-line **satisfied summary** (counts per layer, never one line per
node), so a 50-package closure stays a few lines, not a wall.
"""

from __future__ import annotations

import logging
import re

from python_deps.depgraph.build import build_dep_graph
from python_deps.depgraph.executor import Executor
from python_deps.depgraph.schema import DepGraph, Layer, Node, NodeType, State

logger = logging.getLogger(__name__)

_HEADER = "[DEPENDENCY GRAPH - advisory * host-certified in scratch container]"

# Bottom-up layer order, so the frontier reads base -> ... -> tests.
_LAYER_RANK: dict[Layer, int] = {
    Layer.INTERPRETER: 0,
    Layer.SYSTEM: 1,
    Layer.TOOLCHAIN: 2,
    Layer.PIP: 3,
    Layer.NAMING: 4,
    Layer.RUNTIME: 5,
    Layer.TESTS: 6,
}

# Lines that actually name a cause sit at the END of a traceback, not the header.
_ERROR_HINT = re.compile(
    r"(error|not found|not installed|no information|cannot open|no module|"
    r"no such file|unable to locate|failed|missing)",
    re.I,
)
_TRACEBACK_HEADER = "traceback (most recent call last):"
_MAX_EVIDENCE = 160


def _best_evidence_line(evidence: str | None) -> str | None:
    """The most informative line of ``evidence``.

    A probe/certify failure stores the command's full stderr; the useful line is
    the *cause* at the end of a traceback (``ImportError: libGL.so.1: ...``), not
    the generic ``Traceback (most recent call last):`` header the naive ``[0]``
    slice would pick.  Prefer the last error-looking line, else the last line.
    """
    if not evidence:
        return None
    lines = [ln.strip() for ln in evidence.splitlines() if ln.strip()]
    candidates = [ln for ln in lines if ln.lower() != _TRACEBACK_HEADER] or lines
    if not candidates:
        return None
    for line in reversed(candidates):
        if _ERROR_HINT.search(line):
            return line[:_MAX_EVIDENCE]
    return candidates[-1][:_MAX_EVIDENCE]


def _needed_by(graph: DepGraph, node: Node) -> list[str]:
    """Names of nodes that ``requires`` ``node`` — excluding the same-name
    Import->Package naming link (rendering it reads as "psycopg2 needed by
    psycopg2", which is noise, not a real dependency)."""
    names = {
        pred.name for pred in graph.required_by(node.id) if pred.name != node.name
    }
    return sorted(names)


def _project_deps(graph: DepGraph, proj_id: str) -> list[str]:
    return sorted(
        {
            n.name
            for n in graph.requires_of(proj_id)
            if n.type in (NodeType.PACKAGE, NodeType.IMPORT)
        }
    )


def _recent_attempts(node: Node, limit: int = 2) -> str:
    return "; ".join(f"{a.command} -> {a.outcome}" for a in node.attempts[-limit:])


def render_dep_graph_advisory(graph: DepGraph) -> str:
    """Render ``graph`` as the planner-facing advisory section.

    Returns ``""`` for an empty/uninformative graph (so the caller adds nothing
    to the prompt).
    """
    if not graph.nodes:
        return ""

    lines = [_HEADER]

    goal = next((n for n in graph.nodes if n.type is NodeType.TEST), None)
    proj = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
    if goal is not None:
        lines.append(f"GOAL     {goal.name:24} {goal.state.value}")
    if proj is not None:
        deps = _project_deps(graph, proj.id)
        lines.append(
            f"PROJECT  {proj.name:24} -> "
            f"{', '.join(deps) if deps else '(none declared)'}"
        )

    frontier = sorted(
        (
            n
            for n in graph.nodes
            if n.state is State.MISSING and n.type is not NodeType.TEST
        ),
        key=lambda n: (_LAYER_RANK.get(n.layer, 9), n.name),
    )
    if frontier:
        lines.append("")
        lines.append("FRONTIER (unsatisfied - act here):")
        for n in frontier:
            lines.append(
                f"  {n.layer.value.upper():9} {n.name}   [{n.type.value}]  MISSING"
            )
            ev = _best_evidence_line(n.evidence)
            if ev:
                lines.append(f"            evidence: {ev}")
            nb = _needed_by(graph, n)
            if nb:
                lines.append(f"            needed by: {', '.join(nb)}")
            if n.fix_candidates:
                lines.append(f"            fix-candidate: {', '.join(n.fix_candidates)}")
            attempts = _recent_attempts(n)
            if attempts:
                lines.append(f"            attempts: {attempts}")

    satisfied = [n for n in graph.nodes if n.state is State.SATISFIED]
    if satisfied:
        counts: dict[str, int] = {}
        for n in satisfied:
            counts[n.layer.value] = counts.get(n.layer.value, 0) + 1
        summary = " * ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        lines.append("")
        lines.append(f"SATISFIED (summary): {summary}")

    if len(lines) == 1:  # header only — nothing worth telling the planner
        return ""
    return "\n".join(lines)


def build_advisory_for_repo(
    repo_path: str,
    base_image: str,
    *,
    host_executor: Executor | None = None,
) -> tuple[str, DepGraph | None]:
    """Build the dep graph in a fresh scratch container and render the advisory.

    Returns ``(advisory_str, graph)``.  On **any** failure (Docker down, resolve
    error, install timeout) logs and returns ``("", None)`` — the caller must
    proceed exactly as if the feature were off (graceful degradation; the live
    agent container is never touched because probing uses its own throwaway
    ``DockerExecutor``).
    """
    try:
        from python_deps.depgraph.executor import (
            DockerExecutor,
            LocalSubprocessExecutor,
        )

        host = host_executor or LocalSubprocessExecutor()
        with DockerExecutor(base_image) as scratch:
            graph = build_dep_graph(repo_path, scratch, host_executor=host)
        return render_dep_graph_advisory(graph), graph
    except Exception as exc:  # noqa: BLE001 — advisory must never break a run
        logger.warning("dep-graph advisory unavailable: %s", exc)
        return "", None
