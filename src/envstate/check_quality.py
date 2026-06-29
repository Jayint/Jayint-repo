"""Deterministic check-quality fixes (Stage 2): rewrite brittle SystemLib checks and
reject checks structurally incapable of detecting absence. Pure; no Docker/LLM."""
from __future__ import annotations

import re

from python_deps.depgraph.schema import NodeType
from python_deps.depgraph.check_quality import check_can_detect_absence  # re-export

__all__ = ["rewrite_syslib_check", "check_can_detect_absence"]


def rewrite_syslib_check(node) -> str | None:
    """For a SystemLib whose check is a brittle exact `dpkg -s <name>`, return a
    capability check that survives Debian renames (t64); else None."""
    if node.type is not NodeType.SYSTEM_LIB or not node.check_command:
        return None
    m = re.match(r"^\s*dpkg\s+-s\s+(\S+)\s*$", node.check_command)
    if not m:
        return None
    name = m.group(1)
    soname = name.split(":")[0]
    return f"ldconfig -p | grep -q {soname} || command -v {soname}"
