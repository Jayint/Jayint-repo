"""The 'loop closed' assertion: the selected+pinned image reaches all three
consumers, and target_python == the pinned minor. No Docker, no network."""
import types
import scripts.run_v3_e2e as e2e
from src.envstate.base_image_selection import BaseImageChoice


def test_selected_image_and_minor_thread_to_all_consumers(monkeypatch, tmp_path):
    seen = {}

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    # Facade returns a known pinned choice with a non-None platform override,
    # so the assertion below locks the platform thread through Sandbox(...).
    choice = BaseImageChoice("python:3.10-slim", "3.10", "linux/amd64", "auto: test")
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
    assert seen["sandbox_platform"] == "linux/amd64"     # platform thread -> Sandbox(platform=...)


# ── _target_arch: the {dpkg, uname} dict is the sole guarantor that the arch
# fed to translate_sanitize.apply_arch is never None (apply_arch does
# arch["dpkg"] and would KeyError/misbehave on None or a malformed dict). ──

def test_target_arch_explicit_amd64():
    assert e2e._target_arch("linux/amd64") == {"dpkg": "amd64", "uname": "x86_64"}


def test_target_arch_explicit_arm64():
    assert e2e._target_arch("linux/arm64") == {"dpkg": "arm64", "uname": "aarch64"}


def test_target_arch_none_falls_back_to_host_arm64(monkeypatch):
    monkeypatch.setattr(e2e._platform, "machine", lambda: "aarch64")
    assert e2e._target_arch(None) == {"dpkg": "arm64", "uname": "aarch64"}


def test_target_arch_none_falls_back_to_host_amd64(monkeypatch):
    monkeypatch.setattr(e2e._platform, "machine", lambda: "x86_64")
    assert e2e._target_arch(None) == {"dpkg": "amd64", "uname": "x86_64"}


def test_target_arch_never_none_shape_invariant(monkeypatch):
    monkeypatch.setattr(e2e._platform, "machine", lambda: "x86_64")
    for platform_override in (None, "", "linux/amd64", "linux/arm64"):
        result = e2e._target_arch(platform_override)
        assert set(result.keys()) == {"dpkg", "uname"}
        assert isinstance(result["dpkg"], str) and result["dpkg"]
        assert isinstance(result["uname"], str) and result["uname"]
