"""Stage 3 — resolver v2: ``uv.lock``-driven Package closure.

Primary resolve source is **``uv.lock``** (the richest single uv artifact: nodes
+ versions + transitive edges + markers + sdist/wheel artifacts).  The orchestrator
(:func:`resolve_closure`) creates a throwaway uv project in a temp dir, runs
``uv lock`` (targeted at the container's python/platform) *on the host* through the
injected ``Executor`` (locked decision 1: the ``uv`` binary is invoked, never
imported), reads the produced ``uv.lock`` and feeds it to the PURE parsers below.

Pure, unit-testable parsers (no executor / no network / no uv):

* :func:`parse_uv_lock` — ``tomllib``-parse a lock into Package nodes +
  Package->Package ``requires`` edges (carrying optional dependency markers).
* :func:`native_risk_from_lock` — per-package native-build risk: a package with
  an ``sdist`` and **no wheel matching the target platform** ⇒
  ``build_from_source=True`` (also captures the chosen artifact + hash).
* :func:`parse_resolver_error` — structure a failed ``uv lock`` stderr into
  missing packages, version constraints/conflicts, and python-version incompat.

Resilience (no all-or-nothing): when the combined lock fails, the offending
roots are dropped and the lock is retried so the remaining good roots still yield
a graph; the dropped roots are surfaced as ``missing`` Package nodes (+ conflict
edges) with evidence.  A degraded ``uv pip compile`` ``# via`` parse is kept only
as a last-resort fallback when ``uv.lock`` cannot be produced at all.

Implementation note: the three parsing concerns are split into focused modules
(resolve_lock, resolve_errors, resolve_link); this module re-exports all their
public symbols and contains the orchestration layer (resolve_closure,
_pip_compile_fallback) that ties them together.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import tempfile

try:  # tomllib is stdlib on 3.11+; fall back to the tomli backport on 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib

from python_deps.depgraph.executor import Executor

# --------------------------------------------------------------------------- #
# Re-exports from sub-modules (keep every previously-public name importable
# from python_deps.depgraph.resolve without changes at any call site).
# --------------------------------------------------------------------------- #
from python_deps.depgraph.resolve_lock import (
    UV_BIN,
    DEFAULT_TARGET_PLATFORM,
    _DIST_NAME_RE,
    _REQUIREMENT_RE,
    _REQ_NAME_RE,
    _PY_VERSION_RE,
    _LOCAL_SOURCE_KEYS,
    _safe_dist_names,
    _req_name,
    _validate_target_python,
    _canon,
    _marker_env,
    _target_env_for,
    _marker_applies,
    _version_key,
    _entry_applies,
    _select_applicable_packages,
    _prune_to_applicable,
    _package_node,
    _is_local_source,
    parse_uv_lock,
    _artifact_filename,
    _wheel_matches_platform,
    native_risk_from_lock,
)
from python_deps.depgraph.resolve_errors import (
    MissingPackage,
    VersionConstraint,
    VersionConflict,
    PythonIncompat,
    ResolverDiagnosis,
    _REGISTRY_MISS_RE,
    _NO_VERSION_RE,
    _NO_VERSION_PLAIN_RE,
    _YOU_REQUIRE_RE,
    _DEPENDS_ON_RE,
    _PY_INCOMPAT_RE,
    _PY_IMPOSER_BEFORE_RE,
    _PY_IMPOSER_AFTER_RE,
    _BUILD_FAILURE_RE,
    _UNUSABLE_RE,
    _excerpt,
    parse_resolver_error,
    _missing_package_node,
    _conflict_package_node,
    _ROOT_IMPOSER_NAMES,
    _real_imposer,
    _conflict_endpoint_node,
    _conflict_endpoints,
    _diagnosis_to_graph,
    _offending_root_names,
)
from python_deps.depgraph.resolve_link import (
    _stamp,
    _import_edges,
    link_imports_to_packages,
    _merge,
)
from python_deps.depgraph.schema import Edge, EdgeType, Node

# --------------------------------------------------------------------------- #
# Constants local to the orchestration / fallback layer.
# --------------------------------------------------------------------------- #
# Heredoc delimiter for feeding the root requirements on stdin (fallback path).
_HEREDOC = "DEPGRAPH_REQS"

# A pinned line, e.g. ``opencv-python==4.9.0.80`` (fallback path only).
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)")

# Tokens that mark an annotation source rather than a parent distribution.
_SOURCE_FLAGS = {"-r", "-c", "--requirement", "--constraint"}


# --------------------------------------------------------------------------- #
# Orchestrator: throwaway uv project -> uv lock -> parse, with resilience.
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _project_dir(project_dir: str | None):
    if project_dir is not None:
        yield project_dir
    else:
        with tempfile.TemporaryDirectory(prefix="depgraph-uv-") as tmp:
            yield tmp


def _write_pyproject(
    workdir: str,
    dist_names: list[str],
    target_python: str,
) -> None:
    _validate_target_python(target_python)
    deps = ",\n    ".join(f'"{d}"' for d in _safe_dist_names(dist_names))
    content = (
        "[project]\n"
        'name = "depgraph-resolve-root"\n'
        'version = "0.0.0"\n'
        f'requires-python = ">={target_python}"\n'
        "dependencies = [\n"
        f"    {deps}\n"
        "]\n"
    )
    with open(os.path.join(workdir, "pyproject.toml"), "w", encoding="utf-8") as fh:
        fh.write(content)


def _lock_command(
    workdir: str,
    target_python: str,
    exclude_newer: str | None,
) -> str:
    parts = [shlex.quote(UV_BIN), "lock", "--python", shlex.quote(target_python)]
    if exclude_newer:
        parts += ["--exclude-newer", shlex.quote(exclude_newer)]
    return f"cd {shlex.quote(workdir)} && {' '.join(parts)}"


def _read_lock(workdir: str) -> str | None:
    try:
        with open(os.path.join(workdir, "uv.lock"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def resolve_closure(
    roots: list[tuple[str | None, str]],
    host_executor: Executor,
    *,
    target_python: str = "3.11",
    target_platform: str | None = None,
    exclude_newer: str | None = None,
    project_dir: str | None = None,
) -> tuple[list[Node], list[Edge]]:
    """Resolve ``roots`` to a Package closure (nodes + edges) via ``uv.lock``.

    ``roots`` is a list of ``(import_id | None, dist_name)`` pairs (the shape
    produced by ``roots.select_roots`` / ``naming.package_roots``); a ``None``
    import id is a manifest-declared root with no Import node to attach.

    A throwaway uv project is created in a temp dir (or ``project_dir`` when
    injected, for tests), ``uv lock`` is run on the host through
    ``host_executor``, and the resulting ``uv.lock`` is parsed.  On lock failure
    the offending roots are dropped and the lock is retried (per-root
    resilience); the dropped roots are emitted as ``missing``/conflict Package
    nodes with evidence.  If no lock can be produced at all, a degraded
    ``uv pip compile`` ``# via`` parse is used as a last resort.
    """
    platform = target_platform or DEFAULT_TARGET_PLATFORM
    if not roots:
        return [], []

    diag_nodes: list[Node] = []
    diag_edges: list[Edge] = []

    with _project_dir(project_dir) as workdir:
        current = list(roots)
        # Bounded attempts: full set, then progressively fewer roots.
        for _ in range(len(roots) + 1):
            names = [dist for _import_id, dist in current]
            if not names:
                break
            _write_pyproject(workdir, names, target_python)
            result = host_executor.run(
                _lock_command(workdir, target_python, exclude_newer)
            )
            lock_text = _read_lock(workdir) if result.ok else None

            if lock_text:
                try:
                    nodes, edges = parse_uv_lock(lock_text, target_python)
                    risk = native_risk_from_lock(
                        lock_text, platform, target_python
                    )
                    nodes = [
                        _stamp(n, risk, target_python, platform, exclude_newer)
                        for n in nodes
                    ]
                    edges = list(edges) + _import_edges(current, nodes)
                    return _merge(nodes, edges, diag_nodes, diag_edges)
                except tomllib.TOMLDecodeError:
                    # A truncated/corrupt lock (uv exit 0 but partial write on a
                    # disk-full/NFS hiccup) is treated like a missing lock: fall
                    # through to diagnosis/retry instead of crashing the pipeline.
                    pass

            # Lock failed: diagnose and try dropping the offending roots.
            diag = parse_resolver_error(result.stderr)
            dn, de = _diagnosis_to_graph(diag)
            diag_nodes, diag_edges = _merge(diag_nodes, diag_edges, dn, de)

            current_root_names = {_canon(_req_name(r[1])) for r in current}
            offending = _offending_root_names(diag, current_root_names)
            remaining = [
                r for r in current if _canon(_req_name(r[1])) not in offending
            ]
            if not offending or remaining == current:
                break  # cannot make progress by dropping roots.
            current = remaining

        # Last resort: degraded `uv pip compile` `# via` fallback.
        fb_nodes, fb_edges = _pip_compile_fallback(
            roots, host_executor, target_python
        )
        return _merge(fb_nodes, fb_edges, diag_nodes, diag_edges)


# --------------------------------------------------------------------------- #
# Degraded fallback: `uv pip compile` `# via` annotation parse (last resort).
# --------------------------------------------------------------------------- #
def _compile_command(dist_names: list[str], target_python: str) -> str:
    """Build the ``uv pip compile`` shell command (roots fed via heredoc stdin).

    Names are validated (``_safe_dist_names``) so a value containing a newline +
    the heredoc delimiter cannot prematurely terminate the body; ``UV_BIN`` and
    ``target_python`` are quoted so neither can inject a second shell statement.
    """
    _validate_target_python(target_python)
    reqs = "\n".join(_safe_dist_names(dist_names))
    return (
        f"{shlex.quote(UV_BIN)} pip compile "
        f"--python-version {shlex.quote(target_python)} - "
        f"<<'{_HEREDOC}'\n{reqs}\n{_HEREDOC}"
    )


def _parse_parents(text: str) -> list[str]:
    """Parent distribution names from a ``# via ...`` annotation body."""
    tokens = [t for t in re.split(r"[,\s]+", text.strip()) if t]
    parents: list[str] = []
    for tok in tokens:
        if tok in _SOURCE_FLAGS:
            return []  # remainder is a filename/source, not a dependency.
        if tok == "-" or tok.startswith("-"):
            continue
        parents.append(tok)
    return parents


def _parse_closure(stdout: str) -> tuple[list[tuple[str, str]], dict[str, set[str]]]:
    """Parse pinned lines + ``# via`` annotations (fallback path)."""
    packages: list[tuple[str, str]] = []
    via: dict[str, set[str]] = {}
    current_canon: str | None = None
    in_via = False

    for raw in stdout.splitlines():
        stripped = raw.strip()
        if not stripped:
            in_via = False
            continue
        if stripped.startswith("#"):
            body = stripped[1:].strip()
            if body.startswith("via"):
                in_via = True
                for parent in _parse_parents(body[3:]):
                    if current_canon is not None:
                        via.setdefault(current_canon, set()).add(_canon(parent))
            elif in_via:
                for parent in _parse_parents(body):
                    if current_canon is not None:
                        via.setdefault(current_canon, set()).add(_canon(parent))
            else:
                in_via = False
            continue

        in_via = False
        match = _PIN_RE.match(stripped)
        if match:
            name, version = match.group(1), match.group(2)
            packages.append((name, version))
            current_canon = _canon(name)
    return packages, via


def _pip_compile_fallback(
    roots: list[tuple[str | None, str]],
    host_executor: Executor,
    target_python: str,
) -> tuple[list[Node], list[Edge]]:
    """Degraded resolve via ``uv pip compile`` when ``uv.lock`` is unavailable."""
    dist_names = [dist for _import_id, dist in roots]
    if not dist_names:
        return [], []

    result = host_executor.run(_compile_command(dist_names, target_python))
    if not result.ok:
        return [], []

    packages, via = _parse_closure(result.stdout)

    nodes: list[Node] = []
    canon_to_id: dict[str, str] = {}
    for name, version in packages:
        node = _package_node(name, version, provenance="uv pip compile")
        nodes.append(node)
        canon_to_id[_canon(name)] = node.id

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    def _add_edge(src: str, dst: str) -> None:
        if src == dst or (src, dst) in seen:
            return
        seen.add((src, dst))
        edges.append(
            Edge(src=src, dst=dst, relation=EdgeType.REQUIRES, origin="resolver")
        )

    edges.extend(_import_edges(roots, nodes))
    for e in edges:
        seen.add((e.src, e.dst))

    for child_canon, parents in via.items():
        child_id = canon_to_id.get(child_canon)
        if child_id is None:
            continue
        for parent_canon in parents:
            parent_id = canon_to_id.get(parent_canon)
            if parent_id is not None:
                _add_edge(parent_id, child_id)

    return nodes, edges
