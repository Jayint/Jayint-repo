"""Typed PatchProposal model + tolerant parser (design §9). Pure: no Docker/network/LLM.

The model is the v3 LLM contract (invariant #6): the only accepted state change is a
PatchProposal. NodeSpec deliberately has NO `state` field — the model cannot certify."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: str            # NodeType value, e.g. "SystemLib"
    name: str
    layer: str           # Layer value, e.g. "system"
    check_command: str | None = None
    evidence_ref: str | None = None
    promotion: str | None = None   # "hint" | "candidate" | None (gate validates)


@dataclass(frozen=True)
class ProviderSpec:
    id: str              # e.g. "apt:libplacebo-dev"
    kind: str            # action class, e.g. "apt" | "pip" | "npm" | "shell"
    command: str
    provides: tuple[str, ...] = ()


@dataclass(frozen=True)
class EdgeSpec:
    source: str
    target: str
    relation: str = "requires"
    hard: bool = True


@dataclass(frozen=True)
class ScriptPatch:
    block_id: str
    wave: str
    commands: tuple[str, ...]
    target_node_ids: tuple[str, ...]
    op: str = "add_block"
    checks: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    evidence_ref: str | None = None


@dataclass(frozen=True)
class PatchProposal:
    rationale: dict = field(default_factory=dict)   # advisory only
    add_requirements: tuple[NodeSpec, ...] = ()
    add_providers: tuple[ProviderSpec, ...] = ()
    add_edges: tuple[EdgeSpec, ...] = ()
    script_patches: tuple[ScriptPatch, ...] = ()
    request_checks: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.add_requirements or self.add_providers or self.add_edges
                    or self.script_patches or self.request_checks)


def _as_tuple(x) -> tuple:
    return tuple(x) if isinstance(x, (list, tuple)) else ()


def parse_patch_proposal(d: dict) -> PatchProposal:
    d = d or {}
    patch = d.get("patch", d)
    rationale = d.get("rationale", {})
    if not isinstance(rationale, dict):
        rationale = {}
    reqs = tuple(NodeSpec(
        id=r["id"], type=r["type"], name=r.get("name", ""), layer=r["layer"],
        check_command=r.get("check_command"), evidence_ref=r.get("evidence_ref"),
        promotion=r.get("promotion") if r.get("promotion") is not None else r.get("state"),
    ) for r in _as_tuple(patch.get("add_requirements")))
    provs = tuple(ProviderSpec(
        id=p["id"], kind=p["kind"], command=p["command"], provides=_as_tuple(p.get("provides")),
    ) for p in _as_tuple(patch.get("add_providers")))
    edges = tuple(EdgeSpec(
        source=e["source"], target=e["target"],
        relation=e.get("relation", "requires"), hard=bool(e.get("hard", True)),
    ) for e in _as_tuple(patch.get("add_edges")))
    sps = tuple(ScriptPatch(
        block_id=s["block_id"], wave=s["wave"],
        commands=_as_tuple(s.get("commands")) or ((s["command"],) if s.get("command") else ()),
        target_node_ids=_as_tuple(s.get("target_node_ids")),
        op=s.get("op", "add_block"), checks=_as_tuple(s.get("checks")),
        provides=_as_tuple(s.get("provides")), evidence_ref=s.get("evidence_ref"),
    ) for s in _as_tuple(patch.get("script_patches")))
    return PatchProposal(
        rationale=rationale, add_requirements=reqs, add_providers=provs, add_edges=edges,
        script_patches=sps, request_checks=_as_tuple(patch.get("request_checks")),
    )
