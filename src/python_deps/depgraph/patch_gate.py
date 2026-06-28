"""Deterministic PatchGate (design §10): validate -> apply -> recompose.

The v3 replacement for the LLM Maintainer. validate_proposal returns an error list
(empty = accept); apply_proposal is a pure immutable reducer that NEVER writes
SATISFIED; compose_script re-derives the artifact from the graph plus governed
manual blocks. Pure: no Docker/network/LLM."""
from __future__ import annotations

import re

from python_deps.depgraph.action_class import matches_action_class
from python_deps.depgraph.patch import (
    PatchProposal, NodeSpec, ProviderSpec, EdgeSpec, ScriptPatch,
)
from python_deps.depgraph.schema import (
    DepGraph, NodeType, Layer, EdgeType, EDGE_RULES,
)

# Node-type -> canonical id prefix (ids.py).  Types not listed accept any "<kind>:<rest>".
_KIND_PREFIX: dict[NodeType, str] = {
    NodeType.PACKAGE: "pkg:", NodeType.SYSTEM_LIB: "syslib:", NodeType.TOOL: "tool:",
    NodeType.CONFIG: "config:", NodeType.SERVICE: "service:", NodeType.RUNTIME: "runtime:",
    NodeType.IMPORT: "import:", NodeType.PROJECT: "project:",
}
_ALLOWED_PROMOTION = frozenset({"hint", "candidate"})
_MUTATING = re.compile(
    r"(\bapt-get\s+install\b|\bpip\s+install\b|\bnpm\s+(install|ci)\b|\brm\s|\bmkdir\s|>>|>)")


def _node_type(value: str) -> NodeType | None:
    try:
        return NodeType(value)
    except ValueError:
        return None


def validate_proposal(graph: DepGraph, proposal: PatchProposal, *,
                      known_evidence_ids: frozenset[str]) -> list[str]:
    errs: list[str] = []
    existing_ids = {n.id for n in graph.nodes}
    proposed_node_ids = {r.id for r in proposal.add_requirements}

    # within-proposal duplicate ids (nodes / providers / script blocks)
    for label, ids in (("add_requirements", [r.id for r in proposal.add_requirements]),
                       ("add_providers", [p.id for p in proposal.add_providers]),
                       ("script_patches", [s.block_id for s in proposal.script_patches])):
        if len(ids) != len(set(ids)):
            errs.append(f"duplicate id within {label}")

    for r in proposal.add_requirements:
        nt = _node_type(r.type)
        if nt is None:
            errs.append(f"unknown node type {r.type!r} for {r.id}"); continue
        try:
            Layer(r.layer)
        except ValueError:
            errs.append(f"unknown layer {r.layer!r} for {r.id}")
        prefix = _KIND_PREFIX.get(nt)
        if prefix is not None and not r.id.startswith(prefix):
            errs.append(f"non-canonical id {r.id!r}: {nt.value} requires prefix {prefix!r}")
        elif ":" not in r.id:
            errs.append(f"non-canonical id {r.id!r}: missing '<kind>:' prefix")
        if r.promotion is not None and r.promotion not in _ALLOWED_PROMOTION:
            errs.append(f"illegal promotion {r.promotion!r} for {r.id} "
                        f"(only {sorted(_ALLOWED_PROMOTION)} or none; SATISFIED is host-only)")
        if not r.evidence_ref or r.evidence_ref not in known_evidence_ids:
            errs.append(f"requirement {r.id} cites unknown/absent evidence {r.evidence_ref!r}")
        if r.check_command and _MUTATING.search(r.check_command):
            errs.append(f"check command for {r.id} is not read-only: {r.check_command!r}")
        # conflicting redefinition vs graph
        cur = graph.get(r.id)
        if cur is not None and (cur.type.value != r.type or cur.layer.value != r.layer
                                or (cur.check_command or None) != (r.check_command or None)):
            errs.append(f"conflicting redefinition of existing node {r.id}")

    for p in proposal.add_providers:
        if not matches_action_class(p.kind, p.command):
            errs.append(f"provider {p.id} command does not match action class "
                        f"{p.kind!r}: {p.command!r}")

    known_after = existing_ids | proposed_node_ids
    for s in proposal.script_patches:
        if not s.evidence_ref or s.evidence_ref not in known_evidence_ids:
            errs.append(f"script block {s.block_id} cites unknown/absent evidence {s.evidence_ref!r}")
        for nid in s.target_node_ids:
            if nid not in known_after:
                errs.append(f"script block {s.block_id} targets unknown node {nid!r}")
        for chk in s.checks:
            if _MUTATING.search(chk):
                errs.append(f"script block {s.block_id} check is not read-only: {chk!r}")

    # edges: replicate EDGE_RULES against the post-add_requirements view (with_edge would RAISE).
    type_of = {n.id: n.type.value for n in graph.nodes}
    type_of.update({r.id: r.type for r in proposal.add_requirements})
    for e in proposal.add_edges:
        try:
            EdgeType(e.relation)
        except ValueError:
            errs.append(f"unknown edge relation {e.relation!r}"); continue
        rule = EDGE_RULES.get(e.relation)
        if e.source not in type_of or e.target not in type_of:
            errs.append(f"edge {e.relation} references unknown node(s): {e.source!r} -> {e.target!r}")
            continue
        if rule is not None:
            allowed_src, allowed_dst = rule
            if type_of[e.source] not in allowed_src:
                errs.append(f"illegal {e.relation} source type {type_of[e.source]!r} ({e.source!r})")
            if type_of[e.target] not in allowed_dst:
                errs.append(f"illegal {e.relation} destination type {type_of[e.target]!r} ({e.target!r})")

    return errs
