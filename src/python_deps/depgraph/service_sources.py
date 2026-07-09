"""DISCOVER: pluggable evidence sources (spec §10.1).

Each source yields RawDeclarations. Nothing downstream knows which source a
declaration came from, so adding one (GitLab CI, k8s, devcontainer) never
touches the schema or its consumers.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import yaml

from python_deps.depgraph.service_parse import parse_env


@dataclass(frozen=True)
class RawDeclaration:
    name: str
    entry: dict
    file: str            # repo-relative
    locator: str
    kind: str            # "compose" | "ci"
    doc_env_values: tuple[str, ...]   # each env value in the same document, separately
                                      # (sibling-DSN input; NOT joined — Task 3 urlparses each)


class ServiceEvidenceSource(Protocol):
    def discover(self, repo: str) -> Iterator[RawDeclaration]: ...


def _load(path: str) -> object | None:
    try:
        with open(path, errors="replace") as fh:
            return yaml.safe_load(fh)
    except Exception:                      # noqa: BLE001 - a bad file skips itself
        return None


def _walk(repo: str) -> Iterator[tuple[str, str]]:
    for root, _dirs, files in os.walk(repo):
        for fn in files:
            path = os.path.join(root, fn)
            yield path, os.path.relpath(path, repo)


def _doc_env_values(svcs: dict) -> tuple[str, ...]:
    """Every env value declared anywhere in the document, each kept SEPARATE.

    This is the sibling-DSN input for the port ladder: Task 3 must ``urlparse``
    each value on its own to test whether its hostname equals a target service's
    name. A concatenated blob makes that impossible (``postgres://db:5432@other``
    would misread ``db`` — the username — as a host on 5432).
    """
    return tuple(v for e in svcs.values() if isinstance(e, dict)
                 for v in parse_env(e).values())


class ComposeSource:
    def discover(self, repo: str) -> Iterator[RawDeclaration]:
        for path, rel in _walk(repo):
            low = os.path.basename(path).lower()
            if "compose" not in low or not low.endswith((".yml", ".yaml")):
                continue
            doc = _load(path)
            svcs = doc.get("services") if isinstance(doc, dict) else None
            if not isinstance(svcs, dict):
                continue
            env_values = _doc_env_values(svcs)
            for name, entry in svcs.items():
                if isinstance(entry, dict):
                    yield RawDeclaration(str(name), entry, rel, f"services.{name}",
                                         "compose", env_values)


class GithubActionsSource:
    def discover(self, repo: str) -> Iterator[RawDeclaration]:
        wf = os.path.join(repo, ".github", "workflows")
        if not os.path.isdir(wf):
            return
        for fname in sorted(os.listdir(wf)):
            if not fname.lower().endswith((".yml", ".yaml")):
                continue
            path = os.path.join(wf, fname)
            doc = _load(path)
            jobs = doc.get("jobs") if isinstance(doc, dict) else None
            if not isinstance(jobs, dict):
                continue
            rel = os.path.relpath(path, repo)
            for job, jb in jobs.items():
                if not isinstance(jb, dict):
                    continue
                svcs = jb.get("services")
                if not isinstance(svcs, dict):
                    continue
                for name, entry in svcs.items():
                    # A real GH-Actions service container ALWAYS declares `image:`.
                    if isinstance(entry, dict) and entry.get("image"):
                        yield RawDeclaration(str(name), entry, rel,
                                             f"jobs.{job}.services.{name}", "ci", ())


DEFAULT_SOURCES: tuple[ServiceEvidenceSource, ...] = (ComposeSource(), GithubActionsSource())


def discover_all(repo: str,
                 sources: tuple[ServiceEvidenceSource, ...] = DEFAULT_SOURCES,
                 ) -> list[RawDeclaration]:
    return [d for s in sources for d in s.discover(repo)]
