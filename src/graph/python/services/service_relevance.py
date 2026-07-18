"""SCOPE: which declaration describes the TEST environment (spec §10.2).

Relevance is reachability, not a path pattern: a declaration is test-relevant
if something that runs the tests references it. Path filtering is unsound in
both directions -- `tests/db/compose.yml` IS an environment; a library's
`compose_fixtures/` is not.
"""
from __future__ import annotations

import os
import posixpath
import re
from typing import Iterator

from graph.python.services.service_evidence import Relevance
from graph.python.services.service_sources import RawDeclaration, load_yaml

# `docker compose ... up`, `docker-compose ... up` -- one shell command, bounded
# by a newline or a shell separator (`;`, `|`, `&`) so a reference never leaks
# across into an unrelated command on the same line.
_COMPOSE_CMD = re.compile(r"docker[-\s]compose[^\n;|&]*")
# Each `-f X` / `--file X` / `--file=X` inside that command. Matches the equals
# form and quoted paths that contain spaces, and a single invocation may name
# several files (`-f base.yml -f override.yml`) -- every one is a referenced env.
_FILE_FLAG = re.compile(r"""(?:-f|--file)(?:=|\s+)("[^"]*"|'[^']*'|\S+)""")
_DRIVE = re.compile(r"^[A-Za-z]:")   # `C:\x` -> `C:/x` is absolute, not repo-relative

# Compose auto-loads an `override` file alongside its base when invoked with no
# `-f`, so a root override IS part of the repo's default environment.
_ROOT_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
               "docker-compose.override.yml", "docker-compose.override.yaml",
               "compose.override.yml", "compose.override.yaml")


def _norm(path: str) -> str | None:
    """A declaration's repo-relative POSIX path, or None if it cannot be one.

    `lstrip("./")` is NOT path normalization -- it strips leading `.` and `/`
    CHARACTERS, so `../compose.yml` becomes `compose.yml` and falsely matches a
    root declaration, while `deploy/../compose.yml` is left unnormalized and
    matches nothing. Both failures are silent mis-scoping.
    """
    raw = path.strip().strip("'\"").replace("\\", "/")
    if not raw:
        return None
    norm = posixpath.normpath(raw)
    if (posixpath.isabs(norm) or _DRIVE.match(norm)
            or norm in (".", "..") or norm.startswith("../")):
        return None                       # absolute, or escapes the repo: not a declaration
    return norm


def _run_bodies(doc: object) -> Iterator[str]:
    """Every `jobs.<job>.steps[].run` string. Scanning raw file TEXT instead
    would manufacture a reference out of a comment or a step's `name:` field."""
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return
    for job in jobs.values():
        steps = job.get("steps") if isinstance(job, dict) else None
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                yield step["run"]


def ci_referenced_compose_files(repo: str) -> frozenset[str]:
    """Compose paths that a workflow explicitly brings up. This is the edge from
    'the thing that runs the tests' to 'the environment it needs'.

    Known limitation: a `run:` body that merely *echoes* the command
    (`echo "docker compose -f fake.yml"`) still registers. Accepted -- a repo
    that echoes the command almost always runs it too, and a false
    `ci_referenced_compose` only promotes a node we would have surfaced anyway.
    """
    wf = os.path.join(repo, ".github", "workflows")
    if not os.path.isdir(wf):
        return frozenset()
    found: set[str] = set()
    for fname in sorted(os.listdir(wf)):
        if not fname.lower().endswith((".yml", ".yaml")):
            continue
        for body in _run_bodies(load_yaml(os.path.join(wf, fname))):
            for cmd in _COMPOSE_CMD.finditer(body):
                for m in _FILE_FLAG.finditer(cmd.group(0)):
                    ref = _norm(m.group(1))
                    if ref:
                        found.add(ref)
    return frozenset(found)


def compute_relevance(decl: RawDeclaration, ci_refs: frozenset[str]) -> Relevance:
    if decl.kind == "ci":
        return "ci_service"                       # the job IS the test
    norm = _norm(decl.file)
    if norm is None:
        return "unreferenced_compose"
    if norm in ci_refs:
        return "ci_referenced_compose"
    if "/" not in norm and norm.lower() in _ROOT_NAMES:
        return "root_compose"
    return "unreferenced_compose"
