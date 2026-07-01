"""Task 7 — ``TargetEnv``: one env describing the TARGET container, not the host.

``packaging.markers.Marker.evaluate()`` fills any key missing from the dict you
pass it from ``packaging.markers.default_environment()`` — which reflects the
HOST running the resolve, not the container being built. On a non-x86_64/
non-linux dev host this silently mis-evaluates ``sys_platform`` /
``platform_machine`` / ``os_name`` gated dependencies. ``TargetEnv.marker_env()``
must return every PEP 508 field so no key is ever left for the host default to
fill in.
"""

from __future__ import annotations

from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.target_env import TargetEnv, detect_target_env


def _target(**overrides) -> TargetEnv:
    base = dict(
        python_full="3.11.0",
        python_version="3.11",
        platform_machine="x86_64",
        sys_platform="linux",
        os_name="posix",
        platform_system="Linux",
        python_platform_tag="x86_64-manylinux_2_28",
    )
    base.update(overrides)
    return TargetEnv(**base)


def test_marker_env_has_all_platform_fields():
    t = _target()
    env = t.marker_env()
    assert env["platform_machine"] == "x86_64"
    assert env["sys_platform"] == "linux"
    assert env["os_name"] == "posix"


def test_marker_env_has_both_python_keys():
    t = _target(python_full="3.12.4", python_version="3.12")
    env = t.marker_env()
    assert env["python_version"] == "3.12"
    assert env["python_full_version"] == "3.12.4"


def test_marker_env_has_platform_system():
    t = _target(platform_system="Linux")
    assert t.marker_env()["platform_system"] == "Linux"


def test_target_env_is_frozen():
    t = _target()
    try:
        t.python_version = "3.9"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("TargetEnv must be frozen (immutable)")


class _FakeExecutor:
    """Minimal Executor returning canned results keyed by command substring."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls: list[str] = []

    def run(self, command, *, timeout=300):
        self.calls.append(command)
        for key, (rc, out, err) in self.mapping.items():
            if key in command:
                return CommandResult(command=command, returncode=rc, stdout=out, stderr=err)
        return CommandResult(command=command, returncode=127, stdout="", stderr="not found")


def test_detect_target_env_parses_probe_output():
    ex = _FakeExecutor(
        {
            "import platform,sys,os": (
                0,
                "3.11.4 posix linux x86_64 Linux\n",
                "",
            ),
            "ldd --version": (0, "ldd (GNU libc) 2.36\n", ""),
        }
    )
    t = detect_target_env(ex)
    assert t.python_full == "3.11.4"
    assert t.python_version == "3.11"
    assert t.os_name == "posix"
    assert t.sys_platform == "linux"
    assert t.platform_machine == "x86_64"
    assert t.platform_system == "Linux"
    assert t.python_platform_tag == "x86_64-manylinux_2_28"


def test_detect_target_env_arm_musl_maps_to_musllinux():
    ex = _FakeExecutor(
        {
            "import platform,sys,os": (
                0,
                "3.12.1 posix linux aarch64 Linux\n",
                "",
            ),
            "ldd --version": (1, "", "musl libc (aarch64)\nVersion 1.2.3\n"),
        }
    )
    t = detect_target_env(ex)
    assert t.platform_machine == "aarch64"
    assert t.python_platform_tag == "aarch64-musllinux_1_2"


def test_detect_target_env_degrades_to_defaults_on_probe_failure():
    ex = _FakeExecutor({})  # every command 127s.
    t = detect_target_env(ex)
    # Never crashes; sensible glibc/linux/x86_64 defaults.
    assert t.sys_platform == "linux"
    assert t.os_name == "posix"
    assert t.python_platform_tag.endswith("manylinux_2_28")


def test_detect_target_env_degrades_on_malformed_probe_output():
    ex = _FakeExecutor({"import platform,sys,os": (0, "garbage\n", "")})
    t = detect_target_env(ex)
    assert t.sys_platform == "linux"
    assert t.python_platform_tag.endswith("manylinux_2_28")


def test_detect_target_env_never_crashes_on_executor_exception():
    class _RaisingExecutor:
        def run(self, command, *, timeout=300):
            raise RuntimeError("container is gone")

    t = detect_target_env(_RaisingExecutor())
    assert t.sys_platform == "linux"
    assert t.os_name == "posix"


# ── python3 -> python fallback (review P2, bug 1) ────────────────────────────
#
# Some target images (e.g. slim bases) ship a working `python` but no
# `python3`. The probe must try `python3` first and fall back to `python`,
# not silently default the whole TargetEnv (wrong interpreter/platform).


def test_probe_command_falls_back_to_python():
    """The command detection sends to the executor tries `python3` first and
    falls back to `python` in the same shell round trip (mirrors the existing
    ``--python-platform`` string-presence style test)."""
    ex = _FakeExecutor(
        {
            "import platform,sys,os": (
                0,
                "3.11.4 posix linux x86_64 Linux\n",
                "",
            ),
            "ldd --version": (0, "ldd (GNU libc) 2.36\n", ""),
        }
    )
    detect_target_env(ex)
    assert ex.calls, "detect_target_env never invoked the executor"
    probe_cmd = ex.calls[0]
    assert probe_cmd.startswith("python3 -c")
    assert " || python -c" in probe_cmd


def test_probe_falls_back_to_python_when_python3_absent(monkeypatch, tmp_path):
    """Real shell-level fallback: PATH has no ``python3`` at all, only a
    ``python`` shim. If the ``||`` fallback did not fire, ``detect_target_env``
    would silently return the hardcoded default TargetEnv instead of these
    (deliberately distinctive) probed facts."""
    from python_deps.depgraph.executor import LocalSubprocessExecutor

    shim = tmp_path / "python"
    shim.write_text("#!/bin/sh\necho '9.9.9 posix testplatform arm64 TestSystem'\n")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    t = detect_target_env(LocalSubprocessExecutor())
    assert t.python_full == "9.9.9"
    assert t.sys_platform == "testplatform"
    assert t.platform_system == "TestSystem"
    # Alias normalization applies on the fallback path too.
    assert t.platform_machine == "aarch64"


# ── machine-alias normalization (review P2, bug 2) ───────────────────────────
#
# The target is always a Linux container: `arm64`/`amd64` are non-canonical
# aliases some images report instead of `aarch64`/`x86_64` — the values wheel
# tags and PEP 508 `platform_machine` markers actually expect.


def test_machine_alias_arm64_normalized():
    ex = _FakeExecutor(
        {
            "import platform,sys,os": (
                0,
                "3.12.1 posix linux arm64 Linux\n",
                "",
            ),
            "ldd --version": (0, "ldd (GNU libc) 2.36\n", ""),
        }
    )
    t = detect_target_env(ex)
    assert t.platform_machine == "aarch64"
    assert t.python_platform_tag.startswith("aarch64-")


def test_machine_alias_amd64_normalized():
    ex = _FakeExecutor(
        {
            "import platform,sys,os": (
                0,
                "3.12.1 posix linux amd64 Linux\n",
                "",
            ),
            "ldd --version": (0, "ldd (GNU libc) 2.36\n", ""),
        }
    )
    t = detect_target_env(ex)
    assert t.platform_machine == "x86_64"
    assert t.python_platform_tag.startswith("x86_64-")
