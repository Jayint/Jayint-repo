from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Callable, List

from src.envstate.probes import ProbeSpec, build_probe_command

# run_command(image_ref, command) -> (rc, stdout). In production this starts a
# throwaway container from the built image and runs the command; in tests it is faked.
InImageRunner = Callable[[str, str], tuple]


@dataclass(frozen=True)
class CleanroomResult:
    passed: bool
    reason: str
    failed_probes: tuple = ()
    failed_tests: tuple = ()


def ensure_repo_in_dockerfile(dockerfile_text: str, workdir: str) -> str:
    """Insert `COPY . <workdir>` right after the WORKDIR line so a clean-room rebuild's
    image actually contains the repo. The synthesizer omits COPY because the live flow
    seeds the repo into the running container directly (Sandbox.put_archive); a
    from-scratch image must COPY it in or repo-dependent tests fail structurally.
    Placed after WORKDIR (before the RUN steps) so build-time steps like `pip install -e .`
    see the repo. Idempotent; falls back to appending if no WORKDIR line is present."""
    workdir = workdir or "/app"
    copy_line = f"COPY . {workdir}"
    if copy_line in (dockerfile_text or ""):
        return dockerfile_text
    lines = (dockerfile_text or "").splitlines()
    out = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().startswith("WORKDIR "):
            out.append(copy_line)
            inserted = True
    if not inserted:
        out.append(copy_line)
    return "\n".join(out)


def verify_cleanroom(
    docker_client,
    dockerfile_text: str,
    build_context_dir: str,
    probes: List[ProbeSpec],
    test_commands: List[str],
    run_command: InImageRunner,
) -> CleanroomResult:
    """Build a fresh image from the Dockerfile + repo context, then re-run probes + tests.

    The clean-room Dockerfile contains a `COPY` (injected by the caller via
    `ensure_repo_in_dockerfile`) so the rebuilt image includes the repo; `COPY` resolves
    only against a build CONTEXT, so we build from a directory (`path=`), not a bare
    `fileobj`. `build_context_dir` must contain the repo files; we drop the Dockerfile
    text into it under a unique name and build against it.
    """
    dockerfile_name = "Dockerfile.envstate-cleanroom"
    try:
        with open(os.path.join(build_context_dir, dockerfile_name), "w", encoding="utf-8") as handle:
            handle.write(dockerfile_text)
        image, _logs = docker_client.images.build(
            path=build_context_dir, dockerfile=dockerfile_name, rm=True
        )
    except Exception as exc:  # build failure is a hard fail
        return CleanroomResult(False, f"clean-room build failed: {exc}")

    image_ref = image if isinstance(image, str) else getattr(image, "id", str(image))

    if not probes and not test_commands:
        return CleanroomResult(False, "clean-room had nothing to verify (no probes or test commands)")

    failed_probes = []
    for spec in probes:
        rc, _out = run_command(image_ref, build_probe_command(spec))
        if rc != 0:
            failed_probes.append(spec.name)
    if failed_probes:
        return CleanroomResult(False, "probe(s) regressed in clean image",
                               failed_probes=tuple(failed_probes))

    failed_tests = []
    for command in test_commands:
        rc, _out = run_command(image_ref, command)
        if rc != 0:
            failed_tests.append(command)
    if failed_tests:
        return CleanroomResult(False, "test command(s) failed in clean image",
                               failed_tests=tuple(failed_tests))

    return CleanroomResult(True, "clean-room verification passed")
