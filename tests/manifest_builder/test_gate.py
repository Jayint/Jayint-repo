import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.types import CollectionResult
from src.manifest_builder.gate import accept, pick_best


def _clean(ids, exit_code=0):
    return CollectionResult(exit_code=exit_code, collected=tuple(ids))


def test_clean_stable_pristine_accepts():
    r = _clean(["t.py::a", "t.py::b"])
    v = accept(r, r, protected_ok=True)
    assert v.accepted and v.manifest == ("t.py::a", "t.py::b") and v.reasons == ()


def test_partial_collection_nonzero_exit_rejects():
    r = CollectionResult(exit_code=2, collected=("t.py::a",), collect_errors=("ImportError",))
    v = accept(r, r, protected_ok=True)
    assert not v.accepted and any("exit 2" in x for x in v.reasons) and v.manifest is None


def test_protected_modified_rejects():
    r = _clean(["t.py::a"])
    v = accept(r, r, protected_ok=False)
    assert not v.accepted and "protected files modified" in v.reasons


def test_unstable_nodeids_rejects():
    v = accept(_clean(["t.py::a", "t.py::b"]), _clean(["t.py::a"]), protected_ok=True)
    assert not v.accepted and any("unstable" in x for x in v.reasons)


def test_hollow_zero_collected_rejects():
    r = _clean([])
    v = accept(r, r, protected_ok=True)
    assert not v.accepted and any("hollow" in x for x in v.reasons)


def test_accepts_despite_author_skips_and_deselects():
    r = CollectionResult(exit_code=0, collected=("t.py::a",),
                         skipped_modules=("tests/test_opt.py",), deselected=("t.py::slow",))
    v = accept(r, r, protected_ok=True)
    assert v.accepted and v.manifest == ("t.py::a",)


def test_pick_best_returns_highest_count_accepted():
    small = accept(_clean(["a"]), _clean(["a"]), True)
    big = accept(_clean(["a", "b", "c"]), _clean(["a", "b", "c"]), True)
    bad = accept(_clean([], exit_code=2), _clean([], exit_code=2), True)
    assert pick_best([small, big, bad]) is big


def test_pick_best_none_when_all_rejected():
    bad = accept(_clean([], exit_code=2), _clean([], exit_code=2), True)
    assert pick_best([bad]) is None
