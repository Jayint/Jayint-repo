"""Under-declaration repair ladder — candidate generation + 3-way decide.

PURE module: no Executor, no network, no graph, no subprocess. Ported from the
validated spike ``src/eval/graph_fidelity/underdeclaration_repair_poc.py``
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
    declared_metadata_match,
    normalize_package_name,
    top_level_import_name,
)


# The one rung that is EVIDENCE rather than a guess: a distribution the repo's own
# manifest declares. Named once so generate_candidates and choose_provider cannot
# drift apart on a bare string literal.
DECLARED_SOURCE = "declared_metadata"


@dataclass(frozen=True)
class Candidate:
    """A proposed distribution name and the rung that produced it."""

    dist: str
    source: str  # DECLARED_SOURCE | "normalize" | "curated" | "llm"


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


def declared_candidates(
    import_name: str,
    declared_package_names: "set[str] | frozenset[str] | None" = None,
) -> list[str]:
    """The repo's OWN declared distribution, if its name matches this import.

    Reuses :func:`python_deps.import_mapping.declared_metadata_match` — the same rung
    ``map_import_to_package`` already has. Returns ``[]`` when no declared name
    matches, or when ``declared_package_names`` is ``None`` (the default), so every
    existing caller keeps its old candidate set.

    ⚠ KNOWN LIMITATION — this rung is currently near-redundant, and that is on
    purpose rather than an oversight. ``declared_metadata_match`` matches by
    NORMALIZED NAME EQUALITY (import top-level == distribution name), so it fires only
    for ``freezegun`` -> ``freezegun``, never for ``yaml`` -> ``PyYAML`` or
    ``psycopg2`` -> ``psycopg2-binary`` — precisely the import/distribution mismatches
    that need help. And where it DOES fire, ``normalize_candidates`` already proposes
    the same canonical name, so only the trace LABEL changes.

    Making it useful needs a real design pass, not a wider matcher: a looser match
    (substring/RECORD-grounded over declared dists) mostly lands in AMBIGUOUS anyway,
    because the mechanical guessers frequently confirm too (a real ``jwt`` dist exists
    alongside ``PyJWT``). Letting a declared candidate WIN that tie was tried and
    reverted — see :func:`choose_provider` for why it is unsafe. The rung is kept as
    the plumbing that a correct design will need.
    """
    match = declared_metadata_match(import_name, declared_package_names)
    return [match] if match else []


def generate_candidates(
    import_name: str,
    *,
    declared_package_names: "set[str] | frozenset[str] | None" = None,
    llm: Callable[[str], list[str]] | None = None,
) -> list[Candidate]:
    """Ordered candidates: declared -> normalize -> curated -> (optional) llm.

    The ``declared`` rung runs FIRST — ABOVE the mechanical guessers — because
    it is EVIDENCE (a distribution the repo's own manifest already declares,
    in any group) rather than a name-transform or table-driven guess; when it
    and a guesser rung land on the same canonical distribution, the declared
    rung's label wins the canon-dedup below, so the trace records that the
    candidate came from the manifest, not a guess. It fires only when a caller
    passes ``declared_package_names`` (default ``None`` -> no declared
    candidates, so every existing caller keeps its old candidate set/order
    unchanged — this is an additive, backward-compatible rung).

    Then the deterministic guessers (``normalize`` then ``curated``); the
    ``llm`` rung runs ONLY when an ``llm`` callable is injected, and its
    guesses land last. Canon-deduped with the first (cheapest/most-trusted)
    source winning. ``llm`` defaults to ``None`` so the deterministic core
    never calls a model.

    Note: a declared candidate still must survive RECORD-grounding via
    ``choose_provider`` like every other candidate — it is never auto-accepted.
    Proposing a name the repo already wrote down is not the deleted
    import-name-as-dist-name identity fallback: it names one of the repo's OWN
    declared distributions, not the import's own spelling.
    """
    ordered: list[Candidate] = [
        Candidate(dist, DECLARED_SOURCE)
        for dist in declared_candidates(import_name, declared_package_names)
    ]
    ordered += [
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


# --------------------------------------------------------------------------- #
# P1.3 — RECORD grounding + provider selection (over an INJECTED provider)
# --------------------------------------------------------------------------- #
# The provider hands back the set of top-level module names a candidate dist's
# wheel RECORD/``top_level.txt`` ships, or ``None`` when there is no wheel to
# read. Injecting it keeps grounding pure and testable and keeps the
# network/PyPI detail (the spike's ``wheel_provides``) OUT of the decision core.
RecordProvider = Callable[[str], "set[str] | None"]


def record_grounds(
    candidate_dist: str,
    import_name: str,
    provider: RecordProvider,
) -> str:
    """3-way RECORD grounding for one candidate: ``confirm`` | ``deny`` | ``blind``.

    * ``confirm`` iff the provider's top-level set for ``candidate_dist`` contains
      the import's top-level module.
    * ``deny`` iff the provider returns a set that does NOT contain it — this
      prunes transitive-only shims (e.g. the ``bs4`` dummy dist whose RECORD
      lists something else) before they can be accepted.
    * ``blind`` iff the provider returns ``None`` (no wheel to read) — defer to
      P1.4's install backstop; never a decision on its own.

    Ports the spike's ``wheel_provides`` status mapping (wheel+provides ->
    confirm, wheel+!provides -> deny, no-wheel/too-big -> blind) and the
    ``Judged.record`` logic, but over the INJECTED provider — no network. The
    membership test is case-insensitive, matching the spike's lowercased
    ``top_level.txt`` comparison.
    """
    provided = provider(candidate_dist)
    if provided is None:
        return "blind"
    top = top_level_import_name(import_name).lower()
    if top in {module.lower() for module in provided}:
        return "confirm"
    return "deny"


@dataclass(frozen=True)
class RepairDecision:
    """Outcome of grounding + selecting a provider for one unsatisfied import.

    ``candidates_considered`` carries the ``blind`` distributions — those the
    injected provider could neither confirm nor deny — so the P1.4 fixpoint can
    hand exactly that set to its install backstop. It is empty whenever grounding
    was decisive (everything confirmed or denied), including on ``ACCEPT`` /
    ``AMBIGUOUS`` where no backstop is needed.
    """

    verdict: Verdict
    dist: str | None
    candidates_considered: tuple[str, ...]


def choose_provider(
    import_name: str,
    candidates: list[Candidate],
    provider: RecordProvider,
) -> RepairDecision:
    """Ground each candidate against the injected RECORD provider, then select.

    ``deny`` candidates (shims / hallucinations) are dropped. The surviving
    ``confirm`` set, deduped to CANON-DISTINCT distributions (by
    ``normalize_package_name`` — two spellings of one dist must never read as
    ambiguity), drives the verdict:

      * exactly one canonical confirm -> ``RepairDecision(ACCEPT, dist, ())``;
      * more than one -> ``RepairDecision(AMBIGUOUS, None, ())`` — never pick a
        variant (Global Constraint); two genuinely different confirming dists
        (e.g. ``attrs`` vs ``attr``) legitimately flag here;
      * zero confirms but >=1 ``blind`` -> ``RepairDecision(UNRESOLVED, None,
        <blind dists>)`` — blind is NEVER accepted on grounding alone; the blind
        set is surfaced for P1.4's install backstop to arbitrate;
      * nothing survives -> ``RepairDecision(UNRESOLVED, None, ())``.

    ``decide`` (P1.2) is deliberately NOT reused: it does not dedup its input, so
    counting ACCEPT/AMBIGUOUS goes through the canon-distinct sets built here.
    """
    confirmed: list[str] = []
    confirmed_canons: set[str] = set()
    blind: list[str] = []
    blind_canons: set[str] = set()
    for candidate in candidates:
        grounding = record_grounds(candidate.dist, import_name, provider)
        if grounding == "deny":
            continue
        canon = normalize_package_name(candidate.dist)
        if grounding == "confirm":
            if canon not in confirmed_canons:
                confirmed_canons.add(canon)
                confirmed.append(candidate.dist)
        elif canon not in blind_canons:  # blind
            blind_canons.add(canon)
            blind.append(candidate.dist)

    if len(confirmed) == 1:
        return RepairDecision(Verdict.ACCEPT, confirmed[0], ())
    # A DECLARED confirm does NOT break a variant tie. This was tried and reverted;
    # the reasoning is worth keeping so it is not re-attempted.
    #
    # It looks safe -- "the repo declared it, so it is evidence, not a guess" -- but a
    # missing import's provider is, by construction, one the root filter EXCLUDED
    # (anything in scope became a root, got installed, and would not be missing). So
    # the only declarations reachable here are the gated ones, and gated is exactly
    # where mutual exclusion lives:
    #
    #     [optional-dependencies]  cpu = ["foo"]   gpu = ["python-foo"]
    #
    # with a scanned ``import foo``, both wheels providing ``foo``. select_roots
    # rightly excludes BOTH. A declared tie-break would then accept ``foo`` and add it
    # as an audit root -- resurrecting one arm of a mutually-exclusive pair from an
    # extra the repo never activated. AMBIGUOUS is the correct answer here: two
    # variants confirm, and nothing in scope says which the project wants.
    if len(confirmed) > 1:
        return RepairDecision(Verdict.AMBIGUOUS, None, ())
    return RepairDecision(Verdict.UNRESOLVED, None, tuple(blind))
