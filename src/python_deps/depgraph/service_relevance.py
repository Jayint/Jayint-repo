"""SCOPE: which declaration describes the TEST environment (spec §10.2).

Relevance is reachability, not a path pattern: a declaration is test-relevant
if something that runs the tests references it. Path filtering is unsound in
both directions -- `tests/db/compose.yml` IS an environment; a library's
`compose_fixtures/` is not.
"""
from __future__ import annotations

import os
import re

from python_deps.depgraph.service_evidence import Relevance
from python_deps.depgraph.service_sources import RawDeclaration

# `docker compose ... up`, `docker-compose ... up` -- one shell command, bounded
# by a newline or a shell separator (`;`, `|`, `&`) so a reference never leaks
# across into an unrelated command.
_COMPOSE_CMD = re.compile(r"docker[-\s]compose[^\n;|&]*")
# Each `-f X` / `--file X` inside that command. A single invocation may name
# several files (`-f base.yml -f override.yml`); every one is a referenced env.
_FILE_FLAG = re.compile(r"(?:-f|--file)\s+(\S+)")

_ROOT_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def ci_referenced_compose_files(repo: str) -> frozenset[str]:
    """Compose paths that a workflow explicitly brings up. This is the edge from
    'the thing that runs the tests' to 'the environment it needs'."""
    wf = os.path.join(repo, ".github", "workflows")
    if not os.path.isdir(wf):
        return frozenset()
    found: set[str] = set()
    for fname in sorted(os.listdir(wf)):
        if not fname.lower().endswith((".yml", ".yaml")):
            continue
        try:
            with open(os.path.join(wf, fname), errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for cmd in _COMPOSE_CMD.finditer(text):
            for m in _FILE_FLAG.finditer(cmd.group(0)):
                found.add(m.group(1).strip("'\"").lstrip("./"))
    return frozenset(found)


def compute_relevance(decl: RawDeclaration, ci_refs: frozenset[str]) -> Relevance:
    if decl.kind == "ci":
        return "ci_service"                       # the job IS the test
    norm = decl.file.replace(os.sep, "/").lstrip("./")
    if norm in ci_refs:
        return "ci_referenced_compose"
    if "/" not in norm and norm.lower() in _ROOT_NAMES:
        return "root_compose"
    return "unreferenced_compose"
