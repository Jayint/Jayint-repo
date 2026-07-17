# src/python_deps/depgraph/arbitrate.py
"""Collision-zone arbitration: the exception-aware per-name probe under the
canonical TestEnvPlan, gated on cure success. A deferred collision installs its
PyPI namesake ONLY IF the cure succeeded AND the name genuinely does not resolve
locally (review §1, §7). Container-bound; a sibling of relink/certify, not the
classifier."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from python_deps.depgraph.cure import CureResult, _env_prefix, _run_dir
from python_deps.depgraph.invocation_resolver import TestEnvPlan

_NAME_ERR = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


@dataclass(frozen=True)
class Arbitration:
    resolves_local: frozenset[str]
    fallthrough: frozenset[str]
    unresolved: frozenset[str]


def probe_name(executor, plan: TestEnvPlan, name: str) -> str:
    mount = getattr(executor, "repo_mount_dir", "/workspace/repo")
    cmd = f"cd {shlex.quote(_run_dir(mount, plan))} && {_env_prefix(plan)}python3 -c 'import {name}'"
    result = executor.run(cmd, timeout=120)
    if result.ok:
        return "local"                                  # imports cleanly under the plan → local
    match = _NAME_ERR.search(result.stderr or "")
    if match and match.group(1).split(".", 1)[0] == name:
        return "fallthrough"                            # name genuinely absent → external
    return "broken_local"                               # any other exception → present-but-broken


def arbitrate(executor, plan: TestEnvPlan, cure: CureResult, deferred: frozenset[str]) -> Arbitration:
    if not cure.ok:
        return Arbitration(frozenset(), frozenset(), frozenset(deferred))
    local: set[str] = set()
    through: set[str] = set()
    for name in sorted(deferred):
        verdict = probe_name(executor, plan, name)
        (through if verdict == "fallthrough" else local).add(name)
    return Arbitration(frozenset(local), frozenset(through), frozenset())
