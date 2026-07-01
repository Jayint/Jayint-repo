"""The 'loop closed' assertion: the selected+pinned image reaches all three
consumers, and target_python == the pinned minor. No Docker, no network."""
import types
import pytest
import scripts.run_v3_e2e as e2e
from src.envstate.base_image_selection import BaseImageChoice


def test_selected_image_and_minor_thread_to_all_consumers(monkeypatch, tmp_path):
    seen = {}

    monkeypatch.setattr(e2e, "_load_dotenv", lambda *a, **k: None, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    # Facade returns a known pinned choice.
    choice = BaseImageChoice("python:3.10-slim", "3.10", None, "auto: test")
    monkeypatch.setattr(e2e, "choose_base_image", lambda *a, **k: choice, raising=False)

    def _fake_advisory(repo, image, *, host_executor=None, target_python=None, classify=None):
        seen["advisory_image"] = image
        seen["advisory_target_python"] = target_python
        return "", None
    monkeypatch.setattr(e2e, "build_advisory_for_repo", _fake_advisory, raising=False)

    def _fake_initial_map(*, base_image, **k):
        seen["map_image"] = base_image
        return types.SimpleNamespace(dep_graph=None)
    monkeypatch.setattr(e2e, "initial_map", _fake_initial_map, raising=False)

    class _FakeSandbox:
        def __init__(self, *, base_image, workdir="/app", platform=None, seed_dir=None, **k):
            seen["sandbox_image"] = base_image
            seen["sandbox_platform"] = platform
            self.container = None
        def execute(self, *a, **k): return None
        def exec_readonly(self, *a, **k): return None
        def reset_to_base(self): return None
        def run_install_script(self, *a, **k): return None
    monkeypatch.setattr(e2e, "Sandbox", _FakeSandbox, raising=False)

    monkeypatch.setattr(e2e, "run_v3",
                        lambda *a, **k: (types.SimpleNamespace(dep_graph=None), "done"),
                        raising=False)

    rc = e2e.main_with_args([str(tmp_path)])  # see Step 3 for the seam

    assert seen["advisory_image"] == "python:3.10-slim"
    assert seen["advisory_target_python"] == "3.10"      # loop closed: minor -> graph
    assert seen["map_image"] == "python:3.10-slim"
    assert seen["sandbox_image"] == "python:3.10-slim"
