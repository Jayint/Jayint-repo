from __future__ import annotations
from dataclasses import replace
from typing import Any, Optional

from src.envstate.types import (
    EnvStateSnapshot,
    Evidence,
    HOST_ONLY_SOURCES,
    LLM_ALLOWED_SOURCES,
    LLM_ALLOWED_STATUSES,
    PRESENCE_STATUSES,
    Requirement,
    Source,
    Status,
)


def _replace_requirement(
    snapshot: EnvStateSnapshot, requirement_id: str, new_req: Requirement
) -> EnvStateSnapshot:
    found = False
    updated = []
    for req in snapshot.requirements:
        if req.id == requirement_id:
            updated.append(new_req)
            found = True
        else:
            updated.append(req)
    if not found:
        updated.append(new_req)
    return replace(snapshot, requirements=tuple(updated))


def certify_from_probe(
    snapshot: EnvStateSnapshot,
    requirement_id: str,
    status: str,
    evidence: Evidence,
) -> EnvStateSnapshot:
    """HOST-ONLY. The only path that may set PRESENT/MISSING with Evidence."""
    if status not in PRESENCE_STATUSES:
        raise ValueError(f"certify_from_probe only sets {PRESENCE_STATUSES}, got {status!r}")
    if evidence.env_revision != snapshot.revision:
        raise ValueError(
            f"Evidence revision {evidence.env_revision} != current snapshot revision "
            f"{snapshot.revision}; refusing to certify stale evidence."
        )
    existing = next((r for r in snapshot.requirements if r.id == requirement_id), None)
    if existing is None:
        new_req = Requirement(
            id=requirement_id, name=requirement_id, kind="Tool",
            status=status, source=Source.PROBE, evidence=evidence,
        )
    else:
        new_req = replace(existing, status=status, source=Source.PROBE, evidence=evidence)
    return _replace_requirement(snapshot, requirement_id, new_req)


def _derive_name_from_id(id_val: str) -> str:
    """Derive a bare name from an id by stripping a known kind prefix."""
    for prefix in ("pkg:", "tool:", "header:", "lib:", "pkgconfig:", "path:"):
        if id_val.startswith(prefix):
            return id_val[len(prefix):]
    # Unknown or no prefix: use as-is after stripping up to the first colon.
    if ":" in id_val:
        return id_val.split(":", 1)[1]
    return id_val


def _validate_llm_requirement(raw: dict[str, Any]) -> Optional[str]:
    if not isinstance(raw, dict):
        return "candidate is not an object"
    # Accept when EITHER name OR id is present; derive name from id when needed.
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        id_val = raw.get("id")
        if isinstance(id_val, str) and id_val.strip():
            name = _derive_name_from_id(id_val.strip())
        else:
            return "missing required 'name' (and no 'id' to derive it from)"
    required_by = raw.get("required_by")
    if required_by is not None and not isinstance(required_by, (list, tuple)):
        return "required_by must be a list of strings"
    if isinstance(required_by, (list, tuple)) and not all(isinstance(x, str) for x in required_by):
        return "required_by entries must be strings"
    status = raw.get("status")
    source = raw.get("source", Source.LLM_GUESS)
    if status in PRESENCE_STATUSES:
        return f"LLM may not assert presence status {status!r}"
    if status not in LLM_ALLOWED_STATUSES:
        return f"status must be one of {sorted(LLM_ALLOWED_STATUSES)}, got {status!r}"
    if source not in LLM_ALLOWED_SOURCES:
        return f"source must be one of {sorted(LLM_ALLOWED_SOURCES)}, got {source!r}"
    if raw.get("evidence") is not None:
        return "LLM may not attach Evidence"
    return None


def apply_llm_proposal(
    snapshot: EnvStateSnapshot, proposal: dict[str, Any]
) -> tuple[EnvStateSnapshot, list[dict[str, Any]]]:
    """Merge LLM-proposed candidate_requirements after ACL validation.

    Returns (new_snapshot, rejected) where rejected items each carry a `reason`.
    Rejections are dropped + logged, never raised.
    """
    accepted: list[Requirement] = []
    rejected: list[dict[str, Any]] = []
    for raw in proposal.get("candidate_requirements") or []:
        reason = _validate_llm_requirement(raw)
        if reason is not None:
            rejected.append({"candidate": raw, "reason": reason})
            continue
        # Derive name from id if name is missing (normalisation may have missed it).
        _name = raw.get("name")
        if not isinstance(_name, str) or not _name.strip():
            _id_val = raw.get("id", "")
            _name = _derive_name_from_id(_id_val) if _id_val else ""
        _kind = raw.get("kind", "Tool")
        _id = raw.get("id") or f"{_kind.lower()}:{_name}"
        accepted.append(
            Requirement(
                id=_id,
                name=_name,
                kind=_kind,
                status=raw["status"],
                source=raw.get("source", Source.LLM_GUESS),
                specifier=raw.get("specifier"),
                required_by=tuple(raw.get("required_by") or ()),
            )
        )
    if not accepted:
        return snapshot, rejected
    by_id = {r.id: r for r in snapshot.requirements}
    for req in accepted:
        # Never let an LLM hypothesis overwrite a host-certified fact (PROBE or DIAGNOSE).
        existing = by_id.get(req.id)
        if existing is not None and existing.source in HOST_ONLY_SOURCES:
            rejected.append({"candidate": req.id, "reason": "would overwrite host-certified fact"})
            continue
        by_id[req.id] = req
    return replace(snapshot, requirements=tuple(by_id.values())), rejected


def advance_revision(snapshot: EnvStateSnapshot, mutation_class: str) -> EnvStateSnapshot:
    """Bump revision on an env-mutating action; demote now-stale presence facts."""
    new_revision = snapshot.revision + 1
    live: list[Requirement] = []
    newly_stale: list[Requirement] = []
    for req in snapshot.requirements:
        is_presence = req.status in PRESENCE_STATUSES and req.evidence is not None
        if is_presence and req.evidence.env_revision < new_revision:
            newly_stale.append(req)
            live.append(replace(req, status=Status.UNKNOWN, source=Source.LLM_GUESS, evidence=None))
        else:
            live.append(req)
    return replace(
        snapshot,
        revision=new_revision,
        requirements=tuple(live),
        stale_evidence=snapshot.stale_evidence + tuple(newly_stale),
    )
