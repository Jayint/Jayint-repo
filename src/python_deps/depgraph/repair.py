"""Under-declaration repair ladder — candidate generation + 3-way decide.

PURE module: no Executor, no network, no graph, no subprocess. Ported from the
validated spike ``scripts/eval/graph_fidelity/underdeclaration_repair_poc.py``
(only its pure rungs — the ``_http_json``/``wheel_provides``/``install_and_import``
machinery is deliberately NOT here).

This is the candidate-generation + decision half of the ladder: given a runtime
import the declared/closure set does not provide, propose candidate *distribution
names* and, once someone hands back the empirically grounded subset, make a 3-way
decision. The RECORD-grounding half (P1.3) lands in this same file; the fixpoint
that drives it (P1.4) wires it into construction. Nothing here reaches a model,
a socket, or the graph.

Invariants held here:
  * Roots = declared only. This module proposes candidate distribution names for
    an unsatisfied import; it fabricates a root nowhere.
  * The LLM rung is an INJECTED callable defaulting to ``None`` — the
    deterministic core never calls a model on its own.
  * Never guess a variant: more than one grounded provider ⇒ AMBIGUOUS, never
    silently pick one.
  * Pure functions, frozen dataclasses, no mutation.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Callable

from python_deps.import_mapping import (
    CURATED_IMPORT_TO_PACKAGE,
    normalize_package_name,
    top_level_import_name,
)


@dataclass(frozen=True)
class Candidate:
    """A proposed distribution name and the rung that produced it."""

    dist: str
    source: str  # "normalize" | "curated" | "llm"


class Verdict(enum.Enum):
    """Outcome of grounding the candidate ladder for one import."""

    ACCEPT = "ACCEPT"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


def normalize_candidates(import_name: str) -> list[str]:
    """Mechanical name variants for an import's top-level, canon-deduped.

    Verbatim from the spike: ``top``, ``top.lower()``, dashed
    (``[_.]+`` -> ``-``), ``python-<dashed>``, ``<dashed>-python``. The first
    occurrence of each canonical form wins.
    """
    top = top_level_import_name(import_name)
    dashed = re.sub(r"[_.]+", "-", top.lower())
    raw = [top, top.lower(), dashed, f"python-{dashed}", f"{dashed}-python"]
    seen: set[str] = set()
    out: list[str] = []
    for candidate in raw:
        key = normalize_package_name(candidate)
        if key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def curated_candidates(import_name: str) -> list[str]:
    """The demoted curated remap, now an untrusted candidate source.

    Looks up ``CURATED_IMPORT_TO_PACKAGE`` by the lowercased top-level import
    name and returns ``[hit]`` when present, else ``[]``. The table is NO LONGER
    a root authority — it contributes one candidate rung that still has to be
    grounded downstream.

    Note: the lookup key is ``top_level.lower()`` (mirroring
    ``import_mapping.map_import_to_package``), not the packaging canonical form,
    because the table is keyed by real import spellings — some of which keep
    underscores (e.g. ``django_filters``) that canonicalization would mangle.
    """
    key = top_level_import_name(import_name).lower()
    hit = CURATED_IMPORT_TO_PACKAGE.get(key)
    return [hit] if hit else []


def generate_candidates(
    import_name: str,
    *,
    llm: Callable[[str], list[str]] | None = None,
) -> list[Candidate]:
    """Ordered candidate distributions: normalize -> curated -> (optional) llm.

    Deterministic rungs come first (``normalize`` then ``curated``); the ``llm``
    rung runs ONLY when an ``llm`` callable is injected, and its guesses land
    last. Canon-deduped with the first (cheapest) source winning. ``llm``
    defaults to ``None`` so the deterministic core never calls a model.
    """
    ordered: list[Candidate] = [
        Candidate(dist, "normalize") for dist in normalize_candidates(import_name)
    ]
    ordered += [Candidate(dist, "curated") for dist in curated_candidates(import_name)]
    if llm is not None:
        ordered += [Candidate(dist, "llm") for dist in llm(import_name)]

    seen: set[str] = set()
    out: list[Candidate] = []
    for candidate in ordered:
        key = normalize_package_name(candidate.dist)
        if key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def decide(grounded_dists: list[str]) -> tuple[Verdict, str]:
    """3-way decision over the empirically grounded distributions.

    Exactly one grounded provider -> ``(ACCEPT, dist)``; more than one ->
    ``(AMBIGUOUS, "<joined>")`` (never pick a variant); none ->
    ``(UNRESOLVED, "-")``. Verbatim 3-way branching from the spike.
    """
    grounded = sorted(grounded_dists)
    if len(grounded) == 1:
        return Verdict.ACCEPT, grounded[0]
    if len(grounded) > 1:
        return Verdict.AMBIGUOUS, ", ".join(grounded)
    return Verdict.UNRESOLVED, "-"
