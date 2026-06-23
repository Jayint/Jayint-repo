"""Stage 5 — host certification.

This realizes the certification invariant of design section 3.1: a node's
``state`` is flipped **only** by running its ``check_command`` on the host and
observing the exit code.  Install/import *actions* never imply ``satisfied`` —
only a passing check does.  State is therefore:

* **revocable** — re-certifying after a mutation can flip ``SATISFIED`` back to
  ``MISSING`` (a later install can break an earlier import; design 10.9);
* **host-issued** — nothing here is inferred from an action outcome.

``certify`` certifies one node; ``certify_all`` walks the graph in execution
layer order (design section 6): interpreter -> system -> toolchain -> pip ->
naming -> tests.  Every "mutation" returns a NEW ``DepGraph`` (repo immutability
rule).
"""

from __future__ import annotations

from python_deps.depgraph.executor import Executor
from python_deps.depgraph.schema import DepGraph, Layer, State

# Execution layer priority (design section 6).  Runtime is out of scope for this
# plan and is intentionally omitted from the certification walk.
_LAYER_ORDER: tuple[Layer, ...] = (
    Layer.INTERPRETER,
    Layer.SYSTEM,
    Layer.TOOLCHAIN,
    Layer.PIP,
    Layer.NAMING,
    Layer.TESTS,
)


def certify(
    graph: DepGraph,
    node_id: str,
    executor: Executor,
    cycle: int = 0,
) -> DepGraph:
    """Run one node's ``check_command`` and write its host-certified ``state``.

    * rc 0          -> ``SATISFIED`` with ``certified_cycle = cycle``;
    * rc != 0       -> ``MISSING`` with the check's stderr as ``evidence``;
    * no check_command -> left ``UNKNOWN`` (the host ran nothing).

    ``certified_cycle`` always records the cycle in which the host check was last
    actually run — including on the revocation path (SATISFIED -> MISSING) — so a
    consumer can distinguish "never certified" (``None``) from "certified then
    revoked" (the cycle of the failing re-check).

    Unknown ``node_id`` returns the graph unchanged.  Returns a NEW graph.
    """
    node = graph.get(node_id)
    if node is None or not node.check_command:
        return graph

    result = executor.run(node.check_command)
    if result.ok:
        updated = node.with_state(State.SATISFIED, cycle=cycle)
    else:
        updated = node.with_state(State.MISSING, evidence=result.stderr, cycle=cycle)
    return graph.with_node(updated)


def certify_all(
    graph: DepGraph,
    executor: Executor,
    cycle: int = 0,
) -> DepGraph:
    """Certify every node in execution layer order (design section 6).

    Re-reads the evolving graph after each certification so revocation/ordering
    side effects compose.  Returns a NEW graph.
    """
    new = graph
    for layer in _LAYER_ORDER:
        node_ids = [n.id for n in new.nodes if n.layer is layer]
        for node_id in node_ids:
            new = certify(new, node_id, executor, cycle=cycle)
    return new
