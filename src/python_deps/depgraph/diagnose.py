"""Diagnosis router (design 2026-07-01 diagnosis-first loop).

Pure module — no src.envstate imports. Unit-testable with plain strings.

Wraps the pure runtime classifier with repository context so the loop can
DISTINGUISH an environment requirement from a repo-internal reference, a
residual bug, or a disproven attempt — and so a repo-local import is never
mis-added as a PyPI package (the design's single highest-value guard).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from python_deps.depgraph.runtime_classify import Discovery


class Mode(enum.Enum):
    ENVIRONMENT = "environment"                    # real env requirement -> ingest + repair
    REPO_INTERNAL_REF = "repo_internal_reference"  # local import/path -> out of scope
    RESIDUAL = "residual"                          # assertion/logic bug -> non-env give-up
    INVALID_ATTEMPT = "invalid_attempt"            # pip disproved this name -> do not retry
    AMBIGUOUS = "ambiguous"                        # unclear -> probe then reclassify


@dataclass(frozen=True)
class RepoContext:
    local_names: frozenset[str] = field(default_factory=frozenset)
    invalid_names: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Diagnosis:
    mode: Mode
    discovery: Discovery | None   # populated only when mode is ENVIRONMENT
    reason: str


def is_local_import(import_name: str, local_names: frozenset[str]) -> bool:
    """True when ``import_name`` (or its top-level package) is defined in the repo.

    ``local_names`` are basenames from ``scan.local_module_names`` (dirs with
    ``__init__.py`` and top-level ``*.py`` stems), so compare the first dotted
    segment: ``docs_src.helpers`` is local iff ``docs_src`` is local.
    """
    if not import_name:
        return False
    return import_name.split(".", 1)[0] in local_names
