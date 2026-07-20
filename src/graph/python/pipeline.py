"""Two-phase Python dependency obligations + resolve-root exclusion.

Split (3c-5) from the former ``core/build.py`` — carries its full staged-pipeline
module docstring:

    scan/map   static import scan + declared-ONLY roots -> Import/Test  (cycle 1)

    Phase A -- "is it PROVIDED?"  oracle = RECORD-union coverage. A bounded resolve
       (HOST uv) -> install (CONTAINER) -> look -> repair FIXPOINT
       (``fixpoint._phase_a_fixpoint``). Each round audits the runtime imports
       against the RESOLVED closure's RECORD-union coverage and repairs an
       under-declared import by grounding + adding an AUDIT root, re-resolving until
       coverage is stable.                                                 (cycle 2)

    Phase B -- "does it LOAD / who PROVIDES it?"  oracle = the live CONVERGED
    container. A single tier descent "look then derive": relink -> ldd -> probe ->
    apt.                                                                    (cycle 3)

**Executor split:** resolution is HOST-side (``host_executor``); install/probe/
certify observe the real target env (``container_executor``). Both default-safe for
unit tests. ``_python_package_obligations`` (Phase 1) and ``_python_native_obligations``
(Phase 2) are the ``EcosystemProvider`` seam bodies; ``select_roots`` and the
record-provider constructors are module-level imports here so a test patch reaches
their call site.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from types import MappingProxyType

from graph.contracts.executor import Executor
from graph.executors import LocalSubprocessExecutor
from graph.model import package_id
from graph.model import (
    DepGraph,
    DiscoveredBy,
    Layer,
    Node,
    NodeType,
    State,
)
from graph.python.fixpoint import _FixpointResumeState, _phase_a_fixpoint
from graph.python.invocation_resolver import resolve
from graph.python.lanes.config.cure import CureResult, run_cure, stamp_scratch_certified
from graph.python.lanes.install.ground import (
    DistGuesser,
    RecordProvider,
    composite_record_provider,
    default_record_provider,
    pypi_record_provider,
)
from graph.python.lanes.install.link import certified_import_links
from graph.python.lanes.install.resolve import _req_name
from graph.python.lanes.install.resolve_lock import compute_exclude_newer
from graph.python.lanes.install.roots import select_roots
from graph.python.native.apt import reconcile_apt_names
from graph.python.native.build_deps import seed_build_deps, seed_wheel_oracle_prior
from graph.python.native.runtime_tools import seed_runtime_tools
from graph.python.native.project_native import project_native_obligations
from graph.python.native.system_libs import import_probe, ldd_probe
from graph.python.native.ctypes_scan import add_ctypes_runtime_libs
from graph.python.native.wheel import wheel_preflight_probe
from graph.python.read.evidence import collect_python_dependency_evidence
from graph.python.read.scan import clear_external_names, scan_to_nodes
from graph.python.read.subprocess_scan import add_subprocess_tool_nodes
from graph.python.read.target_env import detect_target_env
from graph.python.route.arbitrate import arbitrate
from graph.python.route.classify import (
    LaneRouting,
    apply_routing,
    classify,
    probe_target_stdlib,
    wire_spine,
)
from graph.python.skeleton import (
    _PROBE_CYCLE,
    _RESOLVER_CYCLE,
    _SCAN_CYCLE,
    _add_project_node,
    _pad_python_full,
    _restamp,
)
from graph.python.util.import_mapping import normalize_package_name

logger = logging.getLogger(__name__)

# PEP-503 canonicalizer, aliased to its util-natured owner (one shared
# canonicalizer, not a build-local copy) — see the split note in git history.
_canon = normalize_package_name


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


def _soft_declared_dist_names(evidence) -> frozenset[str]:
    """Canonical soft-declared dist names fed to the Phase-A declared repair rung.

    EXCLUDES PEP 508 direct references (``name @ <url>``): a soft file pinning a
    dist to a git/URL source declares a NON-PyPI provider, so the rung must never
    propose that dist's public-PyPI namesake as a repair candidate -- the same
    protection `_declared_package_names_for_repair` gives uv-sourced names.
    ``Requirement.url`` is None for a plain requirement, the URL for a direct
    reference.
    """
    return frozenset(
        _canon(r.name)
        for r in evidence.soft_declared_dependencies
        if not r.url
    )


def _route_config_lane(
    graph: DepGraph,
    repo_path: str,
    container_executor: Executor,
    *,
    declared: frozenset[str],
) -> tuple[DepGraph, LaneRouting | None]:
    """THE FLIP, first half — the config-lane classifier, LIVE and BEFORE Phase A.

    Probe the TARGET stdlib once, statically route every scanned top-level import
    into internal / external / deferred (``classify``), attach the edge-less internal
    ``Module`` nodes AND stamp ``data['routed_provider']='module'`` on first-party
    Import nodes (``apply_routing``). Runs BEFORE the Phase-A fixpoint on purpose: the
    stamp + the returned ``deferred`` set are exactly what the lane-aware
    ``_missing_import_nodes`` filter reads, so a first-party name (now minted as an
    Import node by the route-not-drop ``scan``) never inflates the repair bound nor
    reaches the LLM dist-guesser.

    Returns ``(graph, routing)``; the caller threads ``routing.deferred`` into the
    fixpoint and re-uses ``routing`` to wire the spine after the Project node exists
    (:func:`_finalize_config_lane`) — the classifier runs ONCE per construction.

    Fail CLOSED (not open): post-flip the classifier is load-bearing for SAFETY — the
    raw ``scan`` graph carries first-party/collision Import nodes, and if classification
    raised they would flow into Phase A and the LLM dist-guesser (the false-green
    vector). So on ANY classify exception, construction still proceeds (never aborts),
    but the LANE fails closed: the graph is reduced to the PRE-FLIP clear-external set
    (:func:`scan.clear_external_names`) — first-party/unclassifiable Import nodes are
    DROPPED, never treated as external — and ``routing=None`` signals the caller to
    stamp ``routing_failed`` on the Project node for attribution.
    """
    try:
        stdlib = probe_target_stdlib(container_executor)
        routing = classify(repo_path, target_stdlib=stdlib, declared=declared)
        return apply_routing(graph, routing), routing
    except Exception:  # classify raised -> lane fails CLOSED, construction proceeds
        logger.error(
            "live config-lane classify failed; failing CLOSED to the pre-flip "
            "clear-external set (first-party names dropped, never installed)",
            exc_info=True,
        )
        try:
            keep = clear_external_names(repo_path)
            reduced = graph
            for node in graph.nodes:
                if node.type is NodeType.IMPORT and node.name.split(".", 1)[0] not in keep:
                    reduced = reduced.without_node(node.id)
            return reduced, None
        except Exception:  # even the fallback failed -> drop ALL Import nodes (max-safe)
            logger.critical(
                "config-lane fail-closed fallback ALSO failed; dropping every Import "
                "node so no unclassified name can reach the install lane", exc_info=True
            )
            reduced = graph
            for node in graph.nodes:
                if node.type is NodeType.IMPORT:
                    reduced = reduced.without_node(node.id)
            return reduced, None


def _finalize_config_lane(
    graph: DepGraph,
    routing: LaneRouting | None,
) -> DepGraph:
    """THE FLIP, second half — wire the goal spine + record routing AS GRAPH DATA.

    Runs AFTER ``_add_project_node`` (the spine's ``project->module`` /
    ``module->import`` edges need the Project node). Draws the spine (``wire_spine``,
    the flat ``Test->Import`` hub's replacement) and stamps the routing partition on
    the ``Project`` node -- ``routing_internal`` (internal tops) and
    ``routing_deferred`` (collision-zone names) -- the durable record Task-3
    arbitration, ``relink``'s launder guard, and the repair agent all read (never a
    side channel). Tuples keep ``Node.data`` immutable.

    Additive to render: Module/Import nodes and the spine edges between them are not
    reciped, and the project-data keys are inert to the renderer, so the emitted
    ``setup.sh`` is byte-identical to pre-flip apart from Task-2's capstone ``#@check``.

    ``routing is None`` means the classify pass FAILED CLOSED (:func:`_route_config_lane`
    already reduced the graph to the safe clear-external set): stamp
    ``routing_failed=True`` on the Project node so the run is attributable, then no-op.
    Any exception here is logged and the graph is returned as-is.
    """
    if routing is None:
        project = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
        if project is not None:
            return graph.with_node(project.with_data(routing_failed=True))
        return graph
    try:
        new = wire_spine(graph, routing)
        project = next((n for n in new.nodes if n.type is NodeType.PROJECT), None)
        if project is not None:
            new = new.with_node(
                project.with_data(
                    routing_internal=tuple(sorted(top for top, _ in routing.internal)),
                    routing_deferred=tuple(sorted(routing.deferred)),
                )
            )
        return new
    except Exception:  # never let spine wiring fail construction
        logger.warning(
            "live config-lane spine wiring failed; leaving graph unrouted",
            exc_info=True,
        )
        return graph


def _assert_no_causal_recipe(graph: DepGraph) -> DepGraph:
    """Construction invariant: a causal node (IMPORT / MODULE) must never carry an
    install recipe. IMPORT/MODULE are naming/first-party nodes — the renderer emits
    a pip/apt line only for a reciped Package/SystemLib/Tool (or the installable
    Project capstone), never for these; the spine must keep that invariant.

    Fails LOUDLY under test (``assert`` — active with ``__debug__``, i.e. pytest) and
    logs-and-continues in production (``python -O`` strips the assert), matching the
    pipeline's fail-open idiom. A no-op transform; returns ``graph`` unchanged."""
    from graph.compile.emit import _is_reciped  # lazy: avoid load-order coupling
    offenders = [
        n.id for n in graph.nodes
        if n.type in (NodeType.IMPORT, NodeType.MODULE)
        and (_is_reciped(n) or n.setup_commands)
    ]
    if offenders:
        logger.error(
            "construction invariant violated: causal node(s) carry an install "
            "recipe (IMPORT/MODULE must carry none): %s", offenders
        )
        assert not offenders, f"IMPORT/MODULE nodes must carry no recipe: {offenders}"
    return graph


def _apply_live_config_cure(
    graph: DepGraph,
    repo_path: str,
    container_executor: Executor,
) -> DepGraph:
    """Stage C Task 2 — run the editable-install cure LIVE and reconcile it with
    the render-time poison.

    Resolves the deterministic :class:`TestEnvPlan` (:func:`resolve`), runs the
    2-rung editable-install + ``pytest --collect-only`` collect-gate inside the
    scratch container (:func:`run_cure`, which ``cd``s into the mounted repo), and
    stamps the ``Project`` node ``scratch_certified`` (+ ``cure_rung`` /
    ``cure_collect_ok`` evidence) via :func:`stamp_scratch_certified`.

    The stamp is a no-op unless the cure actually installed the project
    (``cure.ok``): a repo with no installable metadata (no ``pyproject``/
    ``setup.py``, e.g. proxy_pool) fails both rungs fast, is never stamped, and the
    render-time poison (``emit._poison_project_certificate``) applies exactly as
    today -- its ``setup.sh`` stays byte-identical. Only a CURED repo keeps its
    capstone ``check_command`` (the poison gate honours ``scratch_certified``), so
    a ``#@check`` renders on its capstone -- the intended behavioural change.

    Fail-open like the Task-1 classify pass: any exception (Docker hiccup, resolver
    error) is logged and the ORIGINAL graph is returned unchanged, so the live cure
    can never introduce a new construction failure mode.
    """
    try:
        plan = resolve(repo_path)
        cure = run_cure(container_executor, plan)
        return stamp_scratch_certified(graph, cure)
    except Exception:  # never let the live config cure fail construction
        logger.warning(
            "live config-lane cure failed; leaving project uncertified", exc_info=True
        )
        return graph


def _project_node(graph: DepGraph) -> Node | None:
    return next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)


def _cure_from_project(project: Node) -> CureResult:
    """Reconstruct the minimal :class:`CureResult` arbitration reads from Task 2's
    durable ``scratch_certified`` stamp. ``arbitrate`` consults only ``cure.ok``, so
    an UNSTAMPED project (the cure failed or the repo had no installable metadata)
    yields ``ok=False`` and the arbiter returns every deferred name unresolved with
    ZERO container probes. Reusing the stamp avoids a second in-container editable
    install — the real cure already ran live in :func:`_apply_live_config_cure`
    moments earlier — while keeping arbitration keyed on the same durable record
    the repair agent and Task 4 read (never a side channel)."""
    if project.data.get("scratch_certified") is not True:
        return CureResult(False, "", False, "not scratch-certified")
    return CureResult(
        True,
        str(project.data.get("cure_rung", "")),
        bool(project.data.get("cure_collect_ok")),
        "reconstructed from scratch_certified stamp",
    )


def _stamp_provisional(
    graph: DepGraph, fallthrough: frozenset[str], cure: CureResult
) -> DepGraph:
    """Stamp ``data["provisional"]`` (the local-collision evidence, with a named
    owner) on every fallthrough-minted Package node — matched by CANONICAL dist
    name, so only the fallthrough root itself is flagged, never a transitive
    closure member that happens to ride in on the same re-resolve. The payload is a
    plain JSON-able dict, so it survives ``Node.to_dict``/``from_dict`` and any
    downstream run artifact that serializes the graph carries the
    "certified-with-provisional" evidence."""
    wanted = {_canon(name) for name in fallthrough}
    new = graph
    for node in graph.nodes:
        if node.type is NodeType.PACKAGE and _canon(node.name) in wanted:
            new = new.with_node(
                node.with_data(
                    provisional={
                        "name": node.name,
                        "reason": (
                            "local-collision fallthrough: name is both a repo "
                            "module and a PyPI distribution, and did not import "
                            "locally under the cure"
                        ),
                        "cure_rung": cure.rung,
                    }
                )
            )
    return new


def _apply_live_arbitration(
    graph: DepGraph,
    repo_path: str,
    container_executor: Executor,
    *,
    reenter,
) -> DepGraph:
    """Stage C Task 3 — collision-zone arbitration runs LIVE after the cure, and
    genuine fallthroughs re-enter the install lane under a provisional flag.

    Reads the collision-zone names Task 1 stamped (``routing_deferred``); when
    empty — the common case — returns IMMEDIATELY with ZERO extra container calls.
    Otherwise reconstructs the cure outcome from Task 2's ``scratch_certified``
    stamp and runs the cure-gated, exception-aware :func:`arbitrate` (a cure that
    did not succeed -> every deferred name unresolved, no probe). The three verdict
    partitions are recorded as sorted-tuple graph data on the ``Project`` node
    (``routing_arbitrated_local`` / ``routing_fallthrough`` / ``routing_unresolved``
    — the durable record the repair agent and Task 4 read). Each ``fallthrough``
    name then becomes an install-lane root and RE-ENTERS the Phase-A fixpoint via
    ``reenter`` (which RESUMES the first run's threaded state — no re-attempt of a
    given-up name, no duplicate/over-dropped pkg nodes — and re-runs the wheel-
    preflight soname prior; a bare ``pip install`` and a fresh unthreaded fixpoint
    are both forbidden), and every fallthrough-minted Package node is flagged
    ``data["provisional"]``.

    Fail-open like Tasks 1–2: any exception is logged and the graph is returned
    as-is (unchanged when the failure is in ``arbitrate``/``resolve`` before any
    stamp; verdicts KEPT but the risky re-entry skipped when the fixpoint itself
    raises), so live arbitration can never propagate a construction failure.

    Pre-flip downscope (route-not-drop is Task 4): the scan still DROPS first-party/
    local names, so a collision name has NO Import node today. Recording the
    ``resolves_local`` verdict therefore has no Import node to route to a Module
    (the verdict is still stamped, for the flip and the repair agent); only the
    ``fallthrough`` install-lane re-entry has a live graph effect pre-flip.
    """
    try:
        project = _project_node(graph)
        if project is None:
            return graph
        deferred = frozenset(project.data.get("routing_deferred", ()))
        if not deferred:
            return graph  # no collision zone -> zero container calls (the common case)
        plan = resolve(repo_path)
        cure = _cure_from_project(project)
        arb = arbitrate(container_executor, plan, cure, deferred)
        graph = graph.with_node(
            project.with_data(
                routing_arbitrated_local=tuple(sorted(arb.resolves_local)),
                routing_fallthrough=tuple(sorted(arb.fallthrough)),
                routing_unresolved=tuple(sorted(arb.unresolved)),
            )
        )
        if not arb.fallthrough:
            return graph
        extra_roots = [(None, name) for name in sorted(arb.fallthrough)]
        graph = reenter(graph, extra_roots)
        return _stamp_provisional(graph, arb.fallthrough, cure)
    except Exception:  # never let live arbitration fail construction
        logger.warning(
            "live config-lane arbitration failed; leaving graph unrouted",
            exc_info=True,
        )
        return graph


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
    from graph.model import runtime_id as _runtime_id
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
    # Single evidence read backs BOTH the repair-candidate name set and the
    # `[tool.uv.sources]` config threaded into resolve_closure below -- see
    # `_declared_package_names_for_repair` for the repair-ladder HIGH-bug
    # protection (git-pinned `acme-sdk` must never be "repaired" into the
    # unrelated public PyPI package of the same name).
    evidence_for_resolve = collect_python_dependency_evidence(repo_path)
    declared_package_names = _declared_package_names_for_repair(evidence_for_resolve)

    # Stage 2.5 — LIVE config-lane classify (THE FLIP: route-not-drop). Runs BEFORE
    # Phase A so the lane-aware fixpoint filter is armed: Module nodes are attached
    # and every first-party Import node (now minted by the route-not-drop ``scan``)
    # is stamped ``routed_provider='module'``, while the collision-zone ``deferred``
    # set is threaded into the fixpoint below -- so a first-party name never inflates
    # the repair bound nor reaches the LLM dist-guesser. ``routing`` is re-used to
    # wire the spine after the Project node exists (``_finalize_config_lane``).
    routing = None
    deferred_names: frozenset[str] = frozenset()
    if repo_path is not None:
        graph, routing = _route_config_lane(
            graph, repo_path, container_executor,
            declared=frozenset(declared_package_names),
        )
        if routing is not None:
            deferred_names = routing.deferred

    # pre_resolve_ids captured AFTER the classify pass so its Module/Import nodes are
    # "pre-resolve" state (kept out of the resolver-cycle restamp -- they retain their
    # CLASSIFIER / STATIC_SCAN discovery stamps).
    pre_resolve_ids = {n.id for n in graph.nodes}
    uv_sourced_names = _uv_sourced_dist_names(evidence_for_resolve)
    # Soft-declared dist names (canonical): deps the repo declared in a *soft*
    # requirements file (no matching pyproject entry). Fed to Phase A's declared
    # repair rung so an under-declared identity-named import (e.g. `import fastapi`
    # with `fastapi` only in a soft requirements.txt) is repaired from the
    # declaration, not left to pipreqs/LLM guessing. Empty for a repo with no soft
    # requirements -> byte-identical to the pre-rung behavior. URL-pinned direct
    # references are excluded (see `_soft_declared_dist_names`) so a git-pinned
    # soft dep is never repaired into its unrelated public-PyPI namesake.
    soft_declared = _soft_declared_dist_names(evidence_for_resolve)

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

    # Phase-A resume state: the first run populates it (attempted pairs + the final
    # resolved-closure ids) so a Stage-C-Task-3 fallthrough re-entry can RESUME the
    # fixpoint (no re-attempt of a given-up name, no over-drop of MISSING placeholder
    # nodes) instead of restarting it from scratch.
    phase_a_state = _FixpointResumeState()
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
        declared_dists=soft_declared,
        uv_sourced_names=uv_sourced_names,
        uv_sources=uv_sources,
        uv_indexes=uv_indexes,
        workspace_members=workspace_members,
        repo_path=repo_path,
        llm=llm_dist_guesser,
        deferred=deferred_names,
        resume=phase_a_state,
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
    # Stage 3a' — curated runtime-executable prior: a binary:<tool> Tool node for
    # each closure package that shells out to an external CLI (git family, pandoc,
    # graphviz/dot, poppler) that no static sensor can observe. RESOLVER/UNKNOWN ->
    # falls inside the resolver_ids restamp below.
    graph = seed_runtime_tools(graph)
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
        if n.id not in pre_resolve_ids
        and n.discovered_by not in (DiscoveredBy.PROBE, DiscoveredBy.GOAL)
    }
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)
    # Stage C — LIVE config lane, second half (THE FLIP): wire the goal spine and
    # record the routing partition on the Project node. The classify pass already
    # ran BEFORE Phase A (``_route_config_lane`` above); here we only draw the
    # ``project->module->import`` edges (which need the now-present Project node) and
    # stamp ``routing_internal`` / ``routing_deferred`` -- the durable record Tasks
    # 2-3 read. ``routing is None`` (classify failed / no repo_path) -> a no-op.
    if repo_path is not None:
        graph = _finalize_config_lane(graph, routing)
        # Stage C Task 2 — run the editable-install cure LIVE immediately after the
        # classify pass and reconcile the render-time poison: a successful in-
        # container cure stamps the Project node scratch_certified (so its capstone
        # check survives render as a #@check); a failed/absent cure leaves the graph
        # -- and the rendered setup.sh -- byte-identical to today.
        graph = _apply_live_config_cure(graph, repo_path, container_executor)
        # Stage C Task 3 — collision-zone arbitration after the cure. Each genuine
        # fallthrough (a deferred collision name that does NOT import locally under
        # the cure) re-enters the Phase-A fixpoint over the ORIGINAL roots augmented
        # with that name, RESUMING ``phase_a_state`` (threaded attempted set + the
        # last closure's ids -> no re-attempt, no duplicate/over-dropped pkg nodes),
        # then re-runs the wheel-preflight soname prior so a native fallthrough dist
        # (lxml-class) gets the same PRE-install DT_NEEDED discovery Phase-A dists
        # get. Reuses the SAME resolver/coverage seams as a closure so arbitration
        # stays agnostic to the resolve-plane's parameters.
        def _reenter(g, extra_roots):
            proj = _project_node(g)
            reentry_deferred = (
                frozenset(proj.data.get("routing_deferred", ())) if proj else frozenset()
            )
            g = _phase_a_fixpoint(
                g,
                roots + list(extra_roots),
                host_executor,
                container_executor,
                record_provider,
                target_env=target_env,
                exclude_newer=exclude_newer,
                needed_extras=needed_extras,
                declared_package_names=declared_package_names,
                declared_dists=soft_declared,
                uv_sourced_names=uv_sourced_names,
                uv_sources=uv_sources,
                uv_indexes=uv_indexes,
                workspace_members=workspace_members,
                repo_path=repo_path,
                llm=llm_dist_guesser,
                deferred=reentry_deferred,
                resume=phase_a_state,
            )
            # F4: pre-install wheel DT_NEEDED soname discovery over the (now-present)
            # fallthrough dists — the Phase-A proactive prior, not the Phase-B ldd
            # backstop. Idempotent for the already-probed closure (soname-keyed upsert).
            return wheel_preflight_probe(g, host_executor, target_env)

        graph = _apply_live_arbitration(
            graph, repo_path, container_executor, reenter=_reenter
        )
    # Construction invariant: the spine's causal nodes (IMPORT/MODULE) carry no
    # install recipe. Fails loudly under test; logs-and-continues in production.
    graph = _assert_no_causal_recipe(graph)
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
    # Stage 4.6 — ctypes/cffi dlopen scan: read the installed closure's source for
    # find_library/CDLL/cffi-dlopen literals and mint SystemLib nodes for runtime
    # libs neither ldd (not linked) nor import-probe (lazy dlopen) can see.
    # STATIC_SCAN nodes -> untouched by the PROBE-only restamp below.
    graph = add_ctypes_runtime_libs(graph, container_executor)
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
