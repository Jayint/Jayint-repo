# tests/depgraph/test_repair_ladder.py
"""P1.2 — pure candidate ladder (generation + 3-way decide).

No network, no Executor, no graph: these exercise only the deterministic rungs
and the injected-LLM seam.
"""
from python_deps.depgraph.repair import (
    Candidate,
    Verdict,
    curated_candidates,
    decide,
    declared_candidates,
    generate_candidates,
    normalize_candidates,
)
from python_deps.import_mapping import normalize_package_name


# --------------------------------------------------------------------------- #
# normalize_candidates
# --------------------------------------------------------------------------- #
def test_normalize_candidates_contains_python_prefix():
    out = normalize_candidates("dateutil")
    assert "python-dateutil" in out


def test_normalize_candidates_canon_deduped():
    # No two survivors share a canonical form (first occurrence wins).
    out = normalize_candidates("dateutil")
    canons = [normalize_package_name(c) for c in out]
    assert len(canons) == len(set(canons))


def test_normalize_candidates_uses_top_level_only():
    out = normalize_candidates("dateutil.parser")
    assert "python-dateutil" in out
    assert all("parser" not in c for c in out)


# --------------------------------------------------------------------------- #
# curated_candidates  (demoted table — untrusted candidate source)
# --------------------------------------------------------------------------- #
def test_curated_candidates_yaml_real_value():
    # The real curated dist for the `yaml` import is PyYAML.
    assert curated_candidates("yaml") == ["PyYAML"]


def test_curated_candidates_requests_is_empty():
    # `requests` is not a curated remap -> no curated candidate.
    assert curated_candidates("requests") == []


# --------------------------------------------------------------------------- #
# generate_candidates
# --------------------------------------------------------------------------- #
def test_generate_candidates_normalize_before_curated():
    cands = generate_candidates("yaml")
    sources = [c.source for c in cands]
    dists = [c.dist for c in cands]
    assert "PyYAML" in dists
    first_curated = sources.index("curated")
    # everything before the first curated rung is a normalize rung
    assert all(s == "normalize" for s in sources[:first_curated])


def test_generate_candidates_llm_last_and_only_when_provided():
    cands = generate_candidates("yaml", llm=lambda n: ["extra"])
    # the llm guess lands last and is the only llm-sourced candidate
    assert cands[-1].dist == "extra"
    assert cands[-1].source == "llm"
    llm_positions = [i for i, c in enumerate(cands) if c.source == "llm"]
    assert llm_positions == [len(cands) - 1]


def test_generate_candidates_no_llm_source_by_default():
    # explicit None
    assert all(c.source != "llm" for c in generate_candidates("yaml", llm=None))
    # and the plain default (no kwarg) never calls a model
    assert all(c.source != "llm" for c in generate_candidates("yaml"))


def test_generate_candidates_returns_candidate_instances():
    cands = generate_candidates("yaml")
    assert cands and all(isinstance(c, Candidate) for c in cands)


# --------------------------------------------------------------------------- #
# decide — 3-way self-check
# --------------------------------------------------------------------------- #
def test_decide_unresolved_on_none():
    assert decide([]) == (Verdict.UNRESOLVED, "-")


def test_decide_accept_on_exactly_one():
    verdict, dist = decide(["a"])
    assert verdict == Verdict.ACCEPT
    assert dist == "a"


def test_decide_ambiguous_on_more_than_one():
    verdict, _ = decide(["a", "b"])
    assert verdict == Verdict.AMBIGUOUS


# --------------------------------------------------------------------------- #
# FIX 2 (B3) — declared_candidates: an evidence rung (not a guess). Reuses
# import_mapping.declared_metadata_match so a distribution the repo's OWN
# manifest already declares (in ANY group, including one select_roots
# filtered out of this resolve) is proposed instead of guessed.
# --------------------------------------------------------------------------- #
def test_declared_candidates_returns_match_when_declared():
    assert declared_candidates("freezegun", frozenset({"freezegun"})) == ["freezegun"]


def test_declared_candidates_empty_when_not_declared():
    assert declared_candidates("freezegun", frozenset({"other"})) == []


def test_declared_candidates_empty_by_default():
    # Backward compatible: no declared_package_names given -> no candidate,
    # not an error.
    assert declared_candidates("freezegun") == []


def test_declared_candidates_normalizes_separators():
    assert declared_candidates("django_filters", frozenset({"django-filters"})) == [
        "django-filters"
    ]


# --------------------------------------------------------------------------- #
# generate_candidates — declared rung wired in ABOVE the guessers
# --------------------------------------------------------------------------- #
def test_generate_candidates_declared_first_when_matched():
    cands = generate_candidates(
        "freezegun", declared_package_names=frozenset({"freezegun"})
    )
    assert cands[0].source == "declared_metadata"
    assert cands[0].dist == "freezegun"


def test_generate_candidates_declared_absent_by_default():
    # Backward compatibility: existing callers that never pass
    # declared_package_names must see EXACTLY the old candidate set/order.
    cands = generate_candidates("freezegun")
    assert all(c.source != "declared_metadata" for c in cands)


def test_generate_candidates_declared_wins_dedup_over_normalize():
    # Flask is both declared AND matched by normalize_candidates's identity
    # guess -- the declared (evidence) rung must win the canon-dedup, so the
    # surviving candidate is labeled declared_metadata, not normalize.
    cands = generate_candidates("Flask", declared_package_names=frozenset({"Flask"}))
    flask_cands = [c for c in cands if normalize_package_name(c.dist) == "flask"]
    assert len(flask_cands) == 1
    assert flask_cands[0].source == "declared_metadata"


def test_generate_candidates_declared_before_curated():
    # A declared match must sit strictly before the curated rung's hit.
    cands = generate_candidates("yaml", declared_package_names=frozenset({"yaml"}))
    sources = [c.source for c in cands]
    assert sources[0] == "declared_metadata"
    assert "curated" in sources
    assert sources.index("declared_metadata") < sources.index("curated")


def test_generate_candidates_declared_absent_when_no_manifest_match():
    # A declared_package_names set that does not contain this import's name
    # must not inject a spurious candidate; behavior degrades to the old
    # normalize -> curated ladder.
    cands = generate_candidates(
        "yaml", declared_package_names=frozenset({"some-other-dist"})
    )
    assert all(c.source != "declared_metadata" for c in cands)
