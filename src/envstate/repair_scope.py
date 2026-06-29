# src/envstate/repair_scope.py
"""The §9 RepairScope packet (2b §6.1): curated, structured context for the v3 typed-patch
agent. Pure: no Docker/network/LLM. Re-derives the Phase-1 RequirementSlice; never raw history."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.req_slice import build_requirement_slice, render_requirement_slice


PATCH_SCHEMA_HINT = """\
Respond with EXACTLY ONE fenced JSON object and nothing after it:
```json
{
  "rationale": {"why": "<one sentence>"},
  "patch": {
    "add_requirements": [{"id": "syslib:<name>", "type": "SystemLib", "name": "<name>",
       "layer": "system", "check_command": "<read-only check>", "evidence_ref": "<ev.id>"}],
    "add_providers": [{"id": "apt:<pkg>", "kind": "apt",
       "command": "apt-get install -y <pkg>", "provides": ["syslib:<name>"], "override": false}],
    "add_edges": [{"source": "<id>", "target": "<id>", "relation": "requires", "hard": true}],
    "script_patches": [{"block_id": "<layer>.<short>", "wave": "system",
       "commands": ["<install>"], "target_node_ids": ["<node id>"],
       "checks": ["<read-only check>"], "provides": ["<id>"], "evidence_ref": "<ev.id>"}]
  }
}
```
Rules: canonical ids (syslib:/pkg:/tool:/service:/config:). check_command MUST be read-only.
Cite an evidence_ref present in the evidence below. Set "override": true to replace a known-bad provider."""


@dataclass(frozen=True)
class RepairScope:
    target_node_id: str | None
    failed_command: str | None
    failed_output: str
    slice_lines: tuple[str, ...]
    known_invalid: tuple[str, ...]
    constraints: tuple[tuple[str, str], ...]
    known_evidence_ids: frozenset[str]


def build_repair_scope(graph, *, target_node_id, failed_block, bundle,
                       known_invalid=(), constraints=None):
    cons = tuple(sorted((str(k), str(v)) for k, v in dict(constraints or {}).items()))
    # Use hasattr so tests can pass object() as graph with a monkeypatched build_requirement_slice.
    # In production graph is a real DepGraph with .get(); in tests the lambda ignores node.
    node = graph.get(target_node_id) if (graph is not None and target_node_id
                                         and hasattr(graph, 'get')) else None
    slice_lines = ()
    # Enter when (a) node resolved from a real graph, OR (b) graph has no .get (test stub:
    # build_requirement_slice is monkeypatched and ignores node, so None is safe). Never enter
    # when a real DepGraph lookup returned None (avoids build_requirement_slice(graph, None) -> None.id).
    if target_node_id and (node is not None or not hasattr(graph, 'get')):
        slice_lines = tuple(render_requirement_slice(build_requirement_slice(graph, node)))
    failed_cmd = failed_block.commands[-1] if (failed_block and failed_block.commands) else None
    failed_out = ""
    # bundle is None on the binding-install repair path (no obligation packet — the failure
    # evidence is the install stderr, surfaced via the debug bundle, not a ledger packet).
    # Treat a missing bundle as empty evidence rather than crashing on bundle.items.
    _evidence = bundle.items if bundle is not None else ()
    for ev in _evidence:
        if ev.rc != 0 and (failed_block is None or ev.block_id == failed_block.block_id):
            failed_cmd = failed_cmd or ev.command
            failed_out = ev.output_excerpt or ""
    return RepairScope(
        target_node_id=target_node_id, failed_command=failed_cmd, failed_output=failed_out,
        slice_lines=slice_lines, known_invalid=tuple(known_invalid), constraints=cons,
        known_evidence_ids=frozenset(ev.evidence_id for ev in _evidence))


def render_repair_scope(scope: RepairScope) -> str:
    parts = []
    if scope.target_node_id:
        parts.append(f"Failing obligation: {scope.target_node_id}")
    if scope.slice_lines:
        parts.append("Graph context:\n" + "\n".join(scope.slice_lines))
    if scope.failed_command:
        parts.append(f"Failed command: {scope.failed_command}")
    if scope.failed_output:
        parts.append("Failure output:\n" + scope.failed_output)
    if scope.known_invalid:
        parts.append("DO NOT propose these (already failed): " + ", ".join(scope.known_invalid))
    if scope.constraints:
        parts.append("Constraints: " + ", ".join(f"{k}={v}" for k, v in scope.constraints))
    parts.append("Cite evidence by id (available: "
                 + ", ".join(sorted(scope.known_evidence_ids)) + ").")
    parts.append(PATCH_SCHEMA_HINT)
    return "\n\n".join(parts)
