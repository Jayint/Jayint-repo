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
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, replace

from python_deps.depgraph.executor import Executor
from python_deps.depgraph.ids import package_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from python_deps.import_mapping import map_import_to_package

# Locked decision 1: the 'uv' binary, invoked (never imported) via the Executor.
# Resolution happens HOST-side (cross-platform resolve needs no container
# interpreter), so resolve from the host PATH; fall back to the bare name so the
# executor's PATH resolves it at run time.
UV_BIN = shutil.which("uv") or "uv"

# Default container target when the caller does not detect/inject one. NEVER
# manylinux2014 (silently downgrades e.g. numpy) — use the modern 2_28 baseline.
DEFAULT_TARGET_PLATFORM = "x86_64-manylinux_2_28"

# Heredoc delimiter for feeding the root requirements on stdin (fallback path).
_HEREDOC = "DEPGRAPH_REQS"

# A pinned line, e.g. ``opencv-python==4.9.0.80`` (fallback path only).
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)")

# A plausible PEP 508 requirement — a distribution name with optional extras and
# an optional version specifier (e.g. ``urllib3<1.21`` / ``numpy>=2,<3``).  The
# allowed alphabet excludes quotes, spaces, newlines, slashes and shell
# metacharacters so nothing untrusted can break out of a TOML string or a heredoc
# body.  Version constraints are kept so the resolver can detect conflicts.
_DIST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"  # distribution name
    r"(?:\[[A-Za-z0-9._,-]+\])?"  # optional extras
    r"[<>=!~,.*+A-Za-z0-9_!-]*$"  # optional version specifier(s)
)
# The bare distribution name at the head of a requirement token (drops the
# extras/specifier) — used for case/separator-insensitive node matching.
_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
# A simple ``X.Y`` / ``X.Y.Z`` python version, for the resolve target.
_PY_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


def _safe_dist_names(dist_names: list[str]) -> list[str]:
    """Keep only injection-safe requirement tokens (name + optional specifier)."""
    return [d for d in dist_names if _REQUIREMENT_RE.match(d)]


def _req_name(token: str) -> str:
    """Bare distribution name of a requirement token (``urllib3<1.21`` -> ``urllib3``)."""
    match = _REQ_NAME_RE.match(token.strip())
    return match.group(1) if match else token.strip()


def _validate_target_python(target_python: str) -> str:
    """Return ``target_python`` if it is a plain version, else raise."""
    if not _PY_VERSION_RE.match(target_python):
        raise ValueError(f"invalid target python version: {target_python!r}")
    return target_python

# Tokens that mark an annotation source rather than a parent distribution.
_SOURCE_FLAGS = {"-r", "-c", "--requirement", "--constraint"}

# uv.lock source kinds that denote the *local* project (the synthetic resolve
# root or a path/editable dependency) rather than an installable distribution.
_LOCAL_SOURCE_KEYS = frozenset({"virtual", "editable", "directory"})


def _canon(name: str) -> str:
    """PEP 503-style normalization for case/separator-insensitive matching."""
    return re.sub(r"[-_.]+", "-", name).lower()


# --------------------------------------------------------------------------- #
# Forked-version resolution: pick the ONE package version applicable to the
# target python when a uv.lock forks a dependency across python markers.
# --------------------------------------------------------------------------- #
def _python_marker_env(target_python: str) -> dict[str, str]:
    """Marker-evaluation environment for ``target_python`` (e.g. ``"3.11"``).

    ``python_version`` keeps two components (``3.11``); ``python_full_version``
    is padded to three (``3.11.0``) so ``python_full_version < '3.12'`` style
    fork markers evaluate correctly.
    """
    parts = [p for p in target_python.split(".") if p]
    version = ".".join(parts[:2]) if len(parts) >= 2 else target_python
    full = ".".join((parts + ["0", "0"])[:3]) if parts else target_python
    return {"python_version": version, "python_full_version": full}


def _marker_applies(marker: str, env: dict[str, str]) -> bool | None:
    """Evaluate a resolution ``marker`` for ``env``; ``None`` if not evaluable.

    ``packaging`` is a near-universal transitive dependency, but its absence (or
    a malformed marker) must not crash the resolver — the caller treats ``None``
    as "unknown" and falls back to a deterministic version pick.
    """
    try:
        from packaging.markers import Marker
    except ImportError:
        return None
    try:
        return bool(Marker(marker).evaluate(env))
    except Exception:
        return None


def _version_key(version: str | None):
    """Sort key for picking the highest version (packaging-aware, str fallback)."""
    if not version:
        return (0,)
    try:
        from packaging.version import Version

        return (1, Version(version))
    except Exception:
        return (0, version)


def _entry_applies(pkg: dict, env: dict[str, str]) -> bool | None:
    """Whether a forked ``[[package]]`` applies under ``env``.

    A package with no ``resolution-markers`` always applies.  Otherwise it
    applies when ANY of its markers evaluates true; ``None`` (unknown) is
    returned only when no marker could be evaluated at all.
    """
    markers = pkg.get("resolution-markers") or []
    if not markers:
        return True
    saw_unknown = False
    for marker in markers:
        verdict = _marker_applies(marker, env)
        if verdict is True:
            return True
        if verdict is None:
            saw_unknown = True
    return None if saw_unknown else False


def _select_applicable_packages(
    raw_packages: list[dict],
    target_python: str | None,
) -> list[dict]:
    """Drop fork duplicates: keep one version per name for ``target_python``.

    When a name appears once, it is kept as-is.  When it forks into multiple
    versions, the version whose ``resolution-markers`` match ``target_python`` is
    kept; ties / unknown markers fall back to the highest version so two versions
    of one distribution are never emitted (which would break ``pip install``).
    With no ``target_python`` the list is returned unchanged (legacy behavior).
    """
    if target_python is None:
        return raw_packages

    env = _python_marker_env(target_python)
    by_canon: dict[str, list[dict]] = {}
    order: list[str] = []
    for pkg in raw_packages:
        name = pkg.get("name")
        if not name:
            continue
        key = _canon(name)
        if key not in by_canon:
            by_canon[key] = []
            order.append(key)
        by_canon[key].append(pkg)

    selected: list[dict] = []
    for key in order:
        group = by_canon[key]
        if len(group) == 1:
            selected.append(group[0])
            continue
        applicable = [p for p in group if _entry_applies(p, env) is True]
        pool = applicable or group
        selected.append(max(pool, key=lambda p: _version_key(p.get("version"))))
    return selected


def _package_node(
    name: str,
    version: str | None,
    *,
    provenance: str = "uv.lock",
    resolvable: bool = True,
) -> Node:
    """A resolver-discovered ``Package`` node (unknown until host-certified).

    ``resolvable=False`` marks an UNRESOLVED placeholder (a root ``uv`` could not
    resolve, or a conflict): it carries NO ``pip:<name>`` fix, because that name
    is exactly what failed to resolve — prescribing ``pip install <name>`` would
    fail (or install a squatter). "No known fix" is the honest signal.
    """
    fix = f"pip:{name}" if resolvable else None
    return Node(
        id=package_id(name, version),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        version=version,
        check_command=f"python -m pip show {name}",
        fix_candidates=(fix,) if resolvable else (),
        chosen_fix=fix,
        provenance=provenance,
    )


# --------------------------------------------------------------------------- #
# Pure parser 1: uv.lock -> Package nodes + Package->Package requires edges.
# --------------------------------------------------------------------------- #
def _is_local_source(source: dict) -> bool:
    """True when a ``[[package]].source`` denotes the local project, not a dist."""
    return any(key in source for key in _LOCAL_SOURCE_KEYS)


def parse_uv_lock(
    text: str,
    target_python: str | None = None,
) -> tuple[list[Node], list[Edge]]:
    """Parse a ``uv.lock`` into Package nodes + Package->Package requires edges.

    Each ``[[package]]`` with a registry/url/git source becomes a ``Package``
    node; local sources (the synthetic resolve root, path/editable deps) are
    skipped.  Each ``dependencies = [{name, marker?}]`` entry becomes a
    parent->child ``requires`` edge carrying the optional dependency ``marker``.

    When ``target_python`` is given and the lock forks a package across python
    ``resolution-markers`` (e.g. ``numpy`` 2.4.6 for ``<3.12`` and 2.5.0 for
    ``>=3.12``), only the version applicable to ``target_python`` is emitted, so
    the closure stays container-accurate and never hands two versions of one
    distribution to ``pip install``.
    """
    data = tomllib.loads(text)
    raw_packages = _select_applicable_packages(
        data.get("package", []), target_python
    )

    nodes: list[Node] = []
    entries: list[tuple[dict, Node]] = []
    canon_to_id: dict[str, str] = {}
    for pkg in raw_packages:
        name = pkg.get("name")
        if not name:
            continue
        if _is_local_source(pkg.get("source", {})):
            continue
        node = _package_node(name, pkg.get("version"))
        nodes.append(node)
        entries.append((pkg, node))
        canon_to_id[_canon(name)] = node.id

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for pkg, node in entries:
        for dep in pkg.get("dependencies", []):
            dep_name = dep.get("name")
            if not dep_name:
                continue
            child_id = canon_to_id.get(_canon(dep_name))
            if child_id is None or child_id == node.id:
                continue
            key = (node.id, child_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                Edge(
                    src=node.id,
                    dst=child_id,
                    relation=EdgeType.REQUIRES,
                    origin="resolver",
                    marker=dep.get("marker"),
                )
            )
    return nodes, edges


# --------------------------------------------------------------------------- #
# Pure parser 2: per-package native-build risk from the lock's artifacts.
# --------------------------------------------------------------------------- #
def _artifact_filename(artifact: dict) -> str | None:
    """Filename of an sdist/wheel lock entry (explicit, or derived from url)."""
    if not isinstance(artifact, dict):
        return None
    name = artifact.get("filename")
    if name:
        return name
    url = artifact.get("url")
    if url:
        return url.rsplit("/", 1)[-1]
    return None


def _wheel_matches_platform(filename: str | None, target_platform: str) -> bool:
    """True when ``filename`` is installable on the (linux) ``target_platform``.

    Universal wheels (``...-none-any.whl``) match every platform.  Otherwise the
    target's arch token (e.g. ``x86_64`` / ``aarch64``) must appear in a *linux*
    platform tag; macOS/Windows wheels never match a linux target.
    """
    if not filename:
        return False
    low = filename.lower()
    if not low.endswith(".whl"):
        return False
    if low.endswith("-none-any.whl"):
        return True
    arch = (target_platform.split("-", 1)[0] if target_platform else "").lower()
    if not arch:
        return False
    if "linux" not in low:  # the target is linux; skip macosx_/win_ wheels.
        return False
    return arch in low


def native_risk_from_lock(
    text: str,
    target_platform: str,
    target_python: str | None = None,
) -> dict[str, dict]:
    """Map ``package name -> {build_from_source, artifact, hash}`` from a lock.

    A package that ships an ``sdist`` but **no wheel matching
    ``target_platform``** must be built from source on the target.  The chosen
    artifact is the matching wheel when one exists, else the sdist.

    ``target_python`` resolves fork duplicates the same way as
    :func:`parse_uv_lock` so the risk for a forked package reflects the version
    actually installed on the target (not whichever version appeared last).
    """
    data = tomllib.loads(text)
    raw_packages = _select_applicable_packages(
        data.get("package", []), target_python
    )
    risk: dict[str, dict] = {}
    for pkg in raw_packages:
        name = pkg.get("name")
        if not name or _is_local_source(pkg.get("source", {})):
            continue
        sdist = pkg.get("sdist")
        wheels = pkg.get("wheels", []) or []

        matching_wheel = next(
            (
                w
                for w in wheels
                if _wheel_matches_platform(_artifact_filename(w), target_platform)
            ),
            None,
        )
        has_sdist = isinstance(sdist, dict) and bool(sdist)
        build_from_source = has_sdist and matching_wheel is None

        if matching_wheel is not None:
            chosen = matching_wheel
        elif has_sdist:
            chosen = sdist
        elif wheels:
            chosen = wheels[0]
        else:
            chosen = None

        risk[name] = {
            "build_from_source": build_from_source,
            "artifact": _artifact_filename(chosen) if chosen else None,
            "hash": chosen.get("hash") if isinstance(chosen, dict) else None,
        }
    return risk


# --------------------------------------------------------------------------- #
# Pure parser 3: failed ``uv lock`` stderr -> structured diagnosis.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MissingPackage:
    name: str
    version: str | None
    evidence: str


@dataclass(frozen=True)
class VersionConstraint:
    package: str  # the constrained package
    specifier: str  # e.g. "<2.0" or ">=2.0"
    imposed_by: str | None  # the package imposing it, or None for the root


@dataclass(frozen=True)
class VersionConflict:
    package: str  # the package under conflict
    left: VersionConstraint
    right: VersionConstraint
    evidence: str


@dataclass(frozen=True)
class PythonIncompat:
    floor: str  # e.g. ">=3.11"
    evidence: str
    imposer: str | None = None  # the package that requires the floor, if known


@dataclass(frozen=True)
class ResolverDiagnosis:
    missing: tuple[MissingPackage, ...] = ()
    constraints: tuple[VersionConstraint, ...] = ()
    conflicts: tuple[VersionConflict, ...] = ()
    python_incompat: PythonIncompat | None = None
    raw: str = ""


# uv wraps long diagnostic lines, so inter-token whitespace may be a newline +
# indent rather than a single space — match runs of whitespace, not literal spaces.
# (Distribution names are never split: uv only wraps at whitespace boundaries.)
_REGISTRY_MISS_RE = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)\s+(?:was|were)\s+not\s+found\s+in\s+the\s+"
    r"(?:package\s+)?registry"
)
_NO_VERSION_RE = re.compile(
    r"there\s+(?:is|are)\s+no\s+versions?\s+of\s+([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s,)]+)"
)
_NO_VERSION_PLAIN_RE = re.compile(
    r"there\s+(?:is|are)\s+no\s+versions?\s+of\s+([A-Za-z0-9][A-Za-z0-9._-]*)(?![=\w.-])"
)
_YOU_REQUIRE_RE = re.compile(
    r"you require ([A-Za-z0-9][A-Za-z0-9._-]*)\s*([<>=!~]=?[0-9][^\s,)]*)"
)
_DEPENDS_ON_RE = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:==[^\s]+)? depends on "
    r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*([<>=!~]=?[0-9][^\s,)]*)"
)
_PY_INCOMPAT_RE = re.compile(
    r"(?:does not satisfy Python|requires Python)\s*(>=?\s*[0-9][0-9.]*)"
)
# The package imposing a python floor: ``X==1.0 requires Python>=3.12`` (form B,
# imposer before the clause) or ``... and you require X`` (form A, after it).
_PY_IMPOSER_BEFORE_RE = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:==[^\s]+)?\s+requires Python"
)
_PY_IMPOSER_AFTER_RE = re.compile(
    r"and you require ([A-Za-z0-9][A-Za-z0-9._-]*)"
)


def _excerpt(text: str, needle_start: int, width: int = 200) -> str:
    """A short evidence window around ``needle_start`` (single-lined)."""
    lo = max(0, needle_start - 20)
    snippet = text[lo : lo + width].strip()
    return " ".join(snippet.split())


def parse_resolver_error(stderr: str) -> ResolverDiagnosis:
    """Structure a failed ``uv lock`` stderr into a :class:`ResolverDiagnosis`.

    Recognizes registry misses, no-such-version, version conflicts (root and
    transitive bounds on a shared package), and python-version incompatibility.
    """
    text = stderr or ""

    missing: list[MissingPackage] = []
    seen_missing: set[str] = set()

    def _add_missing(name: str, version: str | None, start: int) -> None:
        key = _canon(name)
        if key in seen_missing:
            return
        seen_missing.add(key)
        missing.append(MissingPackage(name, version, _excerpt(text, start)))

    for m in _REGISTRY_MISS_RE.finditer(text):
        _add_missing(m.group(1), None, m.start())
    for m in _NO_VERSION_RE.finditer(text):
        _add_missing(m.group(1), m.group(2), m.start())
    for m in _NO_VERSION_PLAIN_RE.finditer(text):
        _add_missing(m.group(1), None, m.start())

    constraints: list[VersionConstraint] = []
    for m in _YOU_REQUIRE_RE.finditer(text):
        constraints.append(VersionConstraint(m.group(1), m.group(2), None))
    for m in _DEPENDS_ON_RE.finditer(text):
        constraints.append(VersionConstraint(m.group(2), m.group(3), m.group(1)))

    # A conflict = a single package carrying >=2 distinct specifiers.
    by_pkg: dict[str, list[VersionConstraint]] = {}
    for c in constraints:
        by_pkg.setdefault(_canon(c.package), []).append(c)
    conflicts: list[VersionConflict] = []
    for cons in by_pkg.values():
        specs = {c.specifier for c in cons}
        if len(cons) >= 2 and len(specs) >= 2:
            # Pick two constraints with DISTINCT specifiers (uv may restate the
            # same bound several times across wrapped lines, so cons[0]/cons[1]
            # are not reliably the two conflicting bounds).  Prefer a pair whose
            # imposers also differ so the edge joins two real distributions.
            left = cons[0]
            right = next(
                (c for c in cons[1:] if c.specifier != left.specifier
                 and _real_imposer(c.imposed_by) != _real_imposer(left.imposed_by)),
                None,
            )
            if right is None:
                right = next(c for c in cons[1:] if c.specifier != left.specifier)
            conflicts.append(
                VersionConflict(
                    package=left.package,
                    left=left,
                    right=right,
                    evidence=_excerpt(text, text.find(left.package)),
                )
            )

    python_incompat: PythonIncompat | None = None
    py = _PY_INCOMPAT_RE.search(text)
    if py:
        floor = re.sub(r"\s+", "", py.group(1))
        before = _PY_IMPOSER_BEFORE_RE.search(text)
        after = _PY_IMPOSER_AFTER_RE.search(text)
        imposer = before.group(1) if before else (after.group(1) if after else None)
        python_incompat = PythonIncompat(floor, _excerpt(text, py.start()), imposer)

    return ResolverDiagnosis(
        missing=tuple(missing),
        constraints=tuple(constraints),
        conflicts=tuple(conflicts),
        python_incompat=python_incompat,
        raw=text,
    )


# --------------------------------------------------------------------------- #
# Diagnosis -> graph (missing/conflict nodes + conflicts_with edges).
# --------------------------------------------------------------------------- #
def _missing_package_node(name: str, version: str | None, evidence: str) -> Node:
    return replace(
        _package_node(name, version, provenance="uv lock (unresolved)", resolvable=False),
        state=State.MISSING,
        evidence=evidence,
    )


def _conflict_package_node(name: str, evidence: str) -> Node:
    return replace(
        _package_node(name, None, provenance="uv lock (conflict)", resolvable=False),
        state=State.UNKNOWN,
        evidence=evidence,
    )


# Synthetic resolve-root labels uv uses for the root requirements ("your project
# depends on ...", "the root project ...").  These are NOT real distributions and
# must never leak in as Package nodes; treat them like the unnamed root (None).
_ROOT_IMPOSER_NAMES: frozenset[str] = frozenset({"project", "root"})


def _real_imposer(imposer: str | None) -> str | None:
    """An imposing distribution name, or ``None`` for the synthetic resolve root."""
    if imposer is None or _canon(imposer) in _ROOT_IMPOSER_NAMES:
        return None
    return imposer


def _conflict_endpoint_node(name: str, c: VersionConflict) -> Node:
    """A Package node for a conflict endpoint.

    The shared package (``c.package``) has no version satisfying every bound, so it
    is ``MISSING``; an imposing distribution resolves fine on its own and stays
    ``UNKNOWN`` (only the *combination* is unsatisfiable).
    """
    if _canon(name) == _canon(c.package):
        return _missing_package_node(name, None, c.evidence)
    return _conflict_package_node(name, c.evidence)


def _conflict_endpoints(c: VersionConflict) -> tuple[str | None, str | None]:
    """Two distinct Package names to join with a conflicts_with edge.

    A synthetic-root imposer (``None``/"your project") falls back to the shared
    package, so a "package P needs D>=x but the root pins D<x" conflict joins the
    real P and the shared D (never the synthetic root).
    """
    a = _real_imposer(c.left.imposed_by) or c.package
    b = _real_imposer(c.right.imposed_by) or c.package
    if a == b:
        imposers = [
            x
            for x in (_real_imposer(c.left.imposed_by), _real_imposer(c.right.imposed_by))
            if x
        ]
        if not imposers:
            return None, None
        a, b = imposers[0], c.package
        if a == b:
            return None, None
    return a, b


def _diagnosis_to_graph(diag: ResolverDiagnosis) -> tuple[list[Node], list[Edge]]:
    nodes: list[Node] = []
    seen_ids: set[str] = set()

    def _add(node: Node) -> Node:
        if node.id not in seen_ids:
            seen_ids.add(node.id)
            nodes.append(node)
        return node

    edges: list[Edge] = []

    for m in diag.missing:
        _add(_missing_package_node(m.name, m.version, m.evidence))

    for c in diag.conflicts:
        a, b = _conflict_endpoints(c)
        if a is None or b is None:
            continue
        na = _add(_conflict_endpoint_node(a, c))
        nb = _add(_conflict_endpoint_node(b, c))
        edges.append(
            Edge(
                src=na.id,
                dst=nb.id,
                relation=EdgeType.CONFLICTS_WITH,
                origin="resolver",
                data={
                    "package": c.package,
                    "src_bound": c.left.specifier,
                    "dst_bound": c.right.specifier,
                    "evidence": c.evidence,
                },
            )
        )

    if diag.python_incompat is not None:
        incompat = diag.python_incompat
        floor = incompat.floor
        py_node = _add(
            Node(
                id="pkg:python",
                type=NodeType.PACKAGE,
                name="python",
                layer=Layer.INTERPRETER,
                discovered_by=DiscoveredBy.RESOLVER,
                version=floor,
                state=State.MISSING,
                evidence=incompat.evidence,
                provenance="uv lock (python incompat)",
            )
        )
        # Conflict edge to the interpreter need with the floor (spec §"Conflict/
        # failure → graph"): the imposing package conflicts_with the interpreter.
        if incompat.imposer and _canon(incompat.imposer) != _canon(py_node.name):
            imposer_node = _add(
                _conflict_package_node(incompat.imposer, incompat.evidence)
            )
            edges.append(
                Edge(
                    src=imposer_node.id,
                    dst=py_node.id,
                    relation=EdgeType.CONFLICTS_WITH,
                    origin="resolver",
                    data={
                        "package": "python",
                        "floor": floor,
                        "dst_bound": floor,
                        "evidence": incompat.evidence,
                    },
                )
            )

    return nodes, edges


def _offending_root_names(diag: ResolverDiagnosis) -> set[str]:
    """Canonical names of packages implicated by a lock failure."""
    names: set[str] = {_canon(m.name) for m in diag.missing}
    for c in diag.conflicts:
        names.add(_canon(c.package))
        for imposer in (_real_imposer(c.left.imposed_by), _real_imposer(c.right.imposed_by)):
            if imposer:
                names.add(_canon(imposer))
    return names


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


def _stamp(
    node: Node,
    risk: dict[str, dict],
    target_python: str,
    target_platform: str,
    exclude_newer: str | None = None,
) -> Node:
    """Stamp targeting provenance + native-build risk onto a Package node."""
    changes: dict = {
        "resolved_python": target_python,
        "resolved_platform": target_platform,
        "exclude_newer": exclude_newer,
    }
    info = risk.get(node.name) or risk.get(_canon(node.name))
    if info is None:
        # Case/separator-insensitive fallback.
        for key, val in risk.items():
            if _canon(key) == _canon(node.name):
                info = val
                break
    if info is not None:
        changes["build_from_source"] = info.get("build_from_source")
        changes["artifact"] = info.get("artifact")
        changes["hash"] = info.get("hash")
    return replace(node, **changes)


def _import_edges(
    roots: list[tuple[str | None, str]],
    nodes: list[Node],
) -> list[Edge]:
    """Import->Package edges for each root with a resolved Package node."""
    canon_to_id = {_canon(n.name): n.id for n in nodes}
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for import_id_, dist in roots:
        if import_id_ is None:
            continue  # manifest-declared root: no Import node to attach.
        pkg_id = canon_to_id.get(_canon(_req_name(dist)))
        if pkg_id is None:
            continue
        key = (import_id_, pkg_id)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            Edge(
                src=import_id_,
                dst=pkg_id,
                relation=EdgeType.REQUIRES,
                origin="resolver",
            )
        )
    return edges


def link_imports_to_packages(graph: DepGraph) -> DepGraph:
    """Connect every Import node to its resolved Package by canonical dist name.

    Complements :func:`_import_edges`, which only links imports that were
    themselves resolver roots.  A manifest-declared dependency seeds a Package via
    a root with ``import_id=None`` (see ``roots.select_roots``), so its scanned
    Import node would otherwise be orphaned from the Package — breaking the
    symptom->owner walk.  This pass links any Import whose mapped distribution
    matches a Package node, regardless of how the root was sourced.  ``_canon``
    collapses ``_``/``-``/``.`` so e.g. ``charset_normalizer`` matches
    ``charset-normalizer`` even via the identity fallback.
    """
    canon_to_pkg = {
        _canon(n.name): n.id for n in graph.nodes if n.type is NodeType.PACKAGE
    }
    existing = {
        (e.src, e.dst) for e in graph.edges if e.relation is EdgeType.REQUIRES
    }
    new = graph
    for node in graph.nodes:
        if node.type is not NodeType.IMPORT:
            continue
        dist = map_import_to_package(node.name).package_name
        pkg_id = canon_to_pkg.get(_canon(dist))
        if pkg_id is None or (node.id, pkg_id) in existing:
            continue
        new = new.with_edge(
            Edge(
                src=node.id,
                dst=pkg_id,
                relation=EdgeType.REQUIRES,
                origin="reconcile",
            )
        )
    return new


def _merge(
    primary_nodes: list[Node],
    primary_edges: list[Edge],
    extra_nodes: list[Node],
    extra_edges: list[Edge],
) -> tuple[list[Node], list[Edge]]:
    """Merge node/edge lists; primary entries win on id/edge-key collisions."""
    nodes: list[Node] = list(primary_nodes)
    have_ids = {n.id for n in nodes}
    for n in extra_nodes:
        if n.id not in have_ids:
            have_ids.add(n.id)
            nodes.append(n)

    edges: list[Edge] = list(primary_edges)
    have_keys = {e.key() for e in edges}
    for e in extra_edges:
        if e.key() not in have_keys:
            have_keys.add(e.key())
            edges.append(e)
    return nodes, edges


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

            offending = _offending_root_names(diag)
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
