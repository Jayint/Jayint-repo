"""Stage 1: static import scan -> Import nodes (route-not-drop; Stage C flip).

Wraps :func:`graph.python.read.import_graph.scan_imports` (which classifies each
top-level import as ``stdlib`` / ``project_local`` / ``external``) and lifts the
``external`` findings into the concrete dependency graph of
``docs/DESIGN-static-probe-certified-dependency-graph.md`` section 5:

  * one ``Import`` node per external import
    (``type=IMPORT``, ``layer=NAMING``, ``discovered_by=STATIC_SCAN``,
    ``state=UNKNOWN``, ``provenance`` = the source file(s),
    ``check_command = python -c "import <name>"``).

THE FLIP (2026-07-17 config-lane spec, "Relocated scan drops"): this stage no
longer drops first-party/local names, and no longer mints the ``Test`` goal node
or the flat ``Test -> Import`` hub. The lane classifier (``route.classify``) is now
the routing authority — a locally-shadowed name (``items``, ``blueprintapp``) is
minted here as an Import node and routed by the classifier to the collision zone
(deferred) or to a first-party ``Module``, never left to Phase-A's dist-guesser.
The goal spine (``project -> module -> import -> {module|package}``) and the ``Test``
goal node are minted downstream by ``skeleton._add_project_node``. Two drops stay
here (they must never become install-lane Import nodes): ``_``-prefixed private/
typing names, and excluded-dir-only imports (examples/docs/scripts/tools — a name
seen ONLY there is out of the "run the repo properly" scope and pre-flip was never
installed; keeping the drop preserves install-line byte-identity).

Static scanning is evidence, not completeness (design 4.1 / 10.1): dynamic and
plugin imports are not visible here.  This stage never sets ``state`` beyond
``UNKNOWN`` — only the host certifier (Task 8) flips it.
"""

from __future__ import annotations

import os
import re

from graph.python.read.import_graph import scan_imports

from graph.model import import_id
from graph.model import (
    DepGraph,
    DiscoveredBy,
    Layer,
    Node,
    NodeType,
    State,
)

# Directory segments whose imports are NOT part of "running the repo properly":
# examples/docs/build artifacts pull in non-project deps (and sometimes non-PyPI
# example names) that wrongly inflate — and can collapse — the resolver closure.
# Scope the scan to project source + tests; an import survives if it appears in at
# least one non-excluded file.
_EXCLUDED_SEGMENTS: frozenset[str] = frozenset(
    {
        "examples", "example", "docs", "doc", "build", "dist", "samples",
        "sample", "benchmarks", "benchmark", "bench", "scripts", "script",
        ".github", ".tox", "node_modules", "site-packages", ".venv", "venv",
        "tools",
    }
)


def _is_excluded_path(path: str) -> bool:
    """True when every meaningful segment routes through an excluded directory."""
    segments = {seg.lower() for seg in re.split(r"[\\/]+", path) if seg}
    return bool(segments & _EXCLUDED_SEGMENTS)


def _in_scope_files(source_files: tuple[str, ...]) -> tuple[str, ...]:
    """Keep only source files that are not under an excluded directory."""
    return tuple(f for f in source_files if not _is_excluded_path(f))


# Dirs never worth walking for local-name detection (vcs/build/venv noise).
# PUBLIC: `repo_modules` walks the SAME tree and MUST prune identically —
# if the two walks diverge, the subset invariant in repo_modules breaks.
SKIP_WALK_DIRS: frozenset[str] = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache"}
) | _EXCLUDED_SEGMENTS

_SKIP_WALK_DIRS: frozenset[str] = SKIP_WALK_DIRS  # back-compat alias


def _local_module_names(repo_path: str) -> frozenset[str]:
    """Names defined *inside* the repo — packages (dirs with ``__init__.py``) and
    top-level module files (``*.py`` stems), found anywhere (incl. nested under
    ``tests/``).

    ``scan_imports`` only treats root/``src`` modules as project-local, so test
    fixture sub-packages (e.g. flask's ``blueprintapp``/``site_package`` under
    ``tests/``) leak through as "external" and become bogus PyPI roots.  A name
    that resolves to a local file/dir is never a distribution the environment must
    install.
    """
    names: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_WALK_DIRS]
        if "__init__.py" in filenames:
            names.add(os.path.basename(dirpath))
        for fname in filenames:
            if fname.endswith(".py") and fname != "__init__.py":
                names.add(fname[:-3])
    return frozenset(names)


def local_module_names(repo_path: str) -> frozenset[str]:
    """Public alias for :func:`_local_module_names` (used by the diagnosis router)."""
    return _local_module_names(repo_path)


def _import_check_command(name: str) -> str:
    return f'python -c "import {name}"'


def _build_import_node(
    name: str, source_files: tuple[str, ...], *,
    optional: bool = False, symbols: tuple[str, ...] = (),
) -> Node:
    provenance = ", ".join(source_files) if source_files else None
    # Tag only genuinely-guarded imports; leave data empty (falsy) otherwise to
    # preserve the "never needlessly rewrite node data" property elsewhere.
    # symbols (the used attributes/from-names) ride alongside, only when present.
    data: dict = {}
    if optional:
        data["optional"] = True
    if symbols:
        data["symbols"] = symbols
    return Node(
        id=import_id(name),
        type=NodeType.IMPORT,
        name=name,
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.UNKNOWN,
        check_command=_import_check_command(name),
        provenance=provenance,
        data=data,
    )


def scan_to_nodes(repo_path: str) -> DepGraph:
    """Scan ``repo_path`` and return a graph of raw top-level Import nodes (no edges).

    THE FLIP (route-not-drop): first-party/local names are NO LONGER dropped here.
    Scan mints an Import node for every ``external`` OR ``project_local`` finding and
    hands routing to ``route.classify``, which owns ALL four pre-flip drops:
    ``_``-prefix (dropped by the classifier), ``local_names`` and non-external
    classifications (routed by the ladder), and excluded-dir-only names (routed to the
    collision zone). ``classify.apply_routing`` then REMOVES any Import node the ladder
    did not route, so an unroutable name never reaches the install lane.

    The ONE drop scan keeps is ``stdlib``: a stdlib name has no lane role (never a
    dist, never a Module, never on the spine), so minting it only to have the
    classifier remove it is pointless churn — and ``scan_imports``' classification is a
    reliable host-side signal for it. First-party (``project_local``) names ARE now
    minted, so internal imports appear on the spine.

    The ``Test`` goal node and the goal spine (``project -> module -> import``) are
    minted downstream by ``skeleton._add_project_node`` and the config-lane wiring.
    """
    findings, _project_local, _errors = scan_imports(repo_path)

    graph = DepGraph()

    for finding in findings:
        # stdlib: the one relocated drop scan keeps (see docstring). Every other name
        # — external, project-local, ``_``-prefixed, excluded-dir-only — is minted raw
        # and routed (or dropped) by the classifier.
        if finding.classification == "stdlib":
            continue
        in_scope = _in_scope_files(finding.source_files)
        provenance_files = in_scope or finding.source_files
        graph = graph.with_node(
            _build_import_node(
                finding.import_name, provenance_files,
                optional=finding.optional, symbols=finding.symbols,
            )
        )

    return graph


def clear_external_names(repo_path: str) -> frozenset[str]:
    """The names the PRE-FLIP scan kept: clear-external only.

    This is the fail-CLOSED fallback for the config lane. If the classifier raises,
    the lane must NOT let first-party/unclassifiable names flow into Phase A as
    external (the false-green vector), so construction falls back to exactly the
    pre-flip behaviour: keep only ``external`` findings that are not ``_``-prefixed,
    not a repo-local shadow (``_local_module_names``), and not excluded-dir-only.
    Every other name is DROPPED (never installed) — the safe direction.
    """
    findings, _project_local, _errors = scan_imports(repo_path)
    local = _local_module_names(repo_path)
    keep: set[str] = set()
    for finding in findings:
        if finding.classification != "external":
            continue
        name = finding.import_name
        if name.startswith("_") or name in local:
            continue
        in_scope = _in_scope_files(finding.source_files)
        if finding.source_files and not in_scope:
            continue
        keep.add(name)
    return frozenset(keep)
