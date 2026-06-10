# tests/test_world_model_progress.py
from src.envstate.world_model import (
    initial_map, merge_map, _derive_progress, Fact, OpenProblem, _PROGRESS_LAYERS,
)


def _map(**kw):
    base = initial_map(
        base_image="python:3.12", workdir="/app", language="python",
        build_system="pip", repo_layout=(), )
    return merge_map(base, **kw)


def test_base_true_when_base_image_set():
    m = _map()
    p = _derive_progress(m.progress, m)
    assert p["base"] is True


def test_runtime_true_when_python_version_present():
    m = _map(env={"python_version": "Python 3.12.1"})
    assert _derive_progress(m.progress, m)["runtime"] is True


def test_deps_true_when_required_subset_of_installed():
    m = _map(required=(Fact("flask"),), installed=(Fact("flask", "3.0.0"),))
    assert _derive_progress(m.progress, m)["deps"] is True


def test_deps_false_when_required_not_satisfied():
    m = _map(required=(Fact("flask"),), installed=())
    assert _derive_progress(m.progress, m)["deps"] is False


def test_tests_true_when_done_flag():
    m = _map(done_flag=True)
    assert _derive_progress(m.progress, m)["tests"] is True


def test_system_false_when_unresolved_system_problem():
    m = _map(open_problems=(OpenProblem("libpq missing", "x", "system"),))
    assert _derive_progress(m.progress, m)["system"] is False


def test_progress_is_monotonic():
    prev = {layer: False for layer in _PROGRESS_LAYERS}
    prev["deps"] = True  # previously achieved
    m = _map(required=(Fact("flask"),), installed=())  # deps would compute False now
    assert _derive_progress(prev, m)["deps"] is True  # stays True
