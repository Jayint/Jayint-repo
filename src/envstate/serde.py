"""v0 snapshot serialisation helpers — to be deleted with Task 40.

types.py (v0) removed: BaseFacts/EnvStateSnapshot/etc. stubs kept as Any so
that this module remains importable until Task 40 deletes it.
"""
from __future__ import annotations
from dataclasses import asdict
from typing import Any, Optional

# v0 type stubs (types.py deleted in Task 39) — replaced with Any.
BaseFacts: Any = None
EnvStateSnapshot: Any = None
Evidence: Any = None
OpenFailure: Any = None
ProviderFact: Any = None
Requirement: Any = None


def snapshot_to_dict(snapshot: EnvStateSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def _evidence_from_dict(data: Optional[dict[str, Any]]) -> Optional[Evidence]:
    if not data:
        return None
    return Evidence(**data)


def _requirement_from_dict(data: dict[str, Any]) -> Requirement:
    data = dict(data)
    data["required_by"] = tuple(data.get("required_by") or ())
    data["provides"] = tuple(data.get("provides") or ())
    data["suspected_provides"] = tuple(data.get("suspected_provides") or ())
    data["evidence"] = _evidence_from_dict(data.get("evidence"))
    return Requirement(**data)


def snapshot_from_dict(data: dict[str, Any]) -> EnvStateSnapshot:
    base_data = dict(data["base"])
    base_data.setdefault("workdir", None)
    return EnvStateSnapshot(
        revision=data["revision"],
        container_id=data["container_id"],
        base=BaseFacts(**base_data),
        requirements=tuple(_requirement_from_dict(r) for r in data.get("requirements", ())),
        provider_facts=tuple(
            ProviderFact(
                provider=p["provider"],
                provides=tuple(p.get("provides") or ()),
                source=p["source"],
                diagnose_cmd=p.get("diagnose_cmd"),
            )
            for p in data.get("provider_facts", ())
        ),
        open_failures=tuple(
            OpenFailure(
                signature=f["signature"],
                first_seen_revision=f["first_seen_revision"],
                last_seen_revision=f["last_seen_revision"],
                hypothesis=f.get("hypothesis"),
                already_tried=tuple(f.get("already_tried") or ()),
            )
            for f in data.get("open_failures", ())
        ),
        stale_evidence=tuple(_requirement_from_dict(r) for r in data.get("stale_evidence", ())),
        plan_notes=tuple(data.get("plan_notes") or ()),
        repo_structure=data.get("repo_structure") or "",
    )
