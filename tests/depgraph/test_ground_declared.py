# tests/depgraph/test_ground_declared.py
from graph.python.lanes.install.ground import declared_coverage, declared_candidates


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
