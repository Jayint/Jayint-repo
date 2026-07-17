"""Stage 3 — resolver v2: ``uv.lock``-driven Package closure.

Primary resolve source is **``uv.lock``** (the richest single uv artifact: nodes
+ versions + transitive edges + markers + sdist/wheel artifacts).  The orchestrator
(:func:`resolve_closure`) creates a throwaway uv project in a temp dir, runs
``uv lock --python <target>`` (a UNIVERSAL, cross-platform lock -- ``uv lock``
has no ``--python-platform`` flag) *on the host* through the injected
``Executor`` (locked decision 1: the ``uv`` binary is invoked, never imported),
reads the produced ``uv.lock`` and feeds it to the PURE parsers below, which
target the container's PLATFORM at parse time.

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
import glob
import logging
import os
import re
import shlex
import tempfile
import time
from types import MappingProxyType
from typing import Mapping

try:  # tomllib is stdlib on 3.11+; fall back to the tomli backport on 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib

from graph.contracts.executor import Executor
from graph.python.read.target_env import TargetEnv

# --------------------------------------------------------------------------- #
# Re-exports from sub-modules (keep every previously-public name importable
# from graph.python.lanes.install.resolve without changes at any call site).
# --------------------------------------------------------------------------- #
from graph.python.lanes.install.resolve_lock import (
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
    _non_default_source_evidence,
    _missing_source_node,
    parse_uv_lock,
    _artifact_filename,
    _wheel_matches_platform,
    native_risk_from_lock,
)
from graph.python.lanes.install.resolve_errors import (
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
from graph.python.lanes.install.resolve_link import (
    _stamp,
    _import_edges,
    link_imports_to_packages,
    _merge,
)
from graph.schema import (
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)

logger = logging.getLogger(__name__)

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
# FIX 4 (B6) — wall-clock budget for the whole drop-retry ladder.
#
# The ladder was previously bounded ONLY by attempt count
# (``len(roots) + 1``), fully serial, with each attempt a real `uv lock`
# subprocess. PostHog (263 roots) burned 2,224s of a 2,400s pipeline budget
# grinding through it without ever converging, and returned zero packages.
# Env-overridable so a caller with a tighter/looser overall budget can tune
# it without a code change; a garbage/absent override falls back to the
# default rather than crashing the resolve.
# --------------------------------------------------------------------------- #
_DEFAULT_RESOLVE_LADDER_BUDGET_S = 300.0
_RESOLVE_LADDER_BUDGET_ENV = "DEPGRAPH_RESOLVE_LADDER_BUDGET_S"

# FIX 4 (B6) continued -- the wall-clock budget above bounds the LADDER as a
# whole, but nothing previously bounded a SINGLE `uv lock`/`uv pip compile`
# subprocess: every ``host_executor.run(...)`` call in this module omitted
# `timeout=`, so it silently fell back to the Executor Protocol's fixed 300s
# default (executor.py:35) -- one hung/slow attempt could burn the entire
# remaining budget (or, early in a very long overall budget, up to this cap
# regardless of how much budget was actually left). Every call site below
# passes `timeout=min(<remaining budget>, PER_ATTEMPT_CAP_S)` instead.
PER_ATTEMPT_CAP_S = 300.0


def _resolve_ladder_budget_seconds() -> float:
    raw = os.environ.get(_RESOLVE_LADDER_BUDGET_ENV)
    if not raw:
        return _DEFAULT_RESOLVE_LADDER_BUDGET_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_RESOLVE_LADDER_BUDGET_S
    return value if value > 0 else _DEFAULT_RESOLVE_LADDER_BUDGET_S


def _log_ladder_exhaustion(
    roots: list[tuple[str | None, str]],
    current: list[tuple[str | None, str]],
    budget_s: float,
    budget_exceeded: bool,
) -> None:
    """Log (never silently swallow) any root the ladder dropped or never got
    to try before falling back to the degraded ``uv pip compile`` path.

    FIX 4 (B6), item 3: previously this returned early whenever
    ``dropped_or_untried`` was empty -- but a wall-clock-budget exhaustion can
    happen with NOTHING dropped (e.g. the very first attempt's timeout stderr
    matches none of ``parse_resolver_error``'s regexes, so no root is ever
    attributable/droppable): that was a resolve that burned its whole budget
    and produced zero packages with zero log line. ``budget_exceeded`` is now
    an independent trigger, on top of the original ``dropped_or_untried``
    condition (never removed -- a normal drop-retry exhaustion still logs)."""
    root_names = {_canon(_req_name(r[1])) for r in roots}
    current_names = {_canon(_req_name(r[1])) for r in current}
    dropped_or_untried = sorted(root_names - current_names)
    if not dropped_or_untried and not budget_exceeded:
        return
    reason = (
        f"wall-clock budget ({budget_s}s) exceeded"
        if budget_exceeded
        else "drop-retry ladder exhausted its attempts"
    )
    logger.warning(
        "resolve_closure: %s with %d/%d root(s) dropped/untried; falling back "
        "to `uv pip compile` over the %d surviving root(s). Dropped/untried: %s",
        reason,
        len(dropped_or_untried),
        len(roots),
        len(current),
        dropped_or_untried,
    )


# Stable id/name for the degraded node below -- one per ``resolve_closure``
# call (never per-package: the budget is exhausted for the WHOLE ladder, not
# any one root), so it collapses onto a single node across repair rounds.
_BUDGET_EXHAUSTED_NODE_ID = "resolve:budget_exhausted"


def _budget_exhausted_node(
    roots: list[tuple[str | None, str]],
    current: list[tuple[str | None, str]],
    budget_s: float,
) -> Node:
    """A degraded, graph-visible marker for a wall-clock-budget-exhausted
    ladder (FIX 4 / B6, item 3) -- so a resolve that burned its whole budget
    and produced nothing is never invisible to anything that reads only the
    graph (unlike a bare log line, which a downstream consumer may not read).

    Must be un-installable AND un-certifiable, by construction (the same two
    belts already used by ``build._excluded_uv_source_node`` /
    ``resolve_lock._missing_source_node`` elsewhere in this codebase):

    * ``version=None`` -- ``emit._is_emittable`` refuses any Package node
      with ``not node.version`` before it ever reaches its (separately
      known-blind) ``uninstallable`` check.
    * ``check_command=None`` -- ``certify()`` short-circuits on
      ``if node is None or not node.check_command: return graph``, so NO
      check ever runs for this node; ``certify()`` flips MISSING -> SATISFIED
      off ANY rc-0 check, so never giving it a check_command at all is the
      only way to guarantee that can never happen.
    * ``data['uninstallable']=True`` -- defense in depth for the renderer's
      own "cannot install" gate (``emit._is_reciped`` / ``build_script.py``).
    """
    root_names = sorted({_canon(_req_name(r[1])) for r in roots})
    current_names = {_canon(_req_name(r[1])) for r in current}
    untried = sorted(n for n in root_names if n not in current_names)
    evidence = (
        f"resolve_closure: wall-clock budget ({budget_s}s) exhausted before "
        "the drop-retry ladder converged on a lock or exhausted its own "
        f"attempt bound; {len(untried)}/{len(root_names)} root(s) were "
        f"dropped or never got a certified outcome this call: {untried}. "
        "See the resolve_closure log for the per-attempt timing."
    )
    return Node(
        id=_BUDGET_EXHAUSTED_NODE_ID,
        type=NodeType.PACKAGE,
        name=_BUDGET_EXHAUSTED_NODE_ID,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        version=None,
        check_command=None,
        fix_candidates=(),
        chosen_fix=None,
        provenance="uv lock (wall-clock budget exhausted)",
        state=State.MISSING,
        evidence=evidence,
        data={"uninstallable": True, "budget_seconds": budget_s},
    )


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


# Injection-safe optional-dependency group name (a bare TOML table key: no
# quotes, brackets, newlines or whitespace).
_SAFE_GROUP_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_group_names(names) -> list[str]:
    """Sorted, injection-safe extras-group names (bare TOML table keys only)."""
    return sorted(n for n in names if isinstance(n, str) and _SAFE_GROUP_RE.match(n))


# --------------------------------------------------------------------------- #
# Task 5 -- ``[tool.uv.sources]`` / ``[[tool.uv.index]]`` rendering into the
# synthetic pyproject. Minimal, hand-rolled TOML writer (no `tomli_w`
# dependency in this codebase): every value that reaches these helpers is
# either a plain string or a bool (uv source-spec dicts only ever carry
# those), so a small scalar/inline-table/dotted-key renderer is enough --
# and keeping it hand-rolled (matching the rest of `_write_pyproject`, which
# already builds TOML by string concatenation) avoids a new dependency.
# --------------------------------------------------------------------------- #
# Safe bare TOML key alphabet (dotted-key path segments / table names). A name
# outside this set is rendered as a quoted key segment instead (still valid
# TOML) rather than risking a mis-parsed dotted path (a literal ``.`` in a
# BARE key would otherwise be read as a nested-table separator).
_SAFE_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_string(value: str) -> str:
    """A double-quoted TOML basic string, with the four control chars that
    matter for a single-line string escaped (backslash/quote must be escaped
    or the string breaks; newline/CR/tab are escaped defensively so a value
    drawn from repo-controlled TOML can never inject a literal line break
    into the generated file -- a raw newline inside a TOML basic string is a
    parse error, not a silent truncation, so the worst case without this is
    still a loud `uv lock` failure, never silent misbehavior)."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _toml_key(name: str) -> str:
    """A single dotted-key path segment: bare when safe, else quoted."""
    return name if _SAFE_TOML_KEY_RE.match(name) else _toml_string(name)


def _toml_scalar(value: object) -> str:
    """A TOML scalar literal for a uv source-spec value (str/bool only, in
    practice; anything else is defensively stringified rather than raising,
    since a malformed value should degrade the ONE affected entry, never
    crash the whole resolve)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (int, float)):
        return str(value)
    return _toml_string(str(value))


def _toml_inline_table(entry: dict) -> str:
    parts = [f"{_toml_key(str(k))} = {_toml_scalar(v)}" for k, v in entry.items()]
    return "{ " + ", ".join(parts) + " }"


def _render_uv_sources(uv_sources: "Mapping[str, tuple[dict, ...]]") -> str:
    """``[tool.uv.sources]`` entries for the synthetic pyproject.

    A single-entry spec renders as a top-level dotted-key assignment
    (``tool.uv.sources.<name> = { ... }``); a marker-conditional LIST of
    specs (uv's multi-source form) renders as one ``[[tool.uv.sources.<name>]]``
    array-of-tables block per entry. Neither form needs an explicit
    ``[tool.uv.sources]`` header (TOML infers the intermediate table from the
    dotted path either way), so the two forms freely mix across names, and
    sorted iteration keeps the output deterministic.
    """
    lines: list[str] = []
    for name in sorted(uv_sources):
        entries = uv_sources[name]
        if not entries:
            continue
        key = _toml_key(name)
        if len(entries) == 1:
            lines.append(f"tool.uv.sources.{key} = {_toml_inline_table(entries[0])}\n")
            continue
        for entry in entries:
            lines.append(f"[[tool.uv.sources.{key}]]\n")
            for k, v in entry.items():
                lines.append(f"{_toml_key(str(k))} = {_toml_scalar(v)}\n")
    return "".join(lines)


def _render_uv_indexes(uv_indexes: tuple[dict, ...]) -> str:
    """``[[tool.uv.index]]`` array-of-tables blocks, one per captured index."""
    lines: list[str] = []
    for entry in uv_indexes:
        lines.append("[[tool.uv.index]]\n")
        for k, v in entry.items():
            lines.append(f"{_toml_key(str(k))} = {_toml_scalar(v)}\n")
    return "".join(lines)


def _write_pyproject(
    workdir: str,
    dist_names: list[str],
    target_python: str,
    *,
    extras: frozenset[str] = frozenset(),
    uv_sources: "Mapping[str, tuple[dict, ...]]" = MappingProxyType({}),
    uv_indexes: tuple[dict, ...] = (),
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
    if extras:
        # The chosen groups' own requirement bodies already flow into
        # `dist_names` above -- roots.select_roots (Task 8) gates a group by
        # `needed_extras` and, when selected, flattens its members straight
        # into the plain root list, so they are already being resolved via
        # `dependencies`. This table is a provenance record of which
        # optional-dependency groups were IN SCOPE for this resolve (visible
        # in the produced uv.lock's root-package metadata for debugging/
        # traceability), not a second resolution path -- declaring the same
        # requirement under two different TOML keys would be redundant.
        content += "\n[project.optional-dependencies]\n"
        for group in _safe_group_names(extras):
            content += f"{group} = []\n"
    # Task 5: carry `[tool.uv.sources]` / `[[tool.uv.index]]` into the
    # synthetic pyproject -- ``uv_sources``/``uv_indexes`` are ALREADY
    # rewritten/round-filtered by the caller (resolve_closure): only names
    # that are (a) actual roots THIS ladder round and (b) honorable (path/
    # workspace resolved to a real directory) ever reach here, so an empty
    # mapping/tuple (every existing caller) reproduces the exact prior
    # output byte-for-byte.
    if uv_sources:
        content += "\n" + _render_uv_sources(uv_sources)
    if uv_indexes:
        content += "\n" + _render_uv_indexes(uv_indexes)
    with open(os.path.join(workdir, "pyproject.toml"), "w", encoding="utf-8") as fh:
        fh.write(content)


def _lock_command(
    workdir: str,
    target_python: str,
    exclude_newer: str | None,
) -> str:
    """Build the ``uv lock`` shell command.

    ``uv.lock`` is a UNIVERSAL, cross-platform lock -- it is not generated for
    one target platform, so ``uv lock`` (unlike ``uv pip compile`` / ``uv
    export``) does not accept ``--python-platform``; passing it makes ``uv``
    reject the whole command (``error: unexpected argument '--python-platform'
    found``), which silently zeroes out every resolve. Platform targeting
    happens downstream at PARSE time instead: ``parse_uv_lock``/
    ``native_risk_from_lock`` evaluate each lock entry's PEP 508 markers and
    wheel tags against the caller-supplied ``target_platform``/``target_env``,
    so the container's platform is honored without ever needing the lock
    command itself to know about it.
    """
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


# --------------------------------------------------------------------------- #
# Task 5 -- rewrite `[tool.uv.sources]` specs for the synthetic (temp-dir)
# pyproject. `workspace = true` and a relative `path = "../x"` resolve
# relative to the REAL repo/workspace root in uv's own semantics; the
# synthetic project lives in a throwaway temp dir, so those specs are
# rewritten to an ABSOLUTE path against the real repo root rather than
# relocating the synthetic project itself. git/url/index specs are already
# self-contained and pass through verbatim.
# --------------------------------------------------------------------------- #
def _read_local_project_name(dir_path: str) -> str | None:
    """``[project].name`` from ``dir_path/pyproject.toml``, or ``None``.

    Guarded exactly like every other ad-hoc TOML read in this codebase: a
    missing file, unparseable TOML, or an unexpected shape yields ``None``
    rather than raising -- a workspace member with no ``[project].name`` (or
    no pyproject at all) just falls back to matching by directory basename.
    """
    pyproject = os.path.join(dir_path, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return None
    try:
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    name = (data.get("project") or {}).get("name")
    return name if isinstance(name, str) else None


def _find_workspace_member_path(
    repo_path: str, workspace_members: tuple[str, ...], dist_name: str
) -> str | None:
    """Absolute path of the on-disk workspace member distributing ``dist_name``.

    ``workspace_members`` are GLOB patterns (uv's ``[tool.uv.workspace].members``
    -- e.g. ``"packages/*"``, or a literal single-directory pattern like
    ``"tools/hogli"``), expanded against the real checkout (``repo_path``),
    never the synthetic temp dir. Each matching directory is checked against
    ``dist_name`` (canonicalized) by its OWN declared ``[project].name`` when
    it has one -- a member WITH a readable declared name is judged SOLELY by
    it, never falling back to directory basename on a mismatch (a directory
    ``packages/hogli`` whose own ``pyproject.toml`` declares
    ``name = "other"`` is NOT a match for ``hogli``, even though its basename
    is). Only a member with NO readable declared name (missing/unparseable
    ``pyproject.toml``) falls back to matching by directory basename.

    Returns ``None`` when no glob match resolves to a real, name-matching
    directory, OR when MORE THAN ONE candidate matches -- an ambiguous match
    is never resolved by "pick the first" (that would make the outcome
    silently dependent on filesystem/glob order); the caller treats either
    case as "cannot be honored" (Part B: a degraded MISSING node, never a
    silent guess).
    """
    target = _canon(dist_name)
    matches: list[str] = []
    for pattern in workspace_members:
        pattern_path = pattern if os.path.isabs(pattern) else os.path.join(repo_path, pattern)
        for candidate in sorted(glob.glob(pattern_path)):
            if not os.path.isdir(candidate):
                continue
            member_name = _read_local_project_name(candidate)
            if member_name is not None:
                if _canon(member_name) == target:
                    matches.append(os.path.abspath(candidate))
                continue  # declared name present but mismatched -- never fall
                          # through to a basename guess for THIS candidate.
            if _canon(os.path.basename(candidate.rstrip(os.sep))) == target:
                matches.append(os.path.abspath(candidate))
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


def _rewrite_uv_source_entries(
    name: str,
    entries: tuple[dict, ...],
    repo_path: str,
    workspace_members: tuple[str, ...],
) -> tuple[tuple[dict, ...], Node | None]:
    """Rewrite one distribution's ``[tool.uv.sources]`` entries for the
    synthetic pyproject; returns ``(rewritten_entries, missing_node)``.

    ``missing_node`` is non-``None`` exactly when a source cannot be honored
    (a ``workspace``/``path`` entry that does not resolve to a real directory
    on disk) -- the caller must drop ``name`` from this round's roots instead
    of ever handing the bare name to ``uv`` (which would silently resolve an
    unrelated PyPI package of the same name) and surface the returned node
    (Part B: degrade the certificate, never drop silently).

    ``git``/``url``/index-only entries are already self-contained and pass
    through verbatim; ``marker``/``extra`` keys on any entry are preserved
    untouched throughout.
    """
    rewritten: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if "workspace" in entry:
            member_path = _find_workspace_member_path(repo_path, workspace_members, name)
            if member_path is None:
                evidence = (
                    f"[tool.uv.sources] '{name}' declares workspace = true but no "
                    f"member directory matching workspace glob(s) "
                    f"{list(workspace_members)!r} was found on disk under {repo_path!r}"
                )
                return (), _missing_package_node(name, None, evidence)
            new_entry = {k: v for k, v in entry.items() if k != "workspace"}
            new_entry["path"] = member_path
            new_entry["editable"] = True
            rewritten.append(new_entry)
        elif "path" in entry:
            raw_path = entry["path"]
            abs_path = (
                raw_path
                if os.path.isabs(raw_path)
                else os.path.normpath(os.path.join(repo_path, raw_path))
            )
            if not os.path.isdir(abs_path):
                evidence = (
                    f"[tool.uv.sources] '{name}' path {raw_path!r} resolved to "
                    f"{abs_path!r}, which is not a directory on disk"
                )
                return (), _missing_package_node(name, None, evidence)
            new_entry = dict(entry)
            new_entry["path"] = abs_path
            rewritten.append(new_entry)
        else:
            # git / url / index / marker-only -- already self-contained.
            rewritten.append(dict(entry))
    return tuple(rewritten), None


def resolve_closure(
    roots: list[tuple[str | None, str]],
    host_executor: Executor,
    *,
    target_env: TargetEnv,
    exclude_newer: str | None = None,
    project_dir: str | None = None,
    extras: frozenset[str] = frozenset(),
    audit_root_names: frozenset[str] = frozenset(),
    excluded_dist_names: frozenset[str] = frozenset(),
    uv_sources: "Mapping[str, tuple[dict, ...]]" = MappingProxyType({}),
    uv_indexes: tuple[dict, ...] = (),
    workspace_members: tuple[str, ...] = (),
    repo_path: str | None = None,
) -> tuple[list[Node], list[Edge]]:
    """Resolve ``roots`` to a Package closure (nodes + edges) via ``uv.lock``.

    ``roots`` is a list of ``(import_id | None, dist_name)`` pairs (the shape
    produced by ``roots.select_roots`` / ``naming.package_roots``); a ``None``
    import id is a manifest-declared root with no Import node to attach.

    ``target_env`` is the single :class:`TargetEnv` (Task 7) the whole resolve
    honors: ``target_env.python_version`` drives ``uv lock --python`` (the
    ONLY targeting flag ``uv lock`` itself accepts -- ``uv.lock`` is a
    universal, cross-platform lock, so there is no ``--python-platform`` for
    ``uv lock`` to take). ``target_env.python_platform_tag`` (the NORMALIZED
    wheel/uv tag) instead drives the wheel-artifact match at PARSE time, while
    ``target_env`` ITSELF (carrying the RAW ``platform.machine()`` the
    container reported) is threaded into ``parse_uv_lock``/
    ``native_risk_from_lock`` so every PEP 508 marker evaluated against a
    forked/conditional lock entry sees the container's own facts — never a
    normalized stand-in, and never the host running this resolve.

    ``extras`` is the set of ``[project.optional-dependencies]`` / extras_require
    group names IN SCOPE for this resolve (Task 8's targeted-extras fix — the
    caller, typically ``roots.select_roots(..., needed_extras=...)`` upstream
    of here, has already gated which groups' members are present in ``roots``
    at all). It is written into the temp pyproject's own
    ``[project.optional-dependencies]`` table as a provenance record of which
    groups were considered (see :func:`_write_pyproject`); the groups'
    requirement bodies themselves reach the resolver via ``roots`` /
    ``dist_names``, not through this table.

    ``audit_root_names`` (P1.4 Correction 2a) is the set of canonical names of
    roots the Phase-A repair fixpoint *added* (``DiscoveredBy.AUDIT``). It is
    threaded into the drop-retry's :func:`_offending_root_names` so a conflict
    prefers dropping a repaired root over a manifest-declared one — a repaired
    root must never evict a declared dependency. Default empty = today's behavior.

    ``excluded_dist_names`` (FIX 3 / C1) is a set of distribution names (any
    case/separator form; canonicalized internally) that must NEVER be written
    into the synthetic resolve-root pyproject as if they were ordinary PyPI
    names — e.g. a ``[tool.uv.sources]`` workspace member or a git-pinned fork
    uv can only resolve through a non-PyPI source. Writing such a name into
    the throwaway pyproject makes ``uv lock`` fail for the WHOLE root set (a
    single unresolvable bare name can collapse hundreds of otherwise-good
    roots — the PostHog/posthog ``hogli`` case). Filtered out up front, before
    the drop-retry ladder ever runs. Default empty set is a no-op for every
    existing caller.

    ``uv_sources`` (Task 5 -- ``PythonDependencyEvidence.uv_sources``, canonical
    dist name -> tuple of verbatim ``[tool.uv.sources]`` spec dicts) is what lets
    the names ``excluded_dist_names`` used to exist for become resolvable roots
    again instead of dropped: each entry is rewritten for the synthetic temp-dir
    project (``workspace = true`` / a relative ``path`` -> an ABSOLUTE path
    against ``repo_path``, resolved via :func:`_find_workspace_member_path`;
    ``git``/``url``/``index`` entries pass through verbatim) and written into
    EVERY round's pyproject via :func:`_write_pyproject` -- ``[tool.uv.sources]``
    is a GLOBAL override table in uv's own semantics (it applies to any
    package name anywhere in the resolved graph, not just the direct roots),
    so it is never re-filtered down to "names still a root this round": a
    root the drop-retry ladder shrinks away may still be a transitive
    dependency of a surviving root, and that dependency's source override
    must keep applying (Gate 4 — round-filtering this was tried and was
    wrong: it let a later round silently resolve a dropped root's PUBLIC PyPI
    namesake for whichever surviving root still depended on it). A source
    that cannot be honored (a workspace/path member not on
    disk) is never handed to ``uv`` as a bare name (which would silently
    resolve an unrelated same-named PyPI package instead) -- it is dropped from
    every round's roots up front and surfaced as a ``MISSING`` Package node
    with evidence instead (Part B: degrade the certificate, never drop
    silently). ``uv_indexes`` (``PythonDependencyEvidence.uv_indexes``) are the
    captured ``[[tool.uv.index]]`` blocks, emitted alongside any source entry
    that references one by name. ``workspace_members`` is
    ``PythonDependencyEvidence.uv_workspace_members`` (the workspace glob
    patterns). ``repo_path`` is the real repo root every relative
    path/workspace lookup is resolved against -- required for any of the above
    to activate; with the defaults (empty ``uv_sources`` / no ``repo_path``)
    this is a complete no-op, byte-identical to the pre-Task-5 behavior.

    A throwaway uv project is created in a temp dir (or ``project_dir`` when
    injected, for tests), ``uv lock`` is run on the host through
    ``host_executor``, and the resulting ``uv.lock`` is parsed.  On lock failure
    the offending roots are dropped and the lock is retried (per-root
    resilience, bounded by BOTH attempt count and a wall-clock budget — see
    :func:`_resolve_ladder_budget_seconds`); the dropped roots are emitted as
    ``missing``/conflict Package nodes with evidence.  If no lock can be
    produced at all, a degraded ``uv pip compile`` ``# via`` parse is used as
    a last resort, preferring the ladder's own reduced/surviving root set over
    the full original one so a partial environment is recovered instead of an
    empty one.
    """
    target_python = target_env.python_version
    platform = target_env.python_platform_tag
    if excluded_dist_names:
        excluded = {_canon(name) for name in excluded_dist_names}
        roots = [r for r in roots if _canon(_req_name(r[1])) not in excluded]

    diag_nodes: list[Node] = []
    diag_edges: list[Edge] = []

    # Task 5/6: rewrite each root's `[tool.uv.sources]` spec ONCE up front (the
    # filesystem shape a workspace/path lookup depends on cannot change
    # between ladder rounds). A name whose source cannot be honored is
    # dropped from every round's roots right here and surfaced as a MISSING
    # Package node instead (never silently hand a bare, unhonorable name to
    # `uv` -- see :func:`_rewrite_uv_source_entries`).
    honored_sources: dict[str, tuple[dict, ...]] = {}
    if uv_sources and repo_path is not None:
        root_canon_names = {_canon(_req_name(r[1])) for r in roots}
        for name, entries in uv_sources.items():
            canon_name = _canon(name)
            if canon_name not in root_canon_names:
                continue
            rewritten, missing_node = _rewrite_uv_source_entries(
                name, entries, repo_path, workspace_members
            )
            if missing_node is not None:
                diag_nodes, diag_edges = _merge(
                    diag_nodes, diag_edges, [missing_node], []
                )
                roots = [r for r in roots if _canon(_req_name(r[1])) != canon_name]
                continue
            honored_sources[canon_name] = rewritten

    if not roots:
        return _merge([], [], diag_nodes, diag_edges)

    # Gate 4: `[tool.uv.sources]` is a GLOBAL override table in uv's own
    # semantics -- it applies to ANY package name anywhere in the resolved
    # graph, not just the top-level roots passed to `uv lock`. Filtering it
    # down to "only names still a root THIS round" (as this used to do) was
    # wrong: when root `foo` is dropped by the drop-retry ladder for an
    # unrelated conflict while a surviving root `bar` still depends on `foo`
    # transitively, the NEXT round would have no source override for `foo` at
    # all, and `uv` would silently resolve public PyPI `foo` in its place --
    # exactly the false-green vector this whole fix closes. So the full
    # `honored_sources` table (already scoped to the ORIGINAL root set above)
    # is computed ONCE and emitted EVERY round, regardless of which names
    # remain in `current` — never re-filtered by round.
    index_names = {
        entry["index"]
        for entries in honored_sources.values()
        for entry in entries
        if isinstance(entry.get("index"), str)
    }
    indexes = tuple(idx for idx in uv_indexes if idx.get("name") in index_names)

    with _project_dir(project_dir) as workdir:
        current = list(roots)
        budget_s = _resolve_ladder_budget_seconds()
        deadline = time.monotonic() + budget_s
        budget_exceeded = False
        # Bounded attempts: full set, then progressively fewer roots -- AND
        # bounded by wall clock (FIX 4 / B6), never attempt count alone.
        for round_num in range(len(roots) + 1):
            now = time.monotonic()
            remaining_budget = deadline - now
            if remaining_budget <= 0:
                budget_exceeded = True
                break
            names = [dist for _import_id, dist in current]
            if not names:
                break
            _write_pyproject(
                workdir,
                names,
                target_python,
                extras=extras,
                uv_sources=honored_sources,
                uv_indexes=indexes,
            )
            # FIX 4 (B6), item 1: never let a single attempt eat the whole
            # remaining budget (or the Executor default, whichever is worse) --
            # cap it to whatever budget is actually left this round.
            attempt_timeout = min(remaining_budget, PER_ATTEMPT_CAP_S)
            result = host_executor.run(
                _lock_command(workdir, target_python, exclude_newer),
                timeout=attempt_timeout,
            )
            # FIX 4 (B6), item 4: per-attempt timing is the only way to ever
            # answer "how was the budget actually spent" after the fact (a
            # PostHog run burned 2,224s of 2,400s with no way to tell whether
            # that was many rounds or a few slow locks).
            after = time.monotonic()
            logger.info(
                "resolve_closure: round %d over %d root(s) took %.2fs "
                "(timeout=%.1fs, rc=%s)",
                round_num,
                len(current),
                after - now,
                attempt_timeout,
                result.returncode,
            )
            if after >= deadline:
                # The attempt itself consumed the rest of the budget (a real
                # in-flight timeout, or just a very slow rc-0 lock) -- flag it
                # even though the "no progress" break below may fire for an
                # unrelated reason (item 3: an undiagnoseable timeout stderr
                # must never silently look identical to "nothing happened").
                budget_exceeded = True
            lock_text = _read_lock(workdir) if result.ok else None

            if lock_text:
                try:
                    nodes, edges = parse_uv_lock(
                        lock_text,
                        target_python,
                        target_platform=platform,
                        target_env=target_env,
                    )
                    risk = native_risk_from_lock(
                        lock_text, platform, target_python, target_env=target_env
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
            offending = _offending_root_names(
                diag, current_root_names, audit_root_names
            )
            remaining = [
                r for r in current if _canon(_req_name(r[1])) not in offending
            ]
            if not offending or remaining == current:
                break  # cannot make progress by dropping roots.
            current = remaining

        # Ladder exhausted (attempt count OR wall-clock budget) without
        # converging: log what was dropped/untried (never silently swallow
        # it; FIX 4/B6 item 3 -- this now also fires on a bare budget
        # exhaustion with nothing dropped), then prefer the ladder's own
        # reduced/surviving root set for the degraded fallback — it already
        # excludes roots the ladder proved bad, so it is far more likely to
        # recover a PARTIAL closure than blindly repeating the FULL original
        # root set (which would fail for the identical reason). A partial
        # environment strictly beats an empty one. FIX 4/B6 item 2: an EMPTY
        # `current` (every root dropped) must stay empty here -- re-attempting
        # the full original `roots` would just repeat names the ladder just
        # proved unresolvable; `_pip_compile_fallback([], ...)` already
        # short-circuits to `[], []` for that case.
        _log_ladder_exhaustion(roots, current, budget_s, budget_exceeded)
        if budget_exceeded:
            diag_nodes, diag_edges = _merge(
                diag_nodes,
                diag_edges,
                [_budget_exhausted_node(roots, current, budget_s)],
                [],
            )
        fallback_roots = current
        # The degraded fallback is a single, cheap, best-effort attempt made
        # independent of the ladder's own budget (it still needs SOME bound,
        # per item 1, so it can never hang unboundedly): reuse whatever budget
        # is left, or the flat per-attempt cap once that budget is already
        # spent.
        fallback_remaining = deadline - time.monotonic()
        fallback_timeout = (
            min(fallback_remaining, PER_ATTEMPT_CAP_S)
            if fallback_remaining > 0
            else PER_ATTEMPT_CAP_S
        )
        fb_nodes, fb_edges = _pip_compile_fallback(
            fallback_roots,
            host_executor,
            target_python,
            uv_sources=uv_sources,
            timeout=fallback_timeout,
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
    *,
    uv_sources: "Mapping[str, tuple[dict, ...]]" = MappingProxyType({}),
    timeout: float = PER_ATTEMPT_CAP_S,
) -> tuple[list[Node], list[Edge]]:
    """Degraded resolve via ``uv pip compile`` when ``uv.lock`` is unavailable.

    Gate 3: ``uv pip compile`` (unlike ``uv lock``) is fed bare distribution
    names on stdin with NO ``[tool.uv.sources]`` override at all -- it has no
    mechanism to honor a git/path/workspace/index source. Any root whose
    canonical name carries a ``uv_sources`` entry must therefore never reach
    the bare compile command: doing so would silently resolve the public
    PyPI namesake instead of the pinned/local dependency the manifest
    actually names -- ladder exhaustion must not launder a sourced name
    through this fallback. Those roots are pulled out up front and surfaced
    as ``State.MISSING`` Package nodes carrying evidence of the unresolved
    source instead; only the remaining ordinary (default-PyPI) roots are fed
    to ``uv pip compile``.

    ``timeout`` (FIX 4 / B6, item 1) bounds the ``uv pip compile`` subprocess
    the same way the ladder's own ``uv lock`` attempts are bounded -- this
    fallback previously omitted ``timeout=`` entirely, silently falling back
    to the Executor Protocol's fixed 300s default regardless of how the
    caller's own budget had already been spent. Defaults to
    ``PER_ATTEMPT_CAP_S`` so a direct/future caller that doesn't pass one
    still gets a bounded attempt rather than an unbounded one.
    """
    sourced_canon = {_canon(name): name for name in uv_sources} if uv_sources else {}
    missing_nodes: list[Node] = []
    plain_roots: list[tuple[str | None, str]] = []
    for import_id, dist in roots:
        bare_name = _req_name(dist)
        canon = _canon(bare_name)
        raw_name = sourced_canon.get(canon)
        if raw_name is not None:
            entries = uv_sources[raw_name]
            spec = entries[0] if entries else {}
            evidence = (
                f"'{dist}' carries a [tool.uv.sources] override ({spec!r}) that "
                "the degraded `uv pip compile` fallback cannot honor (it "
                "resolves bare requirement names only, with no source "
                "overrides) -- never bare-installed from public PyPI in its place"
            )
            missing_nodes.append(_missing_source_node(bare_name, None, evidence))
        else:
            plain_roots.append((import_id, dist))

    dist_names = [dist for _import_id, dist in plain_roots]
    packages: list[tuple[str, str]] = []
    via: dict[str, set[str]] = {}
    if dist_names:
        result = host_executor.run(
            _compile_command(dist_names, target_python), timeout=timeout
        )
        if result.ok:
            packages, via = _parse_closure(result.stdout)

    nodes: list[Node] = list(missing_nodes)
    canon_to_id: dict[str, str] = {}
    for name, version in packages:
        node = _package_node(name, version, provenance="uv pip compile")
        nodes.append(node)
        canon_to_id[_canon(name)] = node.id

    if not nodes:
        return [], []

    edges: list[Edge] = list(_import_edges(roots, nodes))
    seen: set[tuple[str, str]] = {(e.src, e.dst) for e in edges}

    def _add_edge(src: str, dst: str) -> None:
        if src == dst or (src, dst) in seen:
            return
        seen.add((src, dst))
        edges.append(
            Edge(src=src, dst=dst, relation=EdgeType.REQUIRES, origin="resolver")
        )

    for child_canon, parents in via.items():
        child_id = canon_to_id.get(child_canon)
        if child_id is None:
            continue
        for parent_canon in parents:
            parent_id = canon_to_id.get(parent_canon)
            if parent_id is not None:
                _add_edge(parent_id, child_id)

    return nodes, edges
