"""Task 4 — Executor: CommandResult, LocalSubprocessExecutor, FakeExecutor."""

from __future__ import annotations

import sys

from conftest import FakeExecutor, make_result

import graph.contracts.executor as executor_mod
from graph.contracts.executor import (
    CommandResult,
    DockerExecutor,
    Executor,
    LocalSubprocessExecutor,
)


def test_command_result_ok_property():
    assert CommandResult("c", 0, "", "").ok is True
    assert CommandResult("c", 3, "", "boom").ok is False


def test_local_executor_runs_real_command():
    ex = LocalSubprocessExecutor()
    res = ex.run(f'{sys.executable} -c "print(1)"')
    assert res.ok
    assert res.returncode == 0
    assert res.stdout.strip() == "1"


def test_local_executor_captures_nonzero_and_stderr():
    ex = LocalSubprocessExecutor()
    res = ex.run(
        f'{sys.executable} -c "import sys; sys.stderr.write(\'boom\'); sys.exit(3)"'
    )
    assert not res.ok
    assert res.returncode == 3
    assert "boom" in res.stderr


def test_local_executor_satisfies_protocol():
    assert isinstance(LocalSubprocessExecutor(), Executor)


def test_local_executor_timeout_returns_nonzero():
    ex = LocalSubprocessExecutor()
    res = ex.run(f'{sys.executable} -c "import time; time.sleep(5)"', timeout=1)
    assert not res.ok
    assert "timeout" in res.stderr.lower()


def test_fake_executor_substring_longest_match_wins():
    fake = FakeExecutor(
        responses={
            "pip": make_result(stdout="generic-pip"),
            "pip install numpy": make_result(stdout="numpy-specific"),
        }
    )
    res = fake.run("python -m pip install numpy==1.26.4")
    assert res.stdout == "numpy-specific"  # longest matching key wins


def test_fake_executor_default_fallback():
    fake = FakeExecutor(default=make_result(stdout="fallback", returncode=0))
    res = fake.run("anything at all")
    assert res.stdout == "fallback"


def test_fake_executor_no_match_returns_127():
    fake = FakeExecutor()
    res = fake.run("uv pip compile")
    assert res.returncode == 127
    assert res.stderr == "no fake response"


def test_fake_executor_records_calls():
    fake = FakeExecutor()
    fake.run("a")
    fake.run("b")
    assert fake.calls == ["a", "b"]


def test_fake_executor_satisfies_protocol():
    assert isinstance(FakeExecutor(), Executor)


# --- DockerExecutor --platform param (additive; default None = unchanged) ---


def test_docker_run_command_includes_platform_when_set():
    ex = DockerExecutor("python:3.11-slim-bookworm", platform="linux/amd64")
    cmd = ex._run_command()
    assert "--platform linux/amd64" in cmd


def test_docker_run_command_omits_platform_by_default():
    ex = DockerExecutor("python:3.11-slim-bookworm")
    cmd = ex._run_command()
    assert "--platform" not in cmd
    # Byte-identical to the historical (pre-platform) docker run command.
    assert cmd == (
        f"docker run -d --name {ex._name} python:3.11-slim-bookworm sleep infinity"
    )


def test_platform_flag_reaches_docker_run_argv(monkeypatch):
    captured: list[str] = []

    def fake_run(command: str, *, timeout: int) -> CommandResult:
        captured.append(command)
        return make_result(stdout="fakecontainerid\n", returncode=0)

    monkeypatch.setattr(executor_mod, "_run_subprocess", fake_run)
    with DockerExecutor("python:3.11-slim-bookworm", platform="linux/amd64"):
        pass
    run_argv = captured[0]  # first _run_subprocess call is the `docker run`
    assert "--platform" in run_argv
    assert "linux/amd64" in run_argv


def test_platform_flag_absent_from_docker_run_argv_when_none(monkeypatch):
    captured: list[str] = []

    def fake_run(command: str, *, timeout: int) -> CommandResult:
        captured.append(command)
        return make_result(stdout="fakecontainerid\n", returncode=0)

    monkeypatch.setattr(executor_mod, "_run_subprocess", fake_run)
    with DockerExecutor("python:3.11-slim-bookworm"):
        pass
    run_argv = captured[0]
    assert "--platform" not in run_argv


# --- DockerExecutor bootstrap_uv / cache_volumes params (additive; default
# False = byte-identical to the pre-existing docker run command) ---


def test_docker_run_command_includes_cache_volumes_when_set():
    ex = DockerExecutor("python:3.11-slim-bookworm", cache_volumes=True)
    cmd = ex._run_command()
    assert "-v jayint_uv_cache:/root/.cache/uv" in cmd
    assert "-v jayint_pip_cache:/root/.cache/pip" in cmd
    # Pin the FULL command so the mounts stay before --name/image/sleep (a
    # `docker run ... image -v ...` regression would break the mount silently).
    assert cmd == (
        "docker run -d "
        "-v jayint_uv_cache:/root/.cache/uv -v jayint_pip_cache:/root/.cache/pip "
        f"--name {ex._name} python:3.11-slim-bookworm sleep infinity"
    )


def test_docker_run_command_omits_cache_volumes_by_default():
    ex = DockerExecutor("python:3.11-slim-bookworm")
    cmd = ex._run_command()
    assert "jayint_uv_cache" not in cmd
    assert "jayint_pip_cache" not in cmd
    # Byte-identical to the historical (pre-cache-volumes) docker run command.
    assert cmd == (
        f"docker run -d --name {ex._name} python:3.11-slim-bookworm sleep infinity"
    )


def test_bootstrap_uv_and_cache_volumes_default_false():
    ex = DockerExecutor("python:3.11-slim-bookworm")
    assert ex.bootstrap_uv is False
    assert ex.cache_volumes is False


# --- DockerExecutor mount_repo / repo_mount_dir params (additive; default
# None = byte-identical to the pre-existing docker run command) ---


def test_run_command_without_mount_is_unchanged():
    ex = DockerExecutor("python:3.11-slim")
    cmd = ex._run_command()
    assert "sleep infinity" in cmd
    assert "/workspace/repo" not in cmd  # no repo mount when mount_repo is None
    # Byte-identical to the historical (pre-mount) docker run command.
    assert cmd == (
        f"docker run -d --name {ex._name} python:3.11-slim sleep infinity"
    )


def test_run_command_with_mount_binds_repo(tmp_path):
    ex = DockerExecutor("python:3.11-slim", mount_repo=str(tmp_path))
    cmd = ex._run_command()
    assert f"-v {tmp_path.resolve()}:/workspace/repo" in cmd
    assert ex.repo_mount_dir == "/workspace/repo"


def test_run_command_mount_is_the_only_difference(tmp_path):
    """Byte-identical proof: adding a mount is the ONLY textual delta.

    Pin the container names equal so the sole difference between the bare and
    the mounted command is the ``-v <abspath>:/workspace/repo `` fragment. If
    the fragment is stripped, the mounted command must equal the bare command
    character-for-character -- the behavior-preserving invariant for this task.
    """
    bare = DockerExecutor("python:3.11-slim")
    mounted = DockerExecutor("python:3.11-slim", mount_repo=str(tmp_path))
    mounted._name = bare._name  # neutralise the random per-instance container name
    fragment = f"-v {tmp_path.resolve()}:/workspace/repo "
    assert mounted._run_command().replace(fragment, "") == bare._run_command()


def test_empty_mount_repo_normalises_to_none():
    """A falsy mount_repo ("") coerces to None so the off-switch is unambiguous.

    Mounting Path("").resolve() (= cwd) on an empty string would be a footgun;
    normalising "" to None makes `if self.mount_repo` exactly equivalent to
    `if self.mount_repo is not None`.
    """
    assert DockerExecutor("python:3.11-slim", mount_repo="").mount_repo is None


def test_empty_mount_repo_run_command_identical_to_default():
    """`mount_repo=""` yields the SAME docker run command as the default (no mount).

    Pin the container names equal so the only possible difference would be a
    repo mount fragment; there must be none -- byte-identical to the off state.
    """
    empty = DockerExecutor("python:3.11-slim", mount_repo="")
    default = DockerExecutor("python:3.11-slim")
    empty._name = default._name  # neutralise the random per-instance container name
    cmd = empty._run_command()
    assert "/workspace/repo" not in cmd  # no repo mount when mount_repo is falsy
    assert cmd == default._run_command()
