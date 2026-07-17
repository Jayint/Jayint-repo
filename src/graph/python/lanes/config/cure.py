"""In-container config-cure: editable install (build-isolation fallback chain) +
the canonical collect-gate under the TestEnvPlan. On success, stamps a
scratch-certified state so the render-time poison does not erase the config
lane's output (review §3, §9). Container-bound; the command renderer is pure."""
from __future__ import annotations

import posixpath  # container paths are POSIX
import shlex
from dataclasses import dataclass, replace

from graph.python.invocation_resolver import TestEnvPlan
from graph.python.native.probe import INSTALL_TIMEOUT
from graph.model import DepGraph, NodeType, State


@dataclass(frozen=True)
class CureResult:
    ok: bool
    rung: str
    collect_ok: bool
    evidence: str


def _run_dir(mount_dir: str, plan: TestEnvPlan) -> str:
    """The dir to cd into for cure/probe: the config rootdir UNDER the mount.
    plan.cwd is repo-relative ('.' for a root config, e.g. 'sdk/python' for a
    nested one). Materialized here (Task 5's job per the canonical-plan design)."""
    cwd = (plan.cwd or ".").strip()
    if cwd in ("", "."):
        return mount_dir
    return posixpath.join(mount_dir, cwd)


def _env_prefix(plan: TestEnvPlan) -> str:
    parts = []
    pp = ":".join(plan.pythonpath)
    if pp:
        parts.append(f"PYTHONPATH={shlex.quote(pp)}")
    for var, value in plan.env:
        if var == "PYTHONPATH":
            continue  # already merged into plan.pythonpath; avoid a second, clobbering assignment
        parts.append(f"{var}={shlex.quote(value)}")
    return (" ".join(parts) + " ") if parts else ""


def render_cure_commands(plan: TestEnvPlan, mount_dir: str) -> tuple[str, ...]:
    """The build-isolation fallback chain + the collect-gate, all run from the
    config rootdir under the mount (``plan.cwd``; the mount root for a root
    config). Rung 1: isolated ``-e .``. Rung 2 (only if rung 1 fails): ensure
    setuptools/wheel + declared build-system.requires, then ``--no-build-
    isolation -e .`` (a legacy setup.py importing numpy/cython can't see the
    Phase-A closure under isolation). Collect-gate under the plan's env."""
    cd = f"cd {shlex.quote(_run_dir(mount_dir, plan))}"
    env = _env_prefix(plan)
    isolated = f"{cd} && {env}python3 -m pip install --break-system-packages -e ."
    no_iso = (
        f"{cd} && python3 -m pip install --break-system-packages -U setuptools wheel && "
        f"{env}python3 -m pip install --break-system-packages --no-build-isolation -e ."
    )
    collect = f"{cd} && {env}python3 -m pytest --collect-only -q"
    return (isolated, no_iso, collect)


def run_cure(executor, plan: TestEnvPlan) -> CureResult:
    mount = getattr(executor, "repo_mount_dir", "/workspace/repo")
    isolated, no_iso, collect = render_cure_commands(plan, mount)
    r1 = executor.run(isolated, timeout=INSTALL_TIMEOUT)
    rung, ok = ("isolated", True) if r1.ok else ("", False)
    if not ok:
        r2 = executor.run(no_iso, timeout=INSTALL_TIMEOUT)
        rung, ok = ("no_build_isolation", True) if r2.ok else ("failed", False)
    if not ok:
        return CureResult(False, "failed", False, (r1.stderr or "")[-500:])
    cg = executor.run(collect, timeout=INSTALL_TIMEOUT)
    return CureResult(True, rung, cg.ok, f"rung={rung} collect_rc={cg.returncode}")


def stamp_scratch_certified(graph: DepGraph, cure: CureResult) -> DepGraph:
    """On a successful cure, mark the Project node scratch-certified so the
    render-time poison (populate.py) leaves it alone. Additive to data only."""
    if not cure.ok:
        return graph
    new = graph
    for node in graph.nodes:
        if node.type is NodeType.PROJECT:
            data = {**node.data, "scratch_certified": True, "cure_rung": cure.rung}
            new = new.with_node(replace(node, state=State.SATISFIED, data=data))
    return new
