"""Root selection for the resolver — manifest-first, scan-gap-filled, filtered.

The resolver (stage 3) needs a set of *distribution* roots to lock.  Picking the
wrong roots is what caused the "Run-A collapse": feeding non-distributions
(stdlib modules, Python-2 shims, typing-only stubs) to ``uv`` made the whole
resolve fail and produced an empty Package layer.

This module realizes the spec's "Root selection" ladder
(``docs/superpowers/specs/2026-06-23-uv-enriched-depgraph.md``):

1. **Manifest first** — declared dependencies (parsed via
   :func:`python_deps.evidence.collect_python_dependency_evidence`) are the
   highest-trust roots.  They carry no import node, so their ``import_id`` is
   ``None``.
2. **Scan gap-fill** — mapped scanned imports (:func:`naming.package_roots`) are
   added only for distributions not already covered by a manifest declaration.
3. **Filter non-distributions** — stdlib modules, known Py2 shims, typing-only
   stubs, and obvious junk are dropped before anything reaches ``uv``: a name
   that isn't plausibly a PyPI distribution never becomes a root.

Pure (no Executor / no network): it reads the repo on disk and an already-scanned
graph, and returns the root list.
"""

from __future__ import annotations

import re
import sys

from python_deps.depgraph.naming import package_roots
from python_deps.depgraph.schema import DepGraph
from python_deps.evidence import collect_python_dependency_evidence
from python_deps.import_mapping import (
    normalize_package_name,
    top_level_import_name,
)

# Python-2 modules that survive a py3 static scan as "external" (they are neither
# py3 stdlib nor project-local) but are NOT installable PyPI distributions.
PY2_SHIM_DENYLIST: frozenset[str] = frozenset(
    {
        "StringIO",
        "cStringIO",
        "BaseHTTPServer",
        "SimpleHTTPServer",
        "CGIHTTPServer",
        "urllib2",
        "urlparse",
        "Queue",
        "cPickle",
        "cProfile",
        "Tkinter",
        "httplib",
        "cookielib",
        "Cookie",
        "thread",
        "copy_reg",
        "__builtin__",
        "ConfigParser",
        "HTMLParser",
        "SocketServer",
        "xmlrpclib",
        "robotparser",
        "repr",
    }
)

# Typing-only / stub-only modules that exist for static analysis, not runtime.
TYPING_ONLY_DENYLIST: frozenset[str] = frozenset({"_typeshed"})

# Obvious junk that is never a distribution root.
JUNK_DENYLIST: frozenset[str] = frozenset({"", "__future__", "__main__"})

# A version specifier safe to embed verbatim in the resolver's temp pyproject
# (a TOML double-quoted string) and the fallback heredoc body: PEP 440 operators
# plus version characters only — no quotes, spaces, newlines, shell metacharacters
# or environment markers.  Anything else falls back to the bare name.
_SAFE_SPECIFIER_RE = re.compile(
    r"^[<>=!~]=?[A-Za-z0-9.][A-Za-z0-9.*+!-]*"
    r"(?:,[<>=!~]=?[A-Za-z0-9.][A-Za-z0-9.*+!-]*)*$"
)


def _manifest_root_token(req) -> str:
    """Distribution token for a manifest dep, carrying a safe version specifier.

    Carrying the declared constraint (e.g. ``numpy<2``) is what lets the resolver
    SEE a conflict — the spec's "project pinning numpy<2 plus a dep requiring
    numpy>=2" example.  The specifier is dropped (bare name only) when it is empty,
    carries a marker, or fails the injection-safety check.
    """
    spec = (getattr(req, "specifier", "") or "").replace(" ", "")
    if spec and _SAFE_SPECIFIER_RE.match(spec):
        return f"{req.name}{spec}"
    return req.name


def _is_non_distribution(import_name: str) -> bool:
    """True when ``import_name`` cannot be a real PyPI distribution root."""
    top = top_level_import_name(import_name).strip()
    if top in JUNK_DENYLIST:
        return True
    if top in PY2_SHIM_DENYLIST:
        return True
    if top in TYPING_ONLY_DENYLIST:
        return True
    # Stdlib for the running interpreter (proxy for the target). scan.py already
    # drops py3 stdlib, but this defends the manifest path and odd classifications.
    if top in sys.stdlib_module_names:
        return True
    # Leading-underscore private/dunder modules are not distributions.
    if top.startswith("_"):
        return True
    return False


def select_roots(
    repo_path: str,
    graph: DepGraph,
) -> list[tuple[str | None, str]]:
    """Return ``(import_id | None, distribution_name)`` resolver roots.

    Declared manifest dependencies come first (``import_id=None``); mapped
    scanned imports fill gaps only for distributions not already declared.
    Non-distributions are filtered out, and each distribution appears once
    (deduped by normalized name).
    """
    evidence = collect_python_dependency_evidence(repo_path)
    declared_names = {req.name for req in evidence.declared_dependencies}

    roots: list[tuple[str | None, str]] = []
    seen: set[str] = set()

    # 1. Manifest-declared dependencies (highest trust).
    for req in evidence.declared_dependencies:
        normalized = normalize_package_name(req.name)
        if normalized in seen:
            continue
        if _is_non_distribution(req.name):
            continue
        seen.add(normalized)
        roots.append((None, _manifest_root_token(req)))

    # 2. Scan gap-fill: mapped imports not already covered by a declaration.
    for import_node_id, dist_name in package_roots(graph, declared_names):
        node = graph.get(import_node_id)
        module_name = node.name if node is not None else import_node_id
        if _is_non_distribution(module_name):
            continue
        normalized = normalize_package_name(dist_name)
        if normalized in seen:
            continue
        seen.add(normalized)
        roots.append((import_node_id, dist_name))

    return roots
