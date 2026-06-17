# src/envstate/contracts/validators.py
"""Read-only validator registry + host auto-run (spec §5 Validator, §7 rule 4)."""
from __future__ import annotations

from typing import Any, Callable

from . import ids
from .graph import ContractGraph
from .nodes import ContractStatusEvent, Edge, Node
from .schema import redact_secrets

ExecReadonly = Callable[[str], tuple[int, str]]

# kind -> (validator kind, command template using {subject}).
_REGISTRY: dict[str, tuple[str, str]] = {
    "python_package_importable": ("python_import_check", 'python -c "import {subject}"'),
    # Atomic precondition only — NOT the success gate (real execution stays the done-gate's job).
    "pytest_runnable": ("pytest_collect_check", "python -m pytest --collect-only -q --disable-warnings"),
}


def run_confirmed_validators(
    graph: ContractGraph, exec_readonly: ExecReadonly, revision: int
) -> tuple[list[Node], list[Edge], list[ContractStatusEvent]]:
    nodes: list[Node] = []
    edges: list[Edge] = []
    events: list[ContractStatusEvent] = []
    rid = ids.revision_id(revision)

    for contract in graph.nodes_by_type("Contract"):
        if contract.data.get("level") != "atomic":
            continue
        kind = str(contract.data.get("kind", ""))
        spec = _REGISTRY.get(kind)
        if spec is None:
            continue
        vkind, template = spec
        subject = str(contract.data.get("subject", ""))
        cmd = template.format(subject=subject)
        rc, out = exec_readonly(cmd)

        vid = ids.validator_id(vkind, ids.slug(subject) or subject)
        if not graph.has_node(vid):
            nodes.append(Node(vid, "Validator", {"kind": vkind, "command_template": template}))
            edges.append(Edge(contract.id, "verified_by", vid))

        run_id = f"cmd:val:{ids.slug(vid)}:{revision:03d}"
        nodes.append(
            Node(run_id, "CommandExecution",
                 {"command": redact_secrets(cmd), "exit_code": int(rc),
                  "revision_before": rid, "revision_after": rid, "mutation_class": None})
        )
        status = "satisfied" if rc == 0 else "violated"
        events.append(
            ContractStatusEvent(
                contract_id=contract.id, status=status, revision_id=rid,
                evidence_ids=(run_id,), summary=redact_secrets(out[-200:]),
            )
        )
    return nodes, edges, events
