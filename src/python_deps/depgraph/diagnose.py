"""Diagnosis router (design 2026-07-01 diagnosis-first loop).

Pure module — no src.envstate imports. Unit-testable with plain strings.

Wraps the pure runtime classifier with repository context so the loop can
DISTINGUISH an environment requirement from a repo-internal reference, a
residual bug, or a disproven attempt — and so a repo-local import is never
mis-added as a PyPI package (the design's single highest-value guard).
"""
from __future__ import annotations

import enum
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from python_deps.depgraph.runtime_classify import Discovery, classify_observation
from python_deps.failure_classifier import classify_dependency_failure


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


# An assertion / logic failure is a residual (non-environment) bug: the graph
# cannot close it by adding a node. Conservative — anything else stays AMBIGUOUS.
_RESIDUAL_RE = re.compile(r"\bAssertionError\b")

# failure_type values the router treats as import-shaped (candidate packages).
_IMPORT_FAILURE_TYPES = frozenset({"module_not_found", "import_name_error"})


def diagnose(command: str, output: str, ctx: RepoContext) -> Diagnosis:
    """Classify one (command, output) failure into a routing Mode.

    Only ``Mode.ENVIRONMENT`` carries a ``Discovery`` (produced by the existing
    ``classify_observation``, which owns import->package mapping). Every other
    mode carries ``discovery=None`` and a human-readable ``reason``.
    """
    text = output or ""
    dep = classify_dependency_failure(command, text)

    # pip already proved this distribution does not exist -> never retry the name.
    if dep.failure_type == "no_matching_distribution":
        name = dep.package_name or ""
        return Diagnosis(Mode.INVALID_ATTEMPT, None,
                         f"pip found no matching distribution for {name!r}")

    # Import failures split three ways: repo-local (out of scope), previously
    # disproven (invalid), or a genuine external package requirement.
    if dep.failure_type in _IMPORT_FAILURE_TYPES:
        import_name = dep.import_name or ""
        if is_local_import(import_name, ctx.local_names):
            return Diagnosis(Mode.REPO_INTERNAL_REF, None,
                             f"{import_name!r} resolves to a repo-local module")
        disc = classify_observation(command, text)
        if disc is None:
            return Diagnosis(Mode.AMBIGUOUS, None,
                             f"import {import_name!r} had no package mapping")
        if disc.name in ctx.invalid_names:
            return Diagnosis(Mode.INVALID_ATTEMPT, None,
                             f"package {disc.name!r} was previously disproven")
        return Diagnosis(Mode.ENVIRONMENT, disc,
                         f"external import {import_name!r} -> package requirement")

    # Native lib / service / config / tool: reuse the classifier verbatim.
    disc = classify_observation(command, text)
    if disc is not None:
        return Diagnosis(Mode.ENVIRONMENT, disc,
                         f"{disc.node_type.value.lower()} requirement")

    # Nothing environment-shaped matched. Distinguish residual from ambiguous.
    if _RESIDUAL_RE.search(text):
        return Diagnosis(Mode.RESIDUAL, None, "assertion failure — non-environment residual")
    return Diagnosis(Mode.AMBIGUOUS, None, "unclassified failure — probe before repair")


def make_diagnostic_classifier(ctx: RepoContext) -> Callable[[str, str], Discovery | None]:
    """Adapt :func:`diagnose` to the ``ingest_runtime_failures`` classifiers seam.

    Returns a Discovery only for ``Mode.ENVIRONMENT``; every other mode
    (repo-internal-reference, residual, invalid-attempt, ambiguous) returns
    ``None`` so no node is appended. The router's mode/reason are consumed by
    the orchestrator in Phase 2; Phase 1 uses only the ENVIRONMENT/else split.
    """
    def _classify(command: str, output: str) -> Discovery | None:
        return diagnose(command, output, ctx).discovery
    return _classify
