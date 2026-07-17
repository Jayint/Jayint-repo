"""Task 7 — ``build_dep_graph`` builds ONE ``TargetEnv`` (via
``detect_target_env``) instead of two separate ``_detect_target_python`` /
``_detect_target_platform`` probes, and threads it (as the ``python_platform_tag``)
into the ``uv lock --python-platform`` flag so the HOST never leaks into the
resolve. ``target_python`` / ``target_platform`` remain accepted as caller
overrides that patch the detected env (tests / callers that already know the
target skip trusting the probe for that field)."""

from __future__ import annotations

from conftest import FakeExecutor, make_result

from python_deps.depgraph.build import build_dep_graph


def _make_repo(tmp_path):
    (tmp_path / "app.py").write_text("import numpy\n")
    return str(tmp_path)


def _lock_calls(ex):
    return [c for c in ex.calls if "uv lock" in c]


def test_build_lock_command_carries_detected_platform_tag(tmp_path):
    # No probe response canned -> detect_target_env degrades to the default
    # (x86_64-manylinux_2_28); the FIRST `uv lock` attempt must still carry it.
    ex = FakeExecutor(default=make_result(returncode=127))
    build_dep_graph(_make_repo(tmp_path), ex, host_executor=ex)

    lock_calls = _lock_calls(ex)
    assert lock_calls, "build_dep_graph must attempt `uv lock`"
    assert "--python-platform x86_64-manylinux_2_28" in lock_calls[0]


def test_build_lock_command_reflects_detected_arm_musl_target(tmp_path):
    ex = FakeExecutor(
        responses={
            "import platform,sys,os": make_result(
                stdout="3.12.1 posix linux aarch64 Linux\n"
            ),
            "ldd --version": make_result(
                returncode=1, stderr="musl libc (aarch64)\n"
            ),
        },
        default=make_result(returncode=127),
    )
    build_dep_graph(_make_repo(tmp_path), ex, host_executor=ex)

    lock_calls = _lock_calls(ex)
    assert lock_calls
    assert "--python 3.12" in lock_calls[0]
    assert "--python-platform aarch64-musllinux_1_2" in lock_calls[0]


def test_build_target_python_override_wins_over_detected_probe(tmp_path):
    # The probe reports 3.9; an explicit target_python must still win.
    ex = FakeExecutor(
        responses={
            "import platform,sys,os": make_result(
                stdout="3.9.0 posix linux x86_64 Linux\n"
            ),
        },
        default=make_result(returncode=127),
    )
    build_dep_graph(
        _make_repo(tmp_path), ex, host_executor=ex, target_python="3.13"
    )

    lock_calls = _lock_calls(ex)
    assert lock_calls
    assert "--python 3.13" in lock_calls[0]


def test_build_target_platform_override_wins_over_detected_probe(tmp_path):
    # The probe (silently) reports x86_64; an explicit target_platform must win.
    ex = FakeExecutor(default=make_result(returncode=127))
    build_dep_graph(
        _make_repo(tmp_path),
        ex,
        host_executor=ex,
        target_platform="aarch64-manylinux_2_28",
    )

    lock_calls = _lock_calls(ex)
    assert lock_calls
    assert "--python-platform aarch64-manylinux_2_28" in lock_calls[0]
