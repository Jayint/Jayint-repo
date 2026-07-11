from __future__ import annotations

from src.manifest_builder.types import CollectionResult, Verdict


def accept(r1: CollectionResult, r2: CollectionResult, protected_ok: bool) -> Verdict:
    reasons: list[str] = []
    if r1.exit_code != 0:
        reasons.append(f"run1 exit {r1.exit_code} != 0")
    if r2.exit_code != 0:
        reasons.append(f"run2 exit {r2.exit_code} != 0")
    if r1.collected_count == 0:
        reasons.append("no items collected (hollow)")
    if set(r1.collected) != set(r2.collected):
        reasons.append("node-id set unstable across runs")
    if not protected_ok:
        reasons.append("protected files modified")
    accepted = not reasons
    manifest = tuple(sorted(set(r1.collected))) if accepted else None
    return Verdict(accepted=accepted, reasons=tuple(reasons), manifest=manifest,
                   collected_count=r1.collected_count)


def pick_best(verdicts: list[Verdict]) -> Verdict | None:
    accepted = [v for v in verdicts if v.accepted]
    return max(accepted, key=lambda v: v.collected_count) if accepted else None
