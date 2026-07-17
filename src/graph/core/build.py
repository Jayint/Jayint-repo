"""Stage orchestrator — repo path in, host-certified ``DepGraph`` out.

Wires the pipeline of
``docs/DESIGN-static-probe-certified-dependency-graph.md`` /
``docs/superpowers/specs/2026-06-23-uv-enriched-depgraph.md`` as TWO phases with
TWO distinct oracles (P2.1):

    scan/map   static import scan + declared-ONLY roots -> Import/Test  (cycle 1)

    Phase A -- "is it PROVIDED?"  oracle = RECORD-union coverage.
       A bounded resolve (HOST uv) -> install (CONTAINER) -> look -> repair
       FIXPOINT (:func:`_phase_a_fixpoint`). Each round audits the runtime imports
       against the RESOLVED closure's RECORD-union coverage
       (:func:`coverage.resolved_record_coverage`) and repairs an under-declared
       import by grounding + adding an AUDIT root, re-resolving until coverage is
       stable. The loop condition is the RECORD-union oracle, NEVER a per-round
       ``packages_distributions()`` probe — so a resolved-but-failed-to-build dist
       counts PROVIDED here (its wheel RECORDs the module) and its native gap is a
       Phase-B concern, not a Phase-A under-declaration.                (cycle 2)

    Phase B -- "does it LOAD / who PROVIDES it?"  oracle = the live CONVERGED
    container. A single tier descent over the converged closure, "look then
    derive":
       relink   certified Import->Package edges + honest ``unresolved`` flags
                (packages_distributions, CONTAINER) -- Phase B's LOOK           (cycle 3)
       ldd      ldd ext .so ``=> not found`` -> run-time SystemLib (DT_NEEDED)   (cycle 3)
       probe    import backstop -> dlopen'd SystemLib not in NEEDED             (cycle 3)
       apt      reconcile predicted apt names against the target image
       certify  host check_commands (CONTAINER) -> node ``state``              (cycle 4)

**Executor split (spec "Architecture change"):** resolution is HOST-side — ``uv``
cross-platform resolves the container target without a container interpreter — so
it runs through ``host_executor``.  Install/probe/certify must observe the real
target environment, so they run through ``container_executor``.  Both default-safe
for unit tests (a single ``FakeExecutor`` can be injected for both).

Phase B's discovery order and the later execution order differ (design 3.3 /
10.10): the descent LOOKS (relink) then DERIVES system deps (ldd/probe) on the
converged closure, but certification then runs in execution layer order (system
before pip).  Every stage returns a NEW immutable graph; this function only ever
rebinds ``graph``.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import replace
from types import MappingProxyType

try:  # tomllib is stdlib on 3.11+; fall back to the tomli backport on 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib

from graph.python.native.apt_verify import reconcile_apt_names
from graph.python.native.build_deps import seed_build_deps
from graph.core.certify import certify_all
from graph.python.lanes.install.coverage import (
    composite_record_provider,
    default_record_provider,
    pypi_record_provider,
    resolved_record_coverage,
)
from graph.contracts.executor import Executor, LocalSubprocessExecutor
from graph.ids import TEST_NODE_ID, package_id, project_id
from graph.python.native.ldd_probe import ldd_probe
from graph.python.lanes.install.pins import compute_exclude_newer
from graph.python.native.probe import import_probe, install_closure
from graph.python.native.project_native_deps import project_native_obligations
from graph.python.lanes.install.relink import certified_import_links
from graph.python.lanes.install.repair import (
    DistGuesser,
    RecordProvider,
    Verdict,
    choose_provider,
    generate_candidates,
)
from graph.python.lanes.install.resolve import (
    _req_name,
    resolve_closure,
)
from graph.python.lanes.install.roots import select_roots
from graph.python.read.scan import scan_to_nodes
from graph.model import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from graph.python.native.seed import seed_wheel_oracle_prior
from graph.python.read.subprocess_scan import add_subprocess_tool_nodes
from graph.python.read.target_env import detect_target_env
from graph.python.native.wheel_preflight import wheel_preflight_probe
from graph.python.read.evidence import collect_python_dependency_evidence
from graph.python.util.import_mapping import normalize_package_name, top_level_import_name

logger = logging.getLogger(__name__)

# Numeric backstop on Phase-A repair rounds (Correction 2b): the attempted-set is
# the honest terminator; this caps pathological non-convergence.
_MAX_REPAIR_ROUNDS = 5

# discovered_cycle stamps, one per discovery stage (design 5.2 example uses 3 for
# probe-discovered SystemLibs).
_SCAN_CYCLE = 1
_RESOLVER_CYCLE = 2
_PROBE_CYCLE = 3
_CERTIFY_CYCLE = 4


def _restamp(graph: DepGraph, node_ids: set[str], cycle: int) -> DepGraph:
    """Return a new graph with ``discovered_cycle = cycle`` on the named nodes."""
    new = graph
    for node_id in node_ids:
        node = new.get(node_id)
        if node is not None:
            new = new.with_node(replace(node, discovered_cycle=cycle))
    return new


# PEP-503 canonicalizer. Was a build-local def byte-identical to
# import_mapping.normalize_package_name; aliased to that util-natured owner (not
# duplicated) so the fixpoint/skeleton/pipeline split files import one shared
# canonicalizer from util rather than a build-local copy.
_canon = normalize_package_name


def _project_name(repo_path: str) -> str:
    """Project name from ``[project].name`` in pyproject.toml, else dir basename."""
    pyproject = os.path.join(repo_path, "pyproject.toml")
    try:
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        name = (data.get("project") or {}).get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return os.path.basename(repo_path.rstrip("/\\")) or "project"


def _project_build_manifest(repo_path: str) -> str | None:
    """The build manifest that makes the repo editable-installable, or None.

    A ``pip install -e .`` needs DECLARED packaging intent, not merely a file
    named ``pyproject.toml``: many repos ship a ``pyproject.toml`` purely for tool
    config (``[tool.black]``, ``[tool.ruff]``, …) alongside a flat multi-module
    layout that ``pip install -e .`` cannot build (setuptools aborts with
    "Multiple top-level modules discovered in a flat-layout"). Under the rendered
    script's ``set -Eeuo pipefail`` that one line would abort the whole setup.sh —
    a NEW first-pass failure on a repo that never needed the editable install.

    So a ``pyproject.toml`` counts only when it declares ``[project]`` (PEP 621
    metadata) or ``[build-system]`` (PEP 517 backend); a bare ``setup.py`` is the
    legacy installable signal. A tool-config-only pyproject with no ``setup.py``,
    or an unparseable pyproject we cannot confirm, yields None — the renderer then
    emits no editable install (never a command we cannot stand behind).
    """
    pyproject = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        if "project" in data or "build-system" in data:
            return pyproject
    setup_py = os.path.join(repo_path, "setup.py")
    if os.path.isfile(setup_py):
        return setup_py
    return None


def _project_import_target(project_name: str, evidence) -> str | None:
    """The project's own top-level import module to certify-by-import, or None.

    Maps the distribution name to an import name (dash->underscore, lowercased)
    and returns it ONLY when that exact name is one of the repo's own top-level
    modules (``evidence.project_local_modules``). When the import name differs
    from the dist name (``scikit-learn`` -> ``sklearn``) there is no
    tripwire-safe static match, so we return None and leave the Project UNKNOWN
    rather than certify against a guess -- the relink-based mapping (config lane,
    a later task) covers that case with a certified source. ``build.py`` must not
    import ``repo_modules`` (construction-boundary tripwire), so the source here
    is the already-collected ``project_local_modules``.
    """
    canon = project_name.lower().replace("-", "_")
    return canon if canon in set(evidence.project_local_modules) else None


def _add_project_node(graph: DepGraph, repo_path: str) -> DepGraph:
    """Add a Project hub node and connect declared direct deps to it.

    The repo under test is otherwise only reachable through the Test->Import
    chain, so its declared direct dependencies have no shared parent (e.g.
    ``certifi`` had no incoming Package->Package edge).  This node makes "what
    does the project directly require" a single explorable subtree:

    * ``Test --requires--> Project``
    * ``Project --requires--> <runtime declared dep Package>``  (kind=dependency)
    * ``Test --requires--> <test/optional declared dep Package>`` (kind=optional)

    Runtime vs test classification reuses ``evidence`` (kind ``dependency`` vs
    ``optional_dependency``); no new parsing.  Transitive deps still hang off
    their parents, and Import->Package reconciliation is unchanged.
    """
    name = _project_name(repo_path)
    proj_id = project_id(name)
    manifest = _project_build_manifest(repo_path)
    # Collected BEFORE node construction (not after, as before) so
    # soft_requirements_files is available for the node's data at creation time.
    evidence = collect_python_dependency_evidence(repo_path)
    import_target = _project_import_target(name, evidence)
    project_check = f'python -c "import {import_target}"' if import_target else None
    graph = graph.with_node(
        Node(
            id=proj_id,
            type=NodeType.PROJECT,
            name=name,
            layer=Layer.PIP,
            discovered_by=DiscoveredBy.STATIC_SCAN,
            state=State.UNKNOWN,
            check_command=project_check,
            provenance=manifest or repo_path,
            data={
                # installable => the renderer emits `pip install -e .` as the final,
                # post-dependency step (populate/build_script read this flag).
                "installable": manifest is not None,
                # Nested (non-hard-root) requirements files the recursive walk found
                # (see evidence.py / models.PythonDependencyEvidence). Rendered as
                # best-effort, closure-constrained installs by build_script.py — a
                # tuple because Node.data is immutable (MappingProxyType).
                "soft_requirements_files": tuple(evidence.soft_requirements_files),
            },
        )
    )
    graph = graph.with_edge(
        Edge(src=TEST_NODE_ID, dst=proj_id, relation=EdgeType.REQUIRES, origin="project")
    )

    canon_to_pkg = {
        _canon(n.name): n.id for n in graph.nodes if n.type is NodeType.PACKAGE
    }
    for req in evidence.declared_dependencies:
        if getattr(req, "kind", "dependency") == "constraint":
            continue
        pkg_id = canon_to_pkg.get(_canon(normalize_package_name(req.name)))
        if pkg_id is None:
            continue
        # runtime deps hang off the Project; test/optional deps off the Test goal.
        src = (
            TEST_NODE_ID
            if getattr(req, "kind", "dependency") == "optional_dependency"
            else proj_id
        )
        graph = graph.with_edge(
            Edge(src=src, dst=pkg_id, relation=EdgeType.REQUIRES, origin="project")
        )
    return graph


def _pad_python_full(target_python: str) -> str:
    """``"3.13"`` -> ``"3.13.0"`` (padding for a caller-supplied override).

    Mirrors the padding ``resolve_lock._target_env_for`` applies so an
    overridden ``target_python`` still produces a valid ``python_full_version``
    for marker evaluation (``python_full_version < '3.12'`` style forks).
    """
    parts = [p for p in target_python.split(".") if p]
    return ".".join((parts + ["0", "0"])[:3]) if parts else target_python


# Minor-version token in a ``Python 3.13.14`` banner.
_PY_VER_RE = re.compile(r"(\d+\.\d+)")
# Last-resort interpreter version when the container probe yields nothing.
_DEFAULT_TARGET_PYTHON = "3.11"


def _detect_target_python(
    container_executor: Executor, default: str = _DEFAULT_TARGET_PYTHON
) -> str:
    """Probe the container's interpreter minor version (e.g. ``"3.13"``).

    The resolve MUST target the python the container actually runs, or it pins
    versions that have no wheel for that interpreter (observed: a
    3.11-resolved ``pyarrow==2.0.0`` cannot build on a 3.13 container). Tries
    ``python3`` then ``python``, reading both streams (``--version``
    historically printed to stderr). Falls back to ``default`` when nothing
    parses, so a fake/empty executor preserves the legacy 3.11 target.

    Superseded in :func:`build_dep_graph` by :func:`target_env.detect_target_env`
    (Task 7, one combined probe covering python + platform); kept standalone
    (directly unit-tested) as it captures a slightly different signal (a
    ``--version`` banner rather than ``sys.version``) that some callers may
    still want in isolation.
    """
    for cmd in ("python3 --version", "python --version"):
        result = container_executor.run(cmd)
        if not result.ok:
            continue
        m = _PY_VER_RE.search((result.stdout or "") + " " + (result.stderr or ""))
        if m:
            return m.group(1)
    return default


def reconcile_packages(
    graph: DepGraph,
    pkg_nodes: list[Node],
    pkg_edges: list[Edge],
    prev_pkg_ids: set[str],
) -> DepGraph:
    """Merge a fresh resolve round's Package nodes/edges, dropping the prior
    round's stale ones (Phase-A Correction 2c).

    Package ids bake the version (``pkg:name==version``) and ``DepGraph`` is
    upsert-only, so a version shift between rounds would otherwise leave the old
    ``pkg:name==v_old`` node (and its edges) orphaned. Before merging the new
    nodes/edges, remove every Package node the PRIOR round produced
    (``prev_pkg_ids``) that the NEW resolve no longer emits (``without_node`` also
    drops that node's dangling edges), and every prior Package->Package
    ``requires`` edge among still-surviving nodes the new resolve no longer emits
    (``without_edge``). Conflict advisory edges are left untouched. Returns a NEW
    graph; net effect: no stale Package orphan survives a version change.
    """
    new_ids = {n.id for n in pkg_nodes}
    new_edge_keys = {e.key() for e in pkg_edges}
    new = graph
    for stale_id in prev_pkg_ids - new_ids:
        new = new.without_node(stale_id)
    for edge in list(new.edges):
        if (
            edge.relation is EdgeType.REQUIRES
            and edge.src in prev_pkg_ids
            and edge.dst in prev_pkg_ids
            and edge.key() not in new_edge_keys
        ):
            new = new.without_edge(edge)
    for node in pkg_nodes:
        new = new.with_node(node)
    for edge in pkg_edges:
        new = new.with_edge(edge)
    return new


def _stamp_audit(graph: DepGraph, repaired: set[str]) -> DepGraph:
    """Stamp ``discovered_by=AUDIT`` on Package nodes whose canon dist was repaired.

    Each resolve round re-emits a repaired dist's Package as ``RESOLVER`` (fresh
    from the lock), so this runs every round after the merge to keep the AUDIT
    provenance; declared/transitive packages keep ``RESOLVER``.
    """
    new = graph
    for node in graph.nodes:
        if (
            node.type is NodeType.PACKAGE
            and _canon(node.name) in repaired
            and node.discovered_by is not DiscoveredBy.AUDIT
        ):
            new = new.with_node(replace(node, discovered_by=DiscoveredBy.AUDIT))
    return new


def _missing_import_nodes(graph, *, provided: frozenset[str], deferred: frozenset[str]):
    """Non-optional IMPORT nodes no resolved dist provides — LANE-AWARE: also
    excludes Module-routed imports and deferred-collision names so first-party
    names never inflate the repair bound nor reach the dist-guesser. Vacuous when
    no node is Module-routed and ``deferred`` is empty (today's real construction)."""
    return [
        n for n in graph.nodes
        if n.type is NodeType.IMPORT
        and n.data.get("optional") is not True
        and n.data.get("routed_provider") != "module"
        and n.name.split(".", 1)[0] not in deferred
        and top_level_import_name(n.name).lower() not in provided
    ]


def _phase_a_fixpoint(
    graph: DepGraph,
    roots: list[tuple[str | None, str]],
    host_executor: Executor,
    container_executor: Executor,
    record_provider: RecordProvider,
    *,
    target_env,
    exclude_newer: str | None,
    needed_extras: frozenset[str],
    declared_package_names: frozenset[str] = frozenset(),
    uv_sourced_names: frozenset[str] = frozenset(),
    uv_sources=MappingProxyType({}),
    uv_indexes: tuple[dict, ...] = (),
    workspace_members: tuple[str, ...] = (),
    repo_path: str | None = None,
    llm: DistGuesser | None = None,
    deferred: frozenset[str] = frozenset(),
) -> DepGraph:
    """Bounded resolve -> install -> look -> repair fixpoint (Phase A).

    Each round resolves the current roots, reconciles the Package layer (dropping
    stale nodes/edges — Correction 2c), install-probes the closure, then audits
    the FULL runtime import set against the resolved closure's RECORD-union
    coverage (Correction 3 — never ``packages_distributions``). A non-optional
    import that no resolved dist provides is repaired by grounding candidate dists
    (from the vendored pipreqs map, else an injected LLM guesser on a map miss,
    each RECORD-grounded via ``record_provider``) and, on an unambiguous ACCEPT,
    adding the dist as an AUDIT root
    (``audit_root_names`` threads the repaired set into the resolve retry so a
    declared root is never evicted — Correction 2a) and re-resolving.

    Terminates when coverage is complete, when no new ``(import, candidate)`` pair
    can be proposed (attempted-set / fixpoint — Correction 2b), or at the numeric
    bound ``min(initial_missing, 5)``; residue is left for P0.3 to flag
    unresolved downstream — construction is NEVER aborted. Every round returns a
    NEW graph; the orchestrator only rebinds ``graph``.
    """
    root_dists = {_canon(_req_name(dist)) for _imp, dist in roots}
    repaired: set[str] = set()
    attempted: set[tuple[str, str]] = set()
    prev_pkg_ids: set[str] = set()
    bound: int | None = None
    iteration = 0

    while True:
        pkg_nodes, pkg_edges = resolve_closure(
            roots,
            host_executor,
            target_env=target_env,
            exclude_newer=exclude_newer,
            extras=needed_extras,
            audit_root_names=frozenset(repaired),
            uv_sources=uv_sources,
            uv_indexes=uv_indexes,
            workspace_members=workspace_members,
            repo_path=repo_path,
        )
        graph = reconcile_packages(graph, pkg_nodes, pkg_edges, prev_pkg_ids)
        graph = _stamp_audit(graph, repaired)
        prev_pkg_ids = {n.id for n in pkg_nodes}
        graph = install_closure(graph, container_executor)

        # Correction 3: the coverage oracle is RECORD-union over the RESOLVED
        # closure, NOT a post-install packages_distributions() snapshot.
        provided = resolved_record_coverage(pkg_nodes, record_provider)
        missing = _missing_import_nodes(graph, provided=frozenset(provided), deferred=deferred)
        if bound is None:
            bound = min(len(missing), _MAX_REPAIR_ROUNDS)
        if not missing:
            break

        new_pair = False
        for imp in missing:
            # Candidate generation: the vendored pipreqs import->dist table first
            # (deterministic); ONLY on a map miss does the injected ``llm`` guesser
            # propose, fed this import's used-symbols (``data["symbols"]``). When
            # ``llm`` is None (the default), the path stays purely deterministic.
            # Either way every candidate is still RECORD-grounded by
            # ``choose_provider`` -- this only proposes names, never accepts them.
            candidates = generate_candidates(
                imp.name, symbols=tuple(imp.data.get("symbols", ())), llm=llm
            )
            decision = choose_provider(imp.name, candidates, record_provider)
            if (
                decision.verdict is Verdict.ACCEPT
                and decision.dist is not None
                and (imp.name, decision.dist) not in attempted
                and _canon(decision.dist) not in root_dists
                # Gate/Also-fix 2: `generate_candidates` proposes the pipreqs-mapped
                # (or LLM-guessed) dist purely from the import name/symbols, with no
                # knowledge of `_declared_package_names_for_repair`'s uv-source
                # exclusion, so an unactivated optional uv-sourced dependency whose
                # import happens to map to its own dist name could still be
                # RECORD-confirmed and re-enter as a repaired root, silently
                # resolving the unrelated public PyPI package in its place. It must
                # therefore be rejected HERE, at acceptance, as well.
                and _canon(decision.dist) not in uv_sourced_names
            ):
                roots = roots + [(None, decision.dist)]
                repaired.add(_canon(decision.dist))
                root_dists.add(_canon(decision.dist))
                new_pair = True
            # Remember every candidate tried for this import (Correction 2b), so a
            # re-proposal of an already-attempted pair cannot re-add / oscillate.
            attempted |= {(imp.name, candidate.dist) for candidate in candidates}
        if not new_pair:
            logger.warning(
                "phase-A stopped: no new repair candidate; residue left unresolved "
                "(fixpoint/oscillation): %s",
                sorted(n.name for n in missing),
            )
            break
        iteration += 1
        if iteration > bound:
            logger.warning(
                "phase-A hit bound=%d; residue left unresolved (honest), not aborting",
                bound,
            )
            break
    return graph


def _uv_sourced_dist_names(evidence) -> frozenset[str]:
    """Canonical distribution names that resolve to a non-PyPI source: either
    an explicit ``evidence.uv_sources`` override (workspace/git/url/path/
    index) OR a PEP 508 direct reference (``evidence.direct_reference_sources``
    -- Fix 1, docs/superpowers/plans/2026-07-14-post-measurement-fixes.md).
    The union is the ONE canonical "non-PyPI name" set every consumer below
    reads; a direct reference gets the identical protection a
    `[tool.uv.sources]` override already has, through the same function.

    This is a real, previously-found HIGH bug's protection: a git-pinned
    ``acme-sdk`` must never be "repaired" into the unrelated, same-named
    PUBLIC PyPI package. Used to exclude such names at the repair ladder's
    final ACCEPTANCE gate (:func:`_phase_a_fixpoint`) -- the old
    ``declared_metadata`` repair rung no longer exists, so acceptance-time
    exclusion is now where this protection lives. It is needed because
    ``repair.generate_candidates`` proposes a distribution name from the
    vendored pipreqs map (or the injected LLM guesser on a map miss)
    independently of ``declared_package_names``, and that proposed name can
    collide with a same-named public PyPI package: an unactivated optional
    uv-sourced dependency whose import maps to its own dist name could still
    be RECORD-confirmed and accepted as a repaired root. Excluding it at
    acceptance time closes that regardless of how the candidate was proposed.
    """
    names = {normalize_package_name(name) for name in evidence.uv_sources}
    names |= {
        normalize_package_name(name)
        for name in getattr(evidence, "direct_reference_sources", {})
    }
    return frozenset(names)


# --------------------------------------------------------------------------- #
# V3_UV_SOURCES default-OFF path (see build_dep_graph's ``uv_sources_enabled``
# parameter docstring for the full false-green rationale). This is the ONLY
# place in the pipeline that decides whether a `[tool.uv.sources]`-carrying
# root ever reaches the resolver at all; every function below stays a plain
# ``roots``/``graph`` transform, no env read (the flag itself is read once,
# by the impure caller at scripts/run_v3_e2e.py's ``_uv_sources_enabled``, and
# passed down as an explicit argument -- matching this codebase's
# ``V3_INCLUDE_SERVICES`` convention, see emit.py's ``_is_service_reciped``).
# --------------------------------------------------------------------------- #
def _excluded_uv_source_node(name: str, entries: tuple[dict, ...]) -> Node:
    """A ``State.MISSING`` Package node for a dependency EXCLUDED from resolve
    roots because ``uv_sources_enabled`` is False (the default).

    This is the "safe pre-C1 behaviour, plus honesty" default: the dependency
    never becomes a root (so none of the six bare-``name==version`` egress
    points documented on ``build_dep_graph`` can ever reach it), but it also
    must never vanish silently -- so it is surfaced here as an explicit node
    instead.

    Three independent, deliberate belts (verified against the actual renderer/
    certifier code, not assumed):

    * ``version=None`` -- ``emit._is_emittable`` returns False on
      ``if not node.version`` BEFORE it ever reaches its (separately known-
      blind) ``uninstallable`` check, so this node can never be emitted as a
      ``pip install`` line regardless of that blind spot.
    * ``check_command=None`` -- unlike every OTHER Package node this codebase
      builds (which always carry ``python -m pip show <name>``),
      ``certify()`` short-circuits on ``if node is None or not
      node.check_command: return graph`` -- so NO check ever runs for this
      node. ``certify()`` flips MISSING -> SATISFIED off any check that
      merely returns rc 0, blind to ``data['uninstallable']`` too -- if the
      public namesake got installed by an unrelated route (any of the six
      egress points, when the flag is forced on), a live ``pip show <name>``
      would happily succeed. Never giving this node a check_command at all is
      the only way to guarantee that success can never reach it.
    * ``data['uninstallable']=True`` -- kept anyway, defense in depth, for the
      renderer gate every OTHER "cannot install" node already uses
      (``emit._is_reciped`` / ``populate.py`` / ``build_script.py``).
    """
    spec = entries[0] if entries else {}
    kind = next(
        (k for k in ("workspace", "git", "url", "path", "index") if k in spec),
        "source",
    )
    evidence = (
        f"'{name}' carries a [tool.uv.sources] {kind} override ({spec!r}); "
        "excluded from resolve roots because V3_UV_SOURCES is OFF (the "
        "default) -- see build_dep_graph's uv_sources_enabled docstring."
    )
    return Node(
        id=package_id(name, None),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        version=None,
        check_command=None,
        fix_candidates=(),
        chosen_fix=None,
        provenance="[tool.uv.sources] (excluded -- V3_UV_SOURCES off)",
        state=State.MISSING,
        evidence=evidence,
        data={"uninstallable": True},
    )


def _exclude_uv_sourced_roots(
    graph: DepGraph,
    roots: list[tuple[str | None, str]],
    uv_sources: dict[str, tuple[dict, ...]],
) -> tuple[DepGraph, list[tuple[str | None, str]]]:
    """Drop every ``[tool.uv.sources]``-carrying root and surface an honest
    MISSING node for each instead (see :func:`_excluded_uv_source_node`).

    Matching by canonical name (``_canon``, the SAME normalization
    ``_phase_a_fixpoint``'s acceptance gate already relies on against
    ``evidence.uv_sources`` -- see :func:`_uv_sourced_dist_names`), so this
    reuses the identical name-matching contract instead of inventing a new
    one. A no-op (returns ``graph``/``roots`` unchanged) when ``uv_sources``
    is empty -- the "no [tool.uv.sources] at all" byte-identical case.
    """
    if not uv_sources:
        return graph, roots
    sourced = {_canon(name): (name, entries) for name, entries in uv_sources.items()}
    kept: list[tuple[str | None, str]] = []
    for import_id, dist in roots:
        hit = sourced.get(_canon(_req_name(dist)))
        if hit is None:
            kept.append((import_id, dist))
            continue
        raw_name, entries = hit
        graph = graph.with_node(_excluded_uv_source_node(raw_name, entries))
    return graph, kept


# --------------------------------------------------------------------------- #
# Fix 1 (docs/superpowers/plans/2026-07-14-post-measurement-fixes.md): a PEP
# 508 direct reference (``name @ git+.../http(s)://.../file:...``) names a
# non-PyPI source exactly like a `[tool.uv.sources]` override -- ArchipelagoMW/
# Archipelago's root requirements.txt declares
# `kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d` and ``uv lock`` is
# all-or-nothing, so that ONE unresolvable root previously killed the entire
# ~20-package closure at once (the SAME class of bug PostHog's
# `[tool.uv.sources] hogli = { workspace = true }` caused, arriving through a
# third syntax door).
#
# UNCONDITIONAL exclusion, unlike ``_exclude_uv_sourced_roots`` above (which is
# gated by ``uv_sources_enabled``/V3_UV_SOURCES): a direct reference is inline
# PEP 508 syntax with no separate `[tool.uv.sources]` table representation --
# there is no git/rev-aware rewrite in this codebase that could safely carry
# it into the synthetic pyproject the way a workspace/path override sometimes
# can (see resolve.py's ``_render_uv_sources``, which only knows how to emit a
# `[tool.uv.sources]` table, not rewrite a direct-reference URL into one).
# Threading it through unrewritten under V3_UV_SOURCES=1 would let
# `select_roots`' bare-name root (e.g. `"kivymd"`, its specifier already empty
# -- see evidence.py's ``_parse_requirement_line``) reach the resolver with NO
# source information at all, resolving the UNRELATED PUBLIC PyPI package of
# the same name instead -- exactly the false-green vector this whole
# mechanism exists to prevent. So this exclusion runs regardless of the flag.
# --------------------------------------------------------------------------- #
def _excluded_direct_reference_node(name: str, url: str) -> Node:
    """A ``State.MISSING`` Package node for a PEP 508 direct reference
    excluded from resolve roots.

    Identical three immunity belts to :func:`_excluded_uv_source_node`
    (``version=None`` / ``check_command=None`` / ``data['uninstallable']=True``)
    -- see that function's docstring for why each is load-bearing; copied
    exactly, not reinvented.
    """
    evidence = (
        f"'{name}' is a PEP 508 direct reference ({name} @ {url}); excluded "
        "from resolve roots because its real source is a URL, not public "
        "PyPI -- see docs/superpowers/plans/2026-07-14-post-measurement-"
        "fixes.md Fix 1."
    )
    return Node(
        id=package_id(name, None),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        version=None,
        check_command=None,
        fix_candidates=(),
        chosen_fix=None,
        provenance="PEP 508 direct reference (excluded)",
        state=State.MISSING,
        evidence=evidence,
        data={"uninstallable": True},
    )


def _exclude_direct_reference_roots(
    graph: DepGraph,
    roots: list[tuple[str | None, str]],
    direct_reference_sources: dict[str, str],
) -> tuple[DepGraph, list[tuple[str | None, str]]]:
    """Drop every PEP-508-direct-reference root and surface an honest MISSING
    node for each instead (see :func:`_excluded_direct_reference_node`).

    Matching by canonical name (``_canon``), the SAME contract
    :func:`_exclude_uv_sourced_roots` uses. A no-op when
    ``direct_reference_sources`` is empty -- the "no direct references at
    all" byte-identical case. Unlike that sibling function, this ALWAYS runs
    (see the module note above for why): it is called regardless of
    ``uv_sources_enabled``.
    """
    if not direct_reference_sources:
        return graph, roots
    sourced = {_canon(name): (name, url) for name, url in direct_reference_sources.items()}
    kept: list[tuple[str | None, str]] = []
    for import_id, dist in roots:
        hit = sourced.get(_canon(_req_name(dist)))
        if hit is None:
            kept.append((import_id, dist))
            continue
        raw_name, url = hit
        graph = graph.with_node(_excluded_direct_reference_node(raw_name, url))
    return graph, kept


def _declared_package_names_for_repair(evidence) -> frozenset[str]:
    """Declared distribution names, formerly eligible as Phase-A repair candidates.

    NOTE: currently RETAINED PLUMBING -- this value is still computed and
    threaded into :func:`_phase_a_fixpoint`, but is no longer consumed there
    since the declared repair rung was removed (``repair.generate_candidates``
    now proposes from the vendored pipreqs map, else an injected LLM guesser on
    a map miss). Kept for a future consumer; the description below reflects its
    original, now-dormant role.

    EVERY declared distribution, regardless of kind/group -- deliberately NOT
    the scope-filtered subset that reaches ``select_roots``. An unsatisfied
    import may be provided by a dist the repo declared in a group the root
    filter dropped (a feature extra it never signalled); the repair ladder may
    then SELECT that declaration instead of guessing a distribution name.

    EXCLUDES any name :func:`_uv_sourced_dist_names` returns (see its
    docstring for why an uv-sourced dependency must never be "repaired" into
    the unrelated same-named PUBLIC PyPI package); the live half of that
    protection now lives at :func:`_phase_a_fixpoint`'s acceptance gate.
    """
    excluded = _uv_sourced_dist_names(evidence)
    return frozenset(
        req.name
        for req in evidence.declared_dependencies
        if normalize_package_name(req.name) not in excluded
    )


def _python_package_obligations(
    repo_path: str,
    container_executor: Executor,
    *,
    host_executor: Executor | None = None,
    target_python: str | None = None,
    target_platform: str | None = None,
    exclude_newer: str | None = None,
    needed_extras: frozenset[str] = frozenset(),
    record_provider: RecordProvider | None = None,
    uv_sources_enabled: bool = False,
    llm_dist_guesser: DistGuesser | None = None,
    shadow_config_lane: bool = False,
) -> tuple[DepGraph, list, object, str | None]:
    """Python PHASE 1 — VERBATIM move of build_dep_graph body lines 488-608.

    Scan -> target-env -> declared roots -> era-anchor (ONCE, INV-1) -> Runtime
    node -> composite record-provider default (constructed HERE at the old
    569-571 site, INV-8) -> Phase-A repair fixpoint -> aux-once (project/tools/
    seed) -> resolver restamp (INV-7). Returns (graph, roots, target_env,
    exclude_newer); only ``graph`` flows onward — the other three are provider-
    composition / test-visibility surface (never read again after the fixpoint).

    ``uv_sources_enabled`` (V3_UV_SOURCES, default OFF) gates whether a
    `[tool.uv.sources]`-carrying dependency (workspace/git/url/path/index) is
    threaded into the resolver at all. An adversarial review proved the
    PACKAGE layer cannot safely handle a non-PyPI package: it models a
    package as ``(name, version)`` and installs it BY NAME from at least six
    independent sites --

    1. ``emit._is_emittable`` accepts any versioned MISSING Package and does
       not consult ``data['uninstallable']``, so it would emit a bare
       ``pip install forked-sdk==1.2.3`` (the PUBLIC package) for a resolved
       sourced dependency.
    2. ``probe.install_closure`` runs ``uv pip install --system ...`` with NO
       ``--no-deps``, so an ordinary public package that happens to depend on
       a git-sourced name drags in the PUBLIC namesake at install time,
       independent of anything the graph says.
    3. ``certify.certify`` flips a successful check straight to SATISFIED,
       and a Package's check is only ``python -m pip show <name>`` -- once
       the public namesake is installed by ANY of these routes, the node
       flips SATISFIED. ``certify`` never consults ``uninstallable`` either.
       MISSING does not stick.
    4. ``resolve_closure`` (Gate 4) applies ``[tool.uv.sources]`` as a GLOBAL
       override table over the whole resolved graph, so a name that is only
       a TRANSITIVE dependency (never a declared root) can still lose its
       override and resolve the public package silently.
    5. ``build_script.py``'s soft-requirements renderer writes a nested
       ``requirements.txt`` out as a bare ``pip install -r <file>``; a bare
       name inside that file installs the public package.
    6. ``build_script.py``'s pytest-bootstrap line and ``populate.py``'s
       PEP-517 editable-install build isolation can each independently reach
       public PyPI for a name this repo overrode.

    Patching those six egress points individually failed three review rounds
    running -- the safe move is to make the whole class UNREACHABLE by
    default instead. When ``uv_sources_enabled`` is False (the default),
    every `[tool.uv.sources]`-carrying dependency is excluded from resolve
    roots before it ever reaches :func:`resolve_closure` (so none of the six
    sites above can ever touch it) and an honest ``State.MISSING`` Package
    node is emitted in its place -- see :func:`_exclude_uv_sourced_roots` /
    :func:`_excluded_uv_source_node`. A repo with NO `[tool.uv.sources]` at
    all is unaffected either way (byte-identical ON vs OFF).

    When True, this function keeps the FULL source-aware behaviour built for
    it (source table threaded into the synthetic pyproject, workspace ->
    absolute-path rewrite, MISSING nodes for unhonourable sources, the
    repair-ladder / workspace-match protections) -- but that path is NOT SAFE
    for scored/benchmark runs for the six reasons above; it exists only for
    development of the source-aware install layer. The flag is read ONLY at
    the impure orchestration boundary (``scripts/run_v3_e2e.py``'s
    ``_uv_sources_enabled``, mirroring this codebase's ``V3_INCLUDE_SERVICES``
    convention -- see ``emit._is_service_reciped``) and passed down here as
    an explicit argument; this module never reads the environment itself.
    """
    host_executor = host_executor or LocalSubprocessExecutor()

    # Stage 1 — static import scan -> Import + Test nodes.
    graph = scan_to_nodes(repo_path)
    graph = _restamp(graph, {n.id for n in graph.nodes}, _SCAN_CYCLE)

    # Stage 1.5 — detect the TARGET container's env (Task 7) BEFORE root
    # selection (moved ahead of Stage 2, review fix: Stage 2's environment-
    # marker filter needs a real TargetEnv to evaluate against). ONE detected
    # TargetEnv replaces the previous two independent probes; explicit
    # target_python/target_platform (if given) patch the detected env rather
    # than skipping detection, so every other target-honest field (used by
    # marker evaluation in resolve_lock.py and roots.py) still reflects the
    # real container. The resulting `target_env` OBJECT (never decomposed into
    # separate strings) is what gets passed to select_roots below and
    # resolve_closure further down, so its RAW `platform_machine` (e.g. a
    # container reporting "arm64") reaches PEP 508 marker evaluation instead
    # of being lost to a normalized wheel-tag split.
    target_env = detect_target_env(container_executor)
    if target_python:
        target_env = replace(
            target_env,
            python_version=target_python,
            python_full=_pad_python_full(target_python),
        )
    if target_platform:
        target_env = replace(
            target_env,
            platform_machine=target_platform.split("-", 1)[0] or target_env.platform_machine,
            python_platform_tag=target_platform,
        )
    target_python = target_env.python_version

    # Stage 2 — manifest-declared-only, filtered resolver roots (imports never
    # generate roots; graph is passed but not consulted for root selection).
    # needed_extras gates which optional-dependency groups become roots at all
    # (Task 8) -- logged here since it silently determines closure membership.
    # target_env (Task 8 review fix) additionally drops a manifest dep whose
    # PEP 508 environment marker evaluates False for the TARGET (e.g. `foo ;
    # sys_platform == 'win32'` on a Linux target); see
    # roots._env_marker_excludes for the conservative keep-unless-certain rule
    # (extra-gated markers are left untouched -- that's needed_extras' job).
    logger.info("build_dep_graph: needed_extras=%s", sorted(needed_extras))
    roots = select_roots(
        repo_path, graph, needed_extras=needed_extras, target_env=target_env
    )
    # A `[tool.uv.sources]`-carrying dep (workspace/git/url/path/index) keeps
    # its TRUE `kind` (evidence.py no longer retags it), so `select_roots`
    # above already applied the SAME scope rules to it as to every other
    # declared dependency -- no second pass is needed (or wanted: a prior
    # post-selection reinstatement pass here bypassed scope filtering
    # entirely and was removed). Its real source still reaches the resolver
    # via `evidence.uv_sources`, threaded into `resolve_closure` below.

    # Stage 2a — anchor the resolve cutoff to the project's pinned era (HOST,
    # PyPI). A pinned old root (opencv-python==4.9.0.80) otherwise lets uv pull an
    # ABI-incompatible latest transitive dep (numpy 2.x); resolving as-of the pin
    # era keeps the closure compatible. Unset/unpinned -> None -> resolve latest.
    if exclude_newer is None:
        exclude_newer = compute_exclude_newer(roots)

    # Runtime-tier obligation: the container must run the targeted python minor.
    # Certified later by a host check (rc 0 iff sys.version_info matches); discovery
    # here never implies SATISFIED.
    from graph.ids import runtime_id as _runtime_id
    _maj, _min = target_python.split(".")[:2]
    _rt_check = f'python3 -c "import sys; sys.exit(0 if sys.version_info[:2]==({_maj},{_min}) else 1)"'
    graph = graph.with_node(
        Node(
            id=_runtime_id(target_python),
            type=NodeType.RUNTIME,
            name=f"python {target_python}",
            layer=Layer.RUNTIME,
            discovered_by=DiscoveredBy.STATIC_SCAN,
            state=State.UNKNOWN,
            version=target_python,
            check_command=_rt_check,
            resolved_python=target_python,
        )
    )
    # RECORD-union coverage oracle (Correction 3). Injected in tests (fake, no
    # network). The production DEFAULT (P1.5) is the composite: the cheap
    # post-install container reader for already-installed closure members, falling
    # through to the PRE-install PyPI wheel read for not-yet-installed repair
    # CANDIDATES (and resolved-but-failed-to-build dists) — so choose_provider can
    # confirm a candidate and repair is actually functional, not inert. The PyPI
    # read is behind an injected fetch seam (coverage._default_wheel_top_levels);
    # already-installed deps never trigger a PyPI call (composite short-circuit).
    record_provider = record_provider or composite_record_provider(
        default_record_provider(container_executor), pypi_record_provider()
    )

    # === Phase A — repair FIXPOINT: resolve -> install -> audit the runtime ===
    # imports against the resolved closure's RECORD-union coverage -> repair
    # under-declarations by adding AUDIT roots -> re-resolve until stable (P1.4).
    # Install stays inside the loop (re-install each round). The loop only rebinds
    # ``graph``; every round returns a new immutable graph.
    pre_resolve_ids = {n.id for n in graph.nodes}
    # Single evidence read backs BOTH the repair-candidate name set and the
    # `[tool.uv.sources]` config threaded into resolve_closure below -- see
    # `_declared_package_names_for_repair` for the repair-ladder HIGH-bug
    # protection (git-pinned `acme-sdk` must never be "repaired" into the
    # unrelated public PyPI package of the same name).
    evidence_for_resolve = collect_python_dependency_evidence(repo_path)
    declared_package_names = _declared_package_names_for_repair(evidence_for_resolve)
    uv_sourced_names = _uv_sourced_dist_names(evidence_for_resolve)

    # V3_UV_SOURCES default-OFF path (see this function's docstring): drop
    # every [tool.uv.sources]-carrying root up front and never thread its
    # source config into the resolver at all -- so none of the six egress
    # points documented above can ever reach it. A repo with no
    # `[tool.uv.sources]` (``evidence_for_resolve.uv_sources`` empty) takes
    # this branch as a hard no-op either way (see
    # :func:`_exclude_uv_sourced_roots`), so this is byte-identical to the
    # flag-ON path for that (the overwhelmingly common) case.
    uv_sources = evidence_for_resolve.uv_sources
    uv_indexes = evidence_for_resolve.uv_indexes
    workspace_members = evidence_for_resolve.uv_workspace_members

    # Fix 1 (docs/superpowers/plans/2026-07-14-post-measurement-fixes.md): a
    # PEP 508 direct reference is excluded from resolve roots UNCONDITIONALLY
    # -- regardless of ``uv_sources_enabled`` -- see
    # :func:`_exclude_direct_reference_roots`'s module note for why. A repo
    # with no direct references (``evidence_for_resolve.direct_reference_sources``
    # empty) takes this as a hard no-op, so it is byte-identical either way
    # for that (the overwhelmingly common) case.
    graph, roots = _exclude_direct_reference_roots(
        graph, roots, evidence_for_resolve.direct_reference_sources
    )

    if not uv_sources_enabled:
        graph, roots = _exclude_uv_sourced_roots(graph, roots, uv_sources)
        uv_sources = MappingProxyType({})
        uv_indexes = ()
        workspace_members = ()

    graph = _phase_a_fixpoint(
        graph,
        roots,
        host_executor,
        container_executor,
        record_provider,
        target_env=target_env,
        exclude_newer=exclude_newer,
        needed_extras=needed_extras,
        declared_package_names=declared_package_names,
        uv_sourced_names=uv_sourced_names,
        uv_sources=uv_sources,
        uv_indexes=uv_indexes,
        workspace_members=workspace_members,
        repo_path=repo_path,
        llm=llm_dist_guesser,
    )

    # Stage 3a'/3a''/3b — auxiliary node stages run ONCE after convergence (they
    # don't affect the missing-set): add the Project hub + subprocess CLI tools,
    # and seed the wheel-oracle build-essential prior. (The provisional Stage 3a
    # Import->Package heuristic is retired — Stage 4a's certified relink below is
    # now the sole Import->Package source.) Then stamp the RESOLVER discovery
    # cycle onto every node added since the resolve began that is NOT probe-
    # discovered — the install ran inside the loop and already surfaced its probe
    # Tool/SystemLib nodes, which keep the _PROBE_CYCLE stamp below (never
    # restamped back, and AUDIT provenance is set on discovered_by, not touched by
    # the cycle restamp).
    graph = _add_project_node(graph, repo_path)
    graph = add_subprocess_tool_nodes(graph, repo_path)
    graph = seed_wheel_oracle_prior(graph)
    # Stage 3b' — PROACTIVE wheel-soname priors: for each package the Phase-A
    # native_risk_from_lock stamp classified as a WHEEL (build_from_source is
    # False), read its target wheel's DT_NEEDED sonames (host, no install) and
    # seed them as RESOLVER/UNKNOWN SystemLib priors. Additive: non-native /
    # non-wheel closures (build_from_source None/True) add nothing -> byte-
    # identical. Phase-B's ldd_probe reconciles its observations onto these same
    # syslib_id nodes (reconcile_predicted), so no ordering change is needed.
    graph = wheel_preflight_probe(graph, host_executor, target_env)
    # Stage 3b'' — sdist build-dep prior: for each source-built Package
    # (build_from_source not False, incl. None/unclassified), seed the SPECIFIC
    # -dev capability priors (Bucket-B/B.1 curated + Debian Build-Depends +
    # PEP 725) PLUS the unconditional baseline binary:pkg-config (B3), UNIONING
    # with seed_wheel_oracle_prior's generic build-essential FLOOR at :568 (both
    # kept — distinct node ids never erase each other). Runs on the CONTAINER
    # executor: the Bucket-B.1 apt-installability guard shells out via
    # container_executor. RESOLVER/UNKNOWN nodes (never SATISFIED-at-seed), so
    # they fall inside the resolver_ids restamp just below.
    graph = seed_build_deps(graph, container_executor)
    # Stage 3b''' — R1b project-native-build-obligations: the repo-under-test's
    # OWN Project node gets the SAME build-dep-prior treatment as a source-built
    # Package (setup.py Extension.libraries + Debian Build-Depends keyed by the
    # project's OWN name + PEP 725 [external] read locally + the build-essential
    # floor), since it is otherwise categorically excluded from every prior
    # stage above (NodeType.PROJECT, no version -- see
    # docs/superpowers/research/R1-native-build-requirements.md). Pure-additive,
    # no-op for a repo with no native-build signal; RESOLVER/UNKNOWN nodes, so
    # they fall inside the resolver_ids restamp just below. No render-ordering
    # change needed (build_script.py's layer-then-capstone walk already renders
    # Layer.TOOLCHAIN before the Project capstone, edge-independently).
    graph = project_native_obligations(graph, repo_path, host_executor, container_executor)
    if shadow_config_lane and repo_path is not None:
        from graph.python.shadow import run_shadow_config_lane, _write_shadow_record
        record = run_shadow_config_lane(
            graph, repo_path, container_executor,
            declared=frozenset(declared_package_names),
        )
        _write_shadow_record(record)   # graph intentionally NOT rebound
    resolver_ids = {
        n.id
        for n in graph.nodes
        if n.id not in pre_resolve_ids and n.discovered_by is not DiscoveredBy.PROBE
    }
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)
    return graph, roots, target_env, exclude_newer


def _python_native_obligations(graph: DepGraph, container_executor: Executor) -> DepGraph:
    """Python PHASE 2 — "look then derive" on the CONVERGED closure.

    relink (certified Import->Package + honest ``unresolved`` flags) -> ldd
    (DT_NEEDED SystemLibs) -> import_probe (dlopen backstop) -> probe restamp
    (INV-9 order; relink FIRST). Self-contained WITHOUT a snapshot: the probe
    restamp stamps every ``discovered_by=PROBE`` node — no ``pre_resolve_ids``/
    ``pre_probe_ids`` exclusion is needed, because that clause is vacuous for the
    PROBE branch AND a Phase-B-entry snapshot would wrongly drop the PROBE Tool/
    SystemLib nodes ``install_closure`` already created during Phase A (FIX-1; see
    Task 4 proof). The verbatim build.py:610-634 inline stage comments carry over
    below — this docstring does NOT replace them.
    """
    # === Phase B — tier descent on the CONVERGED closure, "look then derive". ===
    # Stage 4a — certified Import->Package relink FIRST: this is Phase B's LOOK,
    # and the SOLE Import->Package source in construction.
    # ``packages_distributions()`` (CONTAINER) certifies Import->Package edges on
    # the converged closure and flags every still-unprovided non-optional import
    # ``unresolved`` (P0.3). It adds certified EDGES + honest data flags to EXISTING
    # Import nodes — it never adds a PROBE node — so it leaves the resolver/probe
    # cycle bookkeeping (below) untouched.
    graph = certified_import_links(graph, container_executor)
    # Stage 4.5 — AUTHORITATIVE run-time native-lib discovery: ldd each installed
    # package's extension .so files and surface ``=> not found`` sonames as
    # SystemLib nodes (DT_NEEDED ground truth). Derives system deps from the SAME
    # converged closure the relink just certified (needs the built .so — runs after
    # the loop, and after the relink LOOK).
    graph = ldd_probe(graph, container_executor)
    # import_probe is the dlopen BACKSTOP only: DT_NEEDED gaps are covered by
    # Stage 4.5 (ldd_probe); this catches libs loaded at run time via dlopen that
    # never appear in the binary's NEEDED list.
    graph = import_probe(graph, container_executor)
    probe_ids = {
        n.id
        for n in graph.nodes
        if n.discovered_by is DiscoveredBy.PROBE
    }
    graph = _restamp(graph, probe_ids, _PROBE_CYCLE)
    # Stage 4b — release-aware apt-name reconciliation against the TARGET image:
    # remap stale predicted/table names (e.g. libglib2.0-0 -> libglib2.0-0t64)
    # so the fix-candidate is correct for the actual base image. The last native
    # step — homed here (not in build_dep_graph) so the orchestrator calls no
    # native module directly.
    graph = reconcile_apt_names(graph, container_executor)
    return graph


def build_dep_graph(
    repo_path: str,
    container_executor: Executor,
    *,
    host_executor: Executor | None = None,
    target_python: str | None = None,
    target_platform: str | None = None,
    exclude_newer: str | None = None,
    needed_extras: frozenset[str] = frozenset(),
    record_provider: RecordProvider | None = None,
    uv_sources_enabled: bool = False,
    llm_dist_guesser: DistGuesser | None = None,
    shadow_config_lane: bool = False,
) -> DepGraph:
    """Build a host-certified dependency graph for ``repo_path``.

    ``container_executor`` runs install/probe/certify inside the target container;
    ``host_executor`` (default :class:`LocalSubprocessExecutor`) runs the
    host-side ``uv`` resolve.  A single :class:`TargetEnv` (Task 7) is detected
    from the container (``detect_target_env`` — one probe covering interpreter
    version, ``sys_platform``/``os_name``/``platform_machine``, and a glibc/musl
    guess for the wheel/uv platform tag used at PARSE time -- ``uv lock`` is
    universal and takes no platform flag of its own) so the resolve — and every
    PEP 508 marker it evaluates — targets the CONTAINER, never the host running
    this function.  ``target_python`` / ``target_platform`` remain accepted as
    caller overrides that patch the detected env (a hardcoded python would pin
    wheels for the wrong interpreter; an unset default would leak the dev host's
    own platform into the resolve).  The detected/patched ``TargetEnv`` OBJECT is
    passed straight into :func:`resolve_closure` (never decomposed into two
    strings first) so its RAW ``platform_machine`` — not a normalized wheel-tag
    stand-in — is what every marker evaluation downstream actually sees.  See
    the module docstring for the staged pipeline.  Returns the final immutable
    ``DepGraph``; certificates produced here are provisional (scratch-container
    scope) per design section 4.6.

    ``needed_extras`` (Task 8, targeted extras) is the set of
    ``[project.optional-dependencies]`` / ``extras_require`` group names this
    build actually needs (e.g. ``{"test"}`` when the goal is running the test
    suite). It is threaded, unchanged, into both :func:`select_roots` (which
    gates which optional groups become roots at all — fixing the prior
    "union every group" bug) and :func:`resolve_closure` (which records the
    chosen groups' scope in the resolver's temp pyproject). The default is
    deliberately runtime-only (``frozenset()``), NOT a union of every declared
    group. **Seam, not policy**: this function does not itself discover which
    extras a repo's CI/tox/Makefile actually invokes (e.g. `pip install -e
    .[test]`) — that discovery is separate future enrichment (cluster-1); a
    caller that already knows the needed groups passes them here.

    ``record_provider`` (P1.4/P1.5) is the RECORD-union coverage oracle the
    Phase-A repair fixpoint audits imports against: ``dist name -> {top-level
    modules}`` or ``None`` (no wheel to read). Injected in tests (a fake, no
    network); when omitted the production DEFAULT is
    :func:`coverage.composite_record_provider` over the cheap post-install
    container reader (:func:`coverage.default_record_provider`) and the PRE-install
    PyPI wheel reader (:func:`coverage.pypi_record_provider`) — so a not-yet-
    installed repair candidate is grounded from PyPI (P1.5, making production
    repair functional) while already-installed closure members stay network-free.

    ``uv_sources_enabled`` (V3_UV_SOURCES, default OFF) -- see
    :func:`_python_package_obligations`'s docstring for the full false-green
    rationale for why this defaults OFF and what turning it on actually
    means. Threaded straight through the ``EcosystemProvider`` seam
    (``EcosystemProvider.package_obligations`` accepts-and-ignores it for any
    non-Python provider); this function never reads the environment itself.

    ``llm_dist_guesser`` (default ``None``) is the injected install-lane dist
    guesser the Phase-A repair fixpoint calls on a pipreqs map MISS, fed each
    unresolved Import's used-symbols. ``None`` keeps repair purely deterministic
    (byte-identical to the pre-guesser behavior). It is threaded end-to-end through
    the ``EcosystemProvider`` seam (``EcosystemProvider.package_obligations`` ->
    ``PythonProvider.package_obligations`` -> :func:`_python_package_obligations` ->
    :func:`_phase_a_fixpoint`), so a live guesser passed here actually reaches the
    fixpoint; non-Python providers accept-and-ignore it.
    """
    # Function-local import breaks the build<->provider cycle: by the time this
    # runs, build.py is fully loaded, so graph.python.provider (which imports
    # build helpers) resolves cleanly.
    from graph.contracts.registry import PROVIDERS, select_provider

    # default=PROVIDERS[0] (the PythonProvider) preserves "build_dep_graph never
    # rejects a repo": if NO provider clears the detect threshold (degenerate /
    # manifest-less / *.py-less repo), dispatch STILL routes to Python instead of
    # raising LookupError — zero-impact vs the pre-seam unconditional-accept path.
    provider = select_provider(repo_path, PROVIDERS, default=PROVIDERS[0])  # dispatch
    graph, roots, target_env, exclude_newer = provider.package_obligations(
        repo_path,
        container_executor,
        host_executor=host_executor,
        target_python=target_python,
        target_platform=target_platform,
        exclude_newer=exclude_newer,
        needed_extras=needed_extras,
        record_provider=record_provider,
        uv_sources_enabled=uv_sources_enabled,
        llm_dist_guesser=llm_dist_guesser,
        shadow_config_lane=shadow_config_lane,
    )
    # NOTE: only `graph` flows onward; roots/target_env/exclude_newer are
    # provider-composition / test-visibility surface (spec extraction boundary).

    graph = provider.native_obligations(graph, container_executor)

    # Stage 5 — host certification in the container (layer-ordered; flips state).
    graph = certify_all(graph, container_executor, cycle=_CERTIFY_CYCLE)

    return graph
