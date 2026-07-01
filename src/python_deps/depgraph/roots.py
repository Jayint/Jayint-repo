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
from typing import TYPE_CHECKING

from python_deps.depgraph.naming import package_roots
from python_deps.depgraph.resolve_lock import _marker_applies
from python_deps.depgraph.schema import DepGraph
from python_deps.evidence import collect_python_dependency_evidence
from python_deps.import_mapping import (
    normalize_package_name,
    top_level_import_name,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids any import-order risk
    from python_deps.depgraph.target_env import TargetEnv

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

    Per-dep extras (``uvicorn[standard]``) are carried too — NOT stripped — so
    the extra's own transitive deps reach the resolver instead of silently
    vanishing under a ``--no-deps`` install (Task 8).
    """
    spec = (getattr(req, "specifier", "") or "").replace(" ", "")
    extras = getattr(req, "extras", ()) or ()
    name = f"{req.name}[{','.join(extras)}]" if extras else req.name
    if spec and _SAFE_SPECIFIER_RE.match(spec):
        return f"{name}{spec}"
    return name


# Optional-dependency group name embedded at the tail of a requirement's
# ``source`` string by evidence.py, e.g.
# ``pyproject.toml:project.optional-dependencies.test`` -> ``test``, or
# ``setup.cfg:options.extras_require.docs`` -> ``docs``.
_OPTIONAL_GROUP_RE = re.compile(r"(?:optional-dependencies|extras_require)\.(.+)$")


def _requirement_group(source: str) -> str:
    """Extras-group name a ``kind=="optional_dependency"`` requirement belongs to."""
    match = _OPTIONAL_GROUP_RE.search(source or "")
    return match.group(1) if match else ""


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
    # TODO(target-stdlib): this is the HOST interpreter's stdlib set, not the
    # TARGET container's (Task 7 threads a TargetEnv through resolve/marker-eval
    # but this filter runs at root-selection time, before any container is
    # probed, and the target's stdlib set isn't available here). A host running
    # a different python minor than the target could under/over-filter modules
    # that moved in/out of stdlib between versions (e.g. `tomllib` 3.11+,
    # `distutils` removed in 3.12). Low blast radius today (root-selection only
    # drops names it is CONFIDENT aren't distributions), but not target-honest.
    if top in sys.stdlib_module_names:
        return True
    # Leading-underscore private/dunder modules are not distributions.
    if top.startswith("_"):
        return True
    return False


def _env_marker_excludes(req, target_env: "TargetEnv | None") -> bool:
    """True only when we are CONFIDENT ``req`` must not be a root for the target.

    Conservative-skip rule (review "no silent shrink" constraint): a dep is
    excluded ONLY when ALL of the following hold —

    * a real ``target_env`` was given (no target -> nothing is filterable),
    * the requirement HAS a marker at all,
    * the marker does NOT reference ``extra`` — ``extra == "..."`` markers
      gate optional-dependency GROUPS, which are already handled by the
      ``needed_extras`` mechanism above; this function must never re-judge
      them, or it would silently drop an extra's dep for a reason unrelated
      to environment (the exact silent-shrink this review flags against), and
    * the marker EVALUATES to False against ``target_env.marker_env()``.

    Every other case — no target_env, no marker, an extra-referencing marker,
    a True evaluation, or any evaluation error (``_marker_applies`` returns
    ``None`` on ``packaging`` absence or a malformed marker) — KEEPS the dep.
    Keeping on uncertainty is the point: an over-eager filter here would
    silently shrink the closure fed to ``uv``, which is worse than resolving
    one extra unneeded root.
    """
    if target_env is None:
        return False
    marker = getattr(req, "marker", "") or ""
    if not marker:
        return False
    if "extra" in marker:
        return False
    try:
        verdict = _marker_applies(marker, target_env.marker_env())
    except Exception:
        return False
    return verdict is False


def select_roots(
    repo_path: str,
    graph: DepGraph,
    needed_extras: frozenset[str] = frozenset(),
    *,
    target_env: "TargetEnv | None" = None,
) -> list[tuple[str | None, str]]:
    """Return ``(import_id | None, distribution_name)`` resolver roots.

    Declared manifest dependencies come first (``import_id=None``); mapped
    scanned imports fill gaps only for distributions not already declared.
    Non-distributions are filtered out, and each distribution appears once
    (deduped by normalized name).

    ``needed_extras`` TARGETS which ``[project.optional-dependencies]`` /
    ``extras_require`` groups are in scope: a ``kind=="optional_dependency"``
    requirement is only added as a root when its group is a member of
    ``needed_extras``; runtime (non-optional) deps are always included. This
    is the fix for the "uv unions all extras groups" bug — previously every
    optional group was appended as a root with no filter, so mutually
    exclusive groups (e.g. ``cpu``/``gpu``) collided into one unsatisfiable
    resolve. The default (``frozenset()``) is deliberately runtime-only; see
    ``build.py`` for the seam that would eventually source this set from
    discovered CI/tox/Makefile ``pip install -e .[...]`` invocations
    (cluster-1 enrichment, not this task).

    ``target_env`` (Task 8 review fix), when given, additionally drops a
    manifest dependency whose PEP 508 environment marker evaluates False for
    the TARGET (e.g. ``foo ; sys_platform == 'win32'`` on a Linux target) —
    see :func:`_env_marker_excludes` for the conservative "keep unless
    certain" rule. Default ``None`` preserves the pre-Task-8-review behavior
    for every existing caller (``advise.py`` and current tests).
    """
    evidence = collect_python_dependency_evidence(repo_path)
    declared_names = {req.name for req in evidence.declared_dependencies}

    roots: list[tuple[str | None, str]] = []
    seen: set[str] = set()

    # 1. Manifest-declared dependencies (highest trust).
    for req in evidence.declared_dependencies:
        if getattr(req, "kind", "dependency") == "optional_dependency":
            if _requirement_group(req.source) not in needed_extras:
                continue
        if _env_marker_excludes(req, target_env):
            continue
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
