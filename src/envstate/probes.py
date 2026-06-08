from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Tuple

from src.envstate.acl import certify_from_probe
from src.envstate.types import Evidence, Status

# A probe executor is any callable(command:str) -> (rc:int, stdout:str).
# Sandbox.exec_readonly satisfies this contract.
ProbeExecutor = Callable[[str], Tuple[int, str]]

CLI = "cli"
PYTHON_IMPORT = "python_import"
PKG_CONFIG = "pkg_config"
HEADER = "header"
SOURCE_BUILD = "source_build"


@dataclass(frozen=True)
class ProbeSpec:
    kind: str
    name: str
    predicate: str
    command: str = ""  # optional explicit override (e.g. source_build replay)


@dataclass(frozen=True)
class ProbeResult:
    spec: ProbeSpec
    rc: int
    stdout: str
    passed: bool
    env_revision: int
    container_id: str


def build_probe_command(spec: ProbeSpec) -> str:
    if spec.command:
        return spec.command
    if spec.kind == CLI:
        return f"command -v {spec.name} && {spec.name} --version"
    if spec.kind == PYTHON_IMPORT:
        # Prefer python3, fall back to python — many images only ship one.
        py = f"import {spec.name}; print(getattr({spec.name}, '__version__', 'no-version'))"
        return f"python3 -c \"{py}\" 2>/dev/null || python -c \"{py}\""
    if spec.kind == PKG_CONFIG:
        return f"pkg-config --exists {spec.name} && pkg-config --modversion {spec.name}"
    if spec.kind == HEADER:
        # Test for the header FILE on the include search path. Do NOT compile —
        # slim base images often ship no C compiler, which would false-MISSING a
        # header that is actually present. `find` needs no toolchain.
        return (
            "find /usr/include /usr/local/include "
            f"-type f -name {spec.name!r} 2>/dev/null | grep -q ."
        )
    if spec.kind == SOURCE_BUILD:
        raise ValueError("source_build probes require an explicit `command`")
    raise ValueError(f"unknown probe kind {spec.kind!r}")


def evaluate_probe(spec: ProbeSpec, rc: int, stdout: str) -> bool:
    # V1: presence == exit code 0. The predicate string is documentation/evidence text.
    return rc == 0


def run_probe(
    executor: ProbeExecutor, spec: ProbeSpec, env_revision: int, container_id: str
) -> ProbeResult:
    command = build_probe_command(spec)
    rc, stdout = executor(command)
    return ProbeResult(
        spec=spec,
        rc=rc,
        stdout=stdout,
        passed=evaluate_probe(spec, rc, stdout),
        env_revision=env_revision,
        container_id=container_id,
    )


def certify_probe_result(snapshot, requirement_id: str, result: ProbeResult):
    """Translate a ProbeResult into host-certified PRESENT/MISSING via the ACL."""
    status = Status.PRESENT if result.passed else Status.MISSING
    evidence = Evidence(
        probe_cmd=build_probe_command(result.spec),
        rc=result.rc,
        stdout_predicate=result.spec.predicate,
        env_revision=result.env_revision,
        container_id=result.container_id,
    )
    return certify_from_probe(snapshot, requirement_id, status, evidence)
