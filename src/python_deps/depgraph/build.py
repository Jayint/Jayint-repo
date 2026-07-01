"""Stage orchestrator — repo path in, host-certified ``DepGraph`` out.

Wires the pipeline of
``docs/DESIGN-static-probe-certified-dependency-graph.md`` /
``docs/superpowers/specs/2026-06-23-uv-enriched-depgraph.md`` in order:

    1. scan      static import scan          -> Import + Test nodes   (cycle 1)
    2. map       roots.select_roots          -> resolver roots
    3. resolve   uv.lock closure (HOST)      -> Package nodes/edges   (cycle 2)
    3b. seed     predicted native nodes      -> Tool/SystemLib        (cycle 2)
    4. probe     install + import (CONTAINER)-> SystemLib/Tool nodes  (cycle 3)
    4.5 ldd      ldd ext .so (CONTAINER)     -> run-time SystemLib     (cycle 3)
    5. certify   host check_commands (CONTAINER) -> node ``state``    (cycle 4)

**Executor split (spec "Architecture change"):** resolution is HOST-side — ``uv``
cross-platform resolves the container target without a container interpreter — so
it runs through ``host_executor``.  Install/probe/certify must observe the real
target environment, so they run through ``container_executor``.  Both default-safe
for unit tests (a single ``FakeExecutor`` can be injected for both).

Discovery order and execution order differ (design 3.3 / 10.10): probing
discovers a SystemLib *after* installing the pip package that needs it, but
certification then runs in execution layer order (system before pip).  Every
stage returns a NEW immutable graph; this function only ever rebinds ``graph``.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import replace

try:  # tomllib is stdlib on 3.11+; fall back to the tomli backport on 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib

from python_deps.depgraph.apt_verify import reconcile_apt_names
from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.executor import Executor, LocalSubprocessExecutor
from python_deps.depgraph.ids import TEST_NODE_ID, project_id
from python_deps.depgraph.ldd_probe import ldd_probe
from python_deps.depgraph.pins import compute_exclude_newer
from python_deps.depgraph.probe import import_probe, install_closure
from python_deps.depgraph.relink import certified_import_links
from python_deps.depgraph.resolve import (
    link_imports_to_packages,
    resolve_closure,
)
from python_deps.depgraph.roots import select_roots
from python_deps.depgraph.scan import scan_to_nodes
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
from python_deps.depgraph.seed import seed_predicted_native
from python_deps.depgraph.target_env import detect_target_env
from python_deps.evidence import collect_python_dependency_evidence
from python_deps.import_mapping import normalize_package_name

logger = logging.getLogger(__name__)

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


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


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
    graph = graph.with_node(
        Node(
            id=proj_id,
            type=NodeType.PROJECT,
            name=name,
            layer=Layer.PIP,
            discovered_by=DiscoveredBy.STATIC_SCAN,
            state=State.UNKNOWN,
            provenance=os.path.join(repo_path, "pyproject.toml"),
        )
    )
    graph = graph.with_edge(
        Edge(src=TEST_NODE_ID, dst=proj_id, relation=EdgeType.REQUIRES, origin="project")
    )

    canon_to_pkg = {
        _canon(n.name): n.id for n in graph.nodes if n.type is NodeType.PACKAGE
    }
    evidence = collect_python_dependency_evidence(repo_path)
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


def build_dep_graph(
    repo_path: str,
    container_executor: Executor,
    *,
    host_executor: Executor | None = None,
    target_python: str | None = None,
    target_platform: str | None = None,
    exclude_newer: str | None = None,
    needed_extras: frozenset[str] = frozenset(),
) -> DepGraph:
    """Build a host-certified dependency graph for ``repo_path``.

    ``container_executor`` runs install/probe/certify inside the target container;
    ``host_executor`` (default :class:`LocalSubprocessExecutor`) runs the
    host-side ``uv`` resolve.  A single :class:`TargetEnv` (Task 7) is detected
    from the container (``detect_target_env`` — one probe covering interpreter
    version, ``sys_platform``/``os_name``/``platform_machine``, and a glibc/musl
    guess for the ``uv lock --python-platform`` tag) so the resolve — and every
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
    """
    host_executor = host_executor or LocalSubprocessExecutor()

    # Stage 1 — static import scan -> Import + Test nodes.
    graph = scan_to_nodes(repo_path)
    graph = _restamp(graph, {n.id for n in graph.nodes}, _SCAN_CYCLE)

    # Stage 2 — manifest-first, scan-gap-filled, filtered resolver roots.
    # needed_extras gates which optional-dependency groups become roots at all
    # (Task 8) -- logged here since it silently determines closure membership.
    logger.info("build_dep_graph: needed_extras=%s", sorted(needed_extras))
    roots = select_roots(repo_path, graph, needed_extras=needed_extras)

    # Stage 2a — anchor the resolve cutoff to the project's pinned era (HOST,
    # PyPI). A pinned old root (opencv-python==4.9.0.80) otherwise lets uv pull an
    # ABI-incompatible latest transitive dep (numpy 2.x); resolving as-of the pin
    # era keeps the closure compatible. Unset/unpinned -> None -> resolve latest.
    if exclude_newer is None:
        exclude_newer = compute_exclude_newer(roots)

    # Stage 3 — HOST-side uv resolve, targeted at the container. ONE detected
    # TargetEnv replaces the previous two independent probes; explicit
    # target_python/target_platform (if given) patch the detected env rather
    # than skipping detection, so every other target-honest field (used by
    # marker evaluation in resolve_lock.py) still reflects the real container.
    # The resulting `target_env` OBJECT (never decomposed into separate
    # strings) is what gets passed to resolve_closure below, so its RAW
    # `platform_machine` (e.g. a container reporting "arm64") reaches PEP 508
    # marker evaluation instead of being lost to a normalized wheel-tag split.
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

    # Runtime-tier obligation: the container must run the targeted python minor.
    # Certified later by a host check (rc 0 iff sys.version_info matches); discovery
    # here never implies SATISFIED.
    from python_deps.depgraph.ids import runtime_id as _runtime_id
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
    pkg_nodes, pkg_edges = resolve_closure(
        roots,
        host_executor,
        target_env=target_env,
        exclude_newer=exclude_newer,
        extras=needed_extras,
    )
    pre_resolve_ids = {n.id for n in graph.nodes}
    for node in pkg_nodes:
        graph = graph.with_node(node)
    for edge in pkg_edges:
        graph = graph.with_edge(edge)

    # Stage 3a — reconcile: link EVERY Import to its resolved Package (covers
    # manifest-declared deps whose root carried import_id=None, which would
    # otherwise leave the scanned Import node orphaned from its Package).
    graph = link_imports_to_packages(graph)

    # Stage 3a' — Project hub: connect declared direct deps to a Project node so
    # the package layer is fully connected (runtime deps off Project, test deps
    # off the Test goal).
    graph = _add_project_node(graph, repo_path)

    # Stage 3b — predicted native Tool/SystemLib nodes (resolver-origin).
    # PACKAGE_TO_SYSTEM_DEPS here is a PROACTIVE FALLBACK (pre-install / install-fail
    # hint); Stage 4.5 ldd_probe is the authoritative run-time native-lib source.
    graph = seed_predicted_native(graph)
    resolver_ids = {n.id for n in graph.nodes} - pre_resolve_ids
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)

    # Stage 4 — CONTAINER probe: install once (build-time gaps -> Tool) then
    # import-probe (run-time gaps -> SystemLib); predictions reconcile in place.
    pre_probe_ids = {n.id for n in graph.nodes}
    graph = install_closure(graph, container_executor)
    # Stage 4.5 — AUTHORITATIVE run-time native-lib discovery: ldd each installed
    # package's extension .so files and surface ``=> not found`` sonames as
    # SystemLib nodes (DT_NEEDED ground truth). Runs after install (needs the
    # built .so) and before relink/import-probe. The curated table (Stage 3b) is
    # demoted to a proactive fallback; ldd is the source of truth here.
    graph = ldd_probe(graph, container_executor)
    # Stage 4a — certified Import->Package relink (packages_distributions, CONTAINER).
    graph = certified_import_links(graph, container_executor)
    # import_probe is now the dlopen BACKSTOP only: DT_NEEDED gaps are covered by
    # Stage 4.5 (ldd_probe); this catches libs loaded at run time via dlopen that
    # never appear in the binary's NEEDED list.
    graph = import_probe(graph, container_executor)
    probe_ids = {n.id for n in graph.nodes} - pre_probe_ids
    graph = _restamp(graph, probe_ids, _PROBE_CYCLE)

    # Stage 4b — release-aware apt-name reconciliation against the TARGET image:
    # remap stale predicted/table names (e.g. libglib2.0-0 -> libglib2.0-0t64)
    # so the fix-candidate is correct for the actual base image.
    graph = reconcile_apt_names(graph, container_executor)

    # Stage 5 — host certification in the container (layer-ordered; flips state).
    graph = certify_all(graph, container_executor, cycle=_CERTIFY_CYCLE)

    return graph
