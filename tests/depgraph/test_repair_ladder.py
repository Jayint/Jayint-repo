# tests/depgraph/test_repair_ladder.py
"""P1.2 — pure candidate ladder (generation + 3-way decide).

No network, no Executor, no graph: these exercise only the deterministic pipreqs
map lookup, the injected-LLM seam (fired only on a map miss), and the 3-way
``decide`` self-check.
"""
from python_deps.depgraph.repair import (
    Candidate,
    Verdict,
    decide,
    generate_candidates,
)


# --------------------------------------------------------------------------- #
# generate_candidates — pipreqs map first, injected LLM only on a miss
# --------------------------------------------------------------------------- #
def test_generate_pipreqs_hit():
    cands = generate_candidates("cv2")
    assert [(c.dist, c.source) for c in cands] == [("opencv-python", "pipreqs")]


def test_generate_llm_only_on_map_miss():
    seen = {}
    def llm(name, symbols):
        seen["called"] = (name, symbols)
        return ["some-dist"]
    # "cv2" is a pipreqs hit -> llm must NOT be called
    generate_candidates("cv2", symbols=("imread",), llm=llm)
    assert "called" not in seen
    # a miss -> llm IS called, fed the symbols
    cands = generate_candidates("zzz_unknown", symbols=("frobnicate",), llm=llm)
    assert seen["called"] == ("zzz_unknown", ("frobnicate",))
    assert [(c.dist, c.source) for c in cands] == [("some-dist", "llm")]


def test_generate_no_llm_no_identity_fallback():
    # miss + no llm -> empty; NEVER the import name itself
    assert generate_candidates("zzz_unknown", llm=None) == []


def test_generate_canon_dedupes():
    def llm(name, symbols):
        return ["Foo-Bar", "foo_bar"]  # same canonical dist, two spellings
    cands = generate_candidates("zzz_unknown", llm=llm)
    assert len(cands) == 1


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
