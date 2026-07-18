from graph.python.lanes.install.ground import pipreqs_candidates


def test_known_mismatch_maps():
    assert pipreqs_candidates("cv2") == ["opencv-python"]
    assert pipreqs_candidates("yaml") == ["pyyaml"]


def test_miss_returns_empty():
    assert pipreqs_candidates("definitely_not_a_real_import_zzz") == []


def test_never_returns_identity():
    # A miss must NOT echo the import name back (no identity fallback).
    assert pipreqs_candidates("definitely_not_a_real_import_zzz") == []


def test_dotted_import_uses_top_level():
    # cv2.something -> looked up by top-level "cv2"
    assert pipreqs_candidates("cv2.aruco") == ["opencv-python"]
