"""Under-declaration grounding: repair the graph from RECORD/import evidence (spec §4B install/ground).

Folded (3c-2) from repair + pipreqs_map + coverage: the RecordProvider ladder that
grounds undeclared imports to installed distributions, the pipreqs candidate map, and
the coverage probe that reads a container's installed packages_distributions. Pure
except the injected Executor probe.
"""
from __future__ import annotations

import enum
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from graph.contracts.executor import Executor
from graph.model import Node, NodeType, State
from graph.python.lanes.install.link import PACKAGES_DIST_CMD, parse_packages_distributions
from graph.python.util.import_mapping import normalize_package_name, top_level_import_name


# === repair.py: the RecordProvider ladder that grounds undeclared imports ===
# The install-lane guesser seam: an INJECTED callable fed ``(import_name, symbols)``
# that returns candidate distribution names on a pipreqs map MISS. Defaulting to
# ``None`` keeps python_deps model-free — no model call originates here.
DistGuesser = Callable[[str, tuple[str, ...]], list[str]]


@dataclass(frozen=True)
class Candidate:
    """A proposed distribution name and the rung that produced it."""

    dist: str
    source: str  # "pipreqs" | "llm" | "declared"


class Verdict(enum.Enum):
    """Outcome of grounding the candidate ladder for one import."""

    ACCEPT = "ACCEPT"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


def generate_candidates(
    import_name: str,
    *,
    symbols: tuple[str, ...] = (),
    llm: DistGuesser | None = None,
) -> list[Candidate]:
    """Ordered candidates for an unresolved import: pipreqs map, else LLM.

    Deterministic step 1 is the vendored pipreqs table; on a *miss* only, an
    injected ``llm`` guesser fed ``(import_name, symbols)`` proposes. No other
    source, and NEVER the import name itself (no identity fallback). Every
    candidate is still RECORD-grounded by ``choose_provider`` — this function
    only proposes. Canon-deduped, first spelling wins.
    """
    top = top_level_import_name(import_name)
    hits = pipreqs_candidates(top)
    if hits:
        raw = [Candidate(dist, "pipreqs") for dist in hits]
    elif llm is not None:
        raw = [Candidate(dist, "llm") for dist in llm(top, symbols)]
    else:
        raw = []

    seen: set[str] = set()
    out: list[Candidate] = []
    for candidate in raw:
        key = normalize_package_name(candidate.dist.strip())
        if key and key not in seen:
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


# === pipreqs_map.py: pipreqs candidate import->package map ===
# Package-data lives at the graph package root (graph/data/), not beside this
# module — anchor to the "graph" ancestor so the path is robust to the module's
# depth within the package.
_GRAPH_ROOT = next(p for p in Path(__file__).parents if p.name == "graph")
_MAPPING_PATH = _GRAPH_ROOT / "data" / "pipreqs_mapping.txt"


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    table: dict[str, str] = {}
    text = _MAPPING_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        imp, dist = line.split(":", 1)
        # Key stays exact-case (real import spelling: Crypto, PIL, ...); the dist
        # VALUE is normalized to PEP 503 canonical lowercase so downstream RECORD
        # grounding gets a stable, case-insensitive name (e.g. PyYAML -> pyyaml).
        imp, dist = imp.strip(), dist.strip().lower()
        if imp and dist:
            table.setdefault(imp, dist)  # first wins (deterministic)
    return table


def pipreqs_candidates(import_name: str) -> list[str]:
    """``[distribution]`` for a known top-level import name, else ``[]``.

    Exact-case match on the top-level segment (pipreqs keys are real import
    spellings, e.g. ``Crypto``/``PIL``). Never echoes the import name back.
    """
    top = top_level_import_name(import_name)
    hit = _load().get(top)
    return [hit] if hit else []


# === coverage.py: read a container's installed packages_distributions (RECORD coverage probe) ===
def resolved_record_coverage(
    pkg_nodes: list[Node], record_provider: RecordProvider
) -> set[str]:
    """The lowercased UNION of top-level module names the RESOLVED packages ship.

    Iterates the ``Package`` nodes (skipping resolver diagnostic ``MISSING``
    placeholders, which have no wheel to read), asks the injected
    ``record_provider`` for each dist's top-level modules, and unions everything
    non-``None`` (a ``None`` return — no wheel / sdist-only / unknown — contributes
    nothing and falls through to the install/import backstop). Pure: no Executor,
    no network; the provider is the sole source of truth.
    """
    provided: set[str] = set()
    for node in pkg_nodes:
        if node.type is not NodeType.PACKAGE or node.state is State.MISSING:
            continue
        modules = record_provider(node.name)
        if modules:
            provided |= {module.lower() for module in modules}
    return provided


def declared_coverage(
    declared_dists: frozenset[str], provider: RecordProvider
) -> dict[str, list[str]]:
    """Map lowercased top-level module -> canonical dist names, over DECLARED dists.

    Reads each declared dist's wheel RECORD once via ``provider`` (a ``None`` return
    contributes nothing — no wheel / sdist-only). Mirrors ``resolved_record_coverage``
    but keyed module->dist so a missing import can look up which declared dist ships it.
    """
    coverage: dict[str, list[str]] = {}
    for dist in sorted(declared_dists):
        modules = provider(dist)
        if not modules:
            continue
        for module in modules:
            bucket = coverage.setdefault(module.lower(), [])
            canon = normalize_package_name(dist)
            if canon not in {normalize_package_name(d) for d in bucket}:
                bucket.append(dist)
    return coverage


def declared_candidates(import_name: str, coverage: dict[str, list[str]]) -> list[Candidate]:
    """Declared dists whose RECORD covers ``import_name`` — the highest-trust rung."""
    top = top_level_import_name(import_name).lower()
    return [Candidate(dist, "declared") for dist in coverage.get(top, [])]


def default_record_provider(container_executor: Executor) -> RecordProvider:
    """Production ``RecordProvider`` — INTERIM post-install container dist-info.

    Builds ``dist -> {top-level modules}`` by inverting the container's
    ``importlib.metadata.packages_distributions()`` (run once, memoized, no
    network). A dist absent from the installed environment returns ``None``.

    KNOWN LIMITATION (why it is not used ALONE): this reads POST-INSTALL state, so
    (a) a resolved-but-failed-to-build dist reports ``None`` here rather than its
    RECORD modules, and (b) a not-yet-installed repair CANDIDATE also reports
    ``None`` — meaning ``choose_provider`` can never ``confirm`` a candidate from
    THIS provider alone, so repair driven by it in isolation is inert. P1.5 closes
    that gap: :func:`composite_record_provider` layers this cheap installed reader
    UNDER :func:`pypi_record_provider` (the PRE-install PyPI wheel read), and the
    composite — not this provider bare — is the production default in ``build.py``.
    This provider stays the fast path for already-installed closure members (no
    PyPI call); the composite falls through to the pre-install reader only when it
    is blind (candidates, failed builds).
    """
    cache: dict[str, set[str]] = {}
    built = {"done": False}

    def provider(dist: str) -> "set[str] | None":
        if not built["done"]:
            built["done"] = True
            result = container_executor.run(PACKAGES_DIST_CMD)
            if result.ok:
                for module, dists in parse_packages_distributions(result.stdout).items():
                    for owner in dists:
                        cache.setdefault(normalize_package_name(owner), set()).add(module)
        return cache.get(normalize_package_name(dist))

    return provider


# --------------------------------------------------------------------------- #
# P1.5 — PRE-install PyPI wheel-metadata provider (makes production repair work)
# --------------------------------------------------------------------------- #
# The single network path in this module: read a candidate dist's wheel top-level
# modules straight from PyPI, BEFORE anything is installed, so ``choose_provider``
# can confirm a not-yet-installed repair candidate. Isolated behind an INJECTED
# ``fetch`` seam (exactly like ``pins._default_fetch``) so unit tests never reach
# the socket — they pass a fake ``fetch``. Ports the validated spike chain in
# ``src/eval/graph_fidelity/underdeclaration_repair_poc.py``.
_PYPI_JSON = "https://pypi.org/pypi/{dist}/json"
_HTTP_TIMEOUT = 20
_MAX_WHEEL_BYTES = 20 * 1024 * 1024
_UA = {"User-Agent": "depgraph-record-provider/0.1 (+local)"}


def _http_json(url: str) -> "dict | None":
    """GET ``url`` and parse JSON, or ``None`` on any failure (best-effort)."""
    try:
        with urllib.request.urlopen(  # noqa: S310 — fixed https PyPI host
            urllib.request.Request(url, headers=_UA), timeout=_HTTP_TIMEOUT
        ) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — absence/error -> None, never raise
        return None


def _find_wheel(data: dict) -> "tuple[str | None, int]":
    """Pick a ``bdist_wheel`` ``(url, size)`` from a PyPI JSON payload.

    Prefers the latest release's ``urls``; falls back to scanning ``releases``
    newest-first. Returns ``(None, 0)`` when the dist ships no wheel (sdist-only).
    """
    for f in data.get("urls", []):
        if f.get("packagetype") == "bdist_wheel":
            return f.get("url"), f.get("size") or 0
    for _ver, files in reversed(list(data.get("releases", {}).items())):
        for f in files:
            if f.get("packagetype") == "bdist_wheel":
                return f.get("url"), f.get("size") or 0
    return None, 0


def _wheel_top_levels(path: str) -> set[str]:
    """Lowercased top-level module names a downloaded wheel ships.

    Reads ``*.dist-info/top_level.txt`` when present, else infers from the first
    path segment of each real (non-metadata) member (RECORD inference), matching
    the spike's ``_wheel_top_levels``.
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        top_level = [n for n in names if n.endswith(".dist-info/top_level.txt")]
        if top_level:
            return {
                token.strip().lower()
                for token in archive.read(top_level[0]).decode("utf-8", "replace").split()
                if token.strip()
            }
        tops: set[str] = set()
        for name in names:  # RECORD-inference: first path segment of real files
            if ".dist-info/" in name or ".data/" in name:
                continue
            segment = name.split("/")[0].split(".")[0]  # strip .py/.cpython-*.so
            if segment and not segment.startswith("__"):
                tops.add(segment.lower())
        return tops


def _default_wheel_top_levels(dist: str) -> "set[str] | None":
    """Real PyPI wheel read — the ONLY network code here; tests inject a fake.

    ``/{dist}/json`` -> pick a ``bdist_wheel`` -> download (skipping wheels over
    ``_MAX_WHEEL_BYTES``) -> return :func:`_wheel_top_levels`. ``None`` on any
    absence/failure (not on PyPI, sdist-only, too big, network error), which the
    grounding layer reads as ``blind``.
    """
    data = _http_json(_PYPI_JSON.format(dist=dist))
    if data is None:
        return None
    url, size = _find_wheel(data)
    if not url:
        return None
    if size and size > _MAX_WHEEL_BYTES:
        return None
    tmp = tempfile.mkdtemp(prefix="rec-whl-")
    try:
        path = os.path.join(tmp, "w.whl")
        with urllib.request.urlopen(  # noqa: S310 — url is a PyPI-hosted wheel
            urllib.request.Request(url, headers=_UA), timeout=_HTTP_TIMEOUT
        ) as response, open(path, "wb") as handle:
            shutil.copyfileobj(response, handle)
        return _wheel_top_levels(path)
    except Exception:  # noqa: BLE001 — failure -> None (blind), never raise
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pypi_record_provider(*, fetch=_default_wheel_top_levels) -> RecordProvider:
    """PRE-install ``RecordProvider`` reading a candidate dist's wheel top-levels.

    Given a dist NAME, the injected ``fetch`` returns the lowercased set of
    top-level modules its (latest compatible) wheel ships, or ``None`` when
    unavailable (not on PyPI / no wheel / sdist-only). Keyed by NAME only, by
    design: a repair CANDIDATE has no resolved version yet, and a dist's top-level
    module names are stable across versions — so ``fetch`` queries ``/{name}/json``
    (latest). This is the piece the post-install :func:`default_record_provider`
    cannot supply, so ``choose_provider`` can now ``confirm`` a not-yet-installed
    candidate. ``fetch`` is the INJECTED network seam (mirrors
    ``pins._default_fetch``); unit tests pass a fake, so no test reaches PyPI.
    Cached per dist (canon-keyed): each dist's ``fetch`` runs at most once and a
    ``None`` answer is cached too (a blind dist is never re-queried).
    """
    cache: dict[str, "set[str] | None"] = {}

    def provider(dist: str) -> "set[str] | None":
        key = normalize_package_name(dist)
        if key not in cache:
            cache[key] = fetch(dist)
        return cache[key]

    return provider


def composite_record_provider(
    installed_provider: RecordProvider, candidate_provider: RecordProvider
) -> RecordProvider:
    """Cheap post-install coverage first; PyPI candidate read only when blind.

    For a dist, consult ``installed_provider`` (the memoized post-install
    container dist-info — free, network-less). If it returns a non-``None`` set,
    use it and NEVER call ``candidate_provider``. Only when the installed provider
    is blind (``None`` — a not-yet-installed repair candidate, or a
    resolved-but-failed-to-build dist) does it fall through to
    ``candidate_provider`` (the PRE-install PyPI wheel read). So candidates and
    failed builds get a real pre-install answer while already-installed closure
    members stay network-free (the short-circuit). Cached per dist (canon-keyed)
    so a dist is neither re-probed nor re-fetched.
    """
    cache: dict[str, "set[str] | None"] = {}

    def provider(dist: str) -> "set[str] | None":
        key = normalize_package_name(dist)
        if key in cache:
            return cache[key]
        provided = installed_provider(dist)
        if provided is None:
            provided = candidate_provider(dist)
        cache[key] = provided
        return provided

    return provider
