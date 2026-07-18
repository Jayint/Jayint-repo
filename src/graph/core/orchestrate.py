"""Stage orchestrator — repo path in, host-certified ``DepGraph`` out.

Split (3c-5) from the former ``core/build.py``: the thin top entry that dispatches
to the ecosystem provider (Python obligations = ``pipeline``), runs its native
obligations, then host-certifies. The staged Python pipeline it drives is
documented in ``graph/python/pipeline.py``.
"""

from __future__ import annotations

from graph.contracts.executor import Executor
from graph.core.certify import certify_all
from graph.model import DepGraph
from graph.python.lanes.install.ground import DistGuesser, RecordProvider
from graph.python.skeleton import _CERTIFY_CYCLE


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
