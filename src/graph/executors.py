"""Concrete Executor implementations — host subprocess + Docker container.

Split out of ``graph/contracts/executor.py`` (Phase 2 T6) so the seam file stays a
pure protocol. These remain graph-internal: ``graph.core.build`` and ``graph.advise``
construct them (``LocalSubprocessExecutor`` as the host-probe default;
``DockerExecutor`` for the scratch-container advisory). ``LocalSubprocessExecutor``
uses ``subprocess`` (no Docker), so graph stays Docker-free.

Physically relocating the Docker machinery to the run stage (``orchestrate/loop/``)
is deferred to Phase 3: it requires an inject-refactor of
``build_dep_graph``/``build_advisory_for_repo`` so graph stops *constructing* the
executor — a logic change gated on the pass-repo Docker sweep, not a pure relocation.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from graph.contracts.executor import CommandResult, TIMEOUT_RC


def _run_subprocess(command: str, *, timeout: int) -> CommandResult:
    """Run ``command`` through the shell, capturing rc/stdout/stderr."""
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return CommandResult(
            command=command,
            returncode=TIMEOUT_RC,
            stdout=stdout,
            stderr=stderr + f"\n[timeout after {timeout}s]",
        )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


class LocalSubprocessExecutor:
    """Executor that runs commands directly on the host/in the current venv."""

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        return _run_subprocess(command, timeout=timeout)


class DockerExecutor:
    """Long-lived container executor used as a context manager.

    ``with DockerExecutor("python:3.11-slim") as ex:`` starts a detached
    ``sleep infinity`` container; ``ex.run(cmd)`` does ``docker exec`` into it;
    ``__exit__`` force-removes the container.  Not unit-tested (covered by the
    gated Docker integration test); kept dependency-free so import never fails
    when Docker is absent.
    """

    def __init__(
        self,
        image: str,
        *,
        network: bool = True,
        platform: str | None = None,
        bootstrap_uv: bool = False,
        cache_volumes: bool = False,
        mount_repo: str | None = None,
        repo_mount_dir: str = "/workspace/repo",
    ) -> None:
        self.image = image
        self.network = network
        self.platform = platform
        self.bootstrap_uv = bootstrap_uv
        self.cache_volumes = cache_volumes
        # Coerce a falsy mount_repo ("" and None alike) to None so the off-switch
        # is unambiguous: `if self.mount_repo` in _run_command is then exactly
        # equivalent to `if self.mount_repo is not None`. Mounting Path("") (= cwd)
        # on an empty string would be a footgun, so empty means "no mount".
        self.mount_repo = mount_repo or None
        self.repo_mount_dir = repo_mount_dir
        self.container_id: str | None = None
        self._name = f"depgraph-probe-{uuid.uuid4().hex[:12]}"

    def _run_command(self) -> str:
        """The ``docker run`` command that starts the probe container (pure/testable)."""
        net = "" if self.network else "--network none "
        plat = f"--platform {self.platform} " if self.platform else ""
        vols = (
            "-v jayint_uv_cache:/root/.cache/uv -v jayint_pip_cache:/root/.cache/pip "
            if self.cache_volumes
            else ""
        )
        repo = (
            f"-v {Path(self.mount_repo).resolve()}:{self.repo_mount_dir} "
            if self.mount_repo
            else ""
        )
        return f"docker run -d {net}{plat}{vols}{repo}--name {self._name} {self.image} sleep infinity"

    def __enter__(self) -> "DockerExecutor":
        start = _run_subprocess(self._run_command(), timeout=300)
        if not start.ok:
            raise RuntimeError(
                f"failed to start probe container: {start.stderr.strip()}"
            )
        self.container_id = start.stdout.strip()
        if self.bootstrap_uv and self.network:
            self._bootstrap_uv()
        return self

    def _bootstrap_uv(self) -> None:
        """Install ``uv`` into the probe container (non-fatal on failure).

        ``install_closure`` uses ``uv pip install`` for speed; the base images
        used here don't ship it.  Best-effort: a failure here surfaces later as
        a real ``uv: command not found`` failure from the closure install, with
        richer context than we could produce here.
        """
        result = self.run("python -m pip install -q uv", timeout=300)
        if result.returncode != 0:
            stderr_tail = result.stderr.strip()[-500:]
            print(
                f"[DockerExecutor] warning: uv bootstrap failed (rc={result.returncode}): "
                f"{stderr_tail}"
            )

    def __exit__(self, *exc: object) -> None:
        # Only tear down a container that __enter__ actually started; if the
        # context was never entered (e.g. an exception before the `with`),
        # container_id is None and there is nothing to remove.
        if self.container_id is not None:
            _run_subprocess(f"docker rm -f {self._name}", timeout=120)
        self.container_id = None
        return None

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        if self.container_id is None:
            raise RuntimeError("DockerExecutor.run called outside its context")
        # Run through an inner shell so pipes/heredocs in `command` work.
        escaped = command.replace("'", "'\"'\"'")
        exec_cmd = f"docker exec {self._name} sh -c '{escaped}'"
        result = _run_subprocess(exec_cmd, timeout=timeout)
        # Report the user's command (not the docker wrapper) for clean evidence.
        return CommandResult(
            command=command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
