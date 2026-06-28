# src/python_deps/depgraph/req_slice.py
"""Read-time derivation of the structured RequirementSlice the v3 agent sees (design
2026-06-29). Pure: no Docker/LLM/subprocess and NO dependency on src.envstate."""
from __future__ import annotations

import re
from dataclasses import dataclass

from python_deps.depgraph.action_class import ACTION_CLASSES   # pure leaf (only imports re); no cycle


@dataclass(frozen=True)
class ProviderCand:
    id: str
    action_class: str            # "apt" | "pip" | "npm" | "shell" (action_class.py) | "" (undeterminable)


@dataclass(frozen=True)
class TriedProvider:
    command: str
    outcome: str
    provider_id: str | None      # best-effort reverse-parse of `command`; None for batch/unparseable


@dataclass(frozen=True)
class ProviderView:
    candidates: tuple[ProviderCand, ...]
    chosen: str | None
    tried_failed: tuple[TriedProvider, ...]


def _action_class_for(provider_id: str) -> str:
    # Reuse the canonical provider taxonomy (apt/pip/npm/shell) — do NOT reimplement the map.
    head = provider_id.split(":", 1)[0] if ":" in provider_id else ""
    return head if head in ACTION_CLASSES else ""


def _provider_from_command(command: str) -> str | None:
    """Map an install command back to a provider id when EXACTLY one package is named
    (single-token); batch installs are not cleanly attributable -> None."""
    m = re.search(r"\bapt(?:-get)?\s+install\b(.*)", command)
    if m:
        toks = [t for t in m.group(1).split() if not t.startswith("-")]
        return f"apt:{toks[0]}" if len(toks) == 1 else None
    m = re.search(r"\bpip3?\s+install\b(.*)", command)
    if m:
        toks = [t for t in m.group(1).split() if not t.startswith("-")]
        return f"pip:{toks[0].split('==')[0]}" if len(toks) == 1 else None
    return None


def providers_view(node) -> ProviderView:
    cand_ids = list(node.fix_candidates)
    if node.chosen_fix and node.chosen_fix not in cand_ids:
        cand_ids.append(node.chosen_fix)
    candidates = tuple(ProviderCand(id=c, action_class=_action_class_for(c)) for c in cand_ids)
    tried = tuple(
        TriedProvider(command=a.command, outcome=a.outcome,
                      provider_id=_provider_from_command(a.command))
        for a in node.attempts if a.outcome == "failed"
    )
    return ProviderView(candidates=candidates, chosen=node.chosen_fix, tried_failed=tried)
