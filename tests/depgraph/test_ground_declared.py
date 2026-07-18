# tests/depgraph/test_ground_declared.py
from graph.python.lanes.install.ground import (
    Candidate,
    declared_candidates,
    declared_coverage,
)
from graph.python.util.import_mapping import normalize_package_name


def _fake_provider(table):
    return lambda dist: table.get(dist.lower())


def test_declared_coverage_maps_module_to_dist():
    prov = _fake_provider({"fastapi": {"fastapi"}, "opencv-python": {"cv2"}})
    cov = declared_coverage(frozenset({"fastapi", "opencv-python"}), prov)
    assert cov["fastapi"] == ["fastapi"]
    assert cov["cv2"] == ["opencv-python"]


def test_declared_candidates_for_identity_import():
    cov = {"fastapi": ["fastapi"]}
    cands = declared_candidates("fastapi", cov)
    assert [(c.dist, c.source) for c in cands] == [("fastapi", "declared")]


def test_declared_candidates_empty_when_no_coverage():
    assert declared_candidates("fastapi", {}) == []


def test_declared_coverage_emits_canonical_dist_name():
    # A NON-canonical declared spelling must surface CANONICALIZED in coverage,
    # honoring the docstring's "module -> canonical dist names" contract. Pre-fix
    # the original spelling "Foo_Bar" leaked through here (this asserted "foo-bar"
    # would fail); post-fix the bucket holds the normalized name.
    assert normalize_package_name("Foo_Bar") == "foo-bar"  # canonical form, computed not guessed
    prov = _fake_provider({"foo_bar": {"foo_bar"}})
    cov = declared_coverage(frozenset({"Foo_Bar"}), prov)
    assert cov["foo_bar"] == ["foo-bar"]
    # ...and it flows through to declared_candidates as the canonical candidate.
    assert declared_candidates("foo_bar", cov) == [Candidate("foo-bar", "declared")]
