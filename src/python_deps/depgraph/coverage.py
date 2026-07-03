"""Phase-A coverage oracle — which top-level import names the RESOLVED closure
provides, read from wheel RECORD metadata (NOT a post-install snapshot).

The repair fixpoint's "is this import satisfied?" test unions the top-level
module names that the RESOLVED package nodes' wheels ship, obtained through an
INJECTED ``RecordProvider`` (:mod:`python_deps.depgraph.repair`). Reading RECORD
metadata rather than ``packages_distributions()`` is the whole point of
Correction 3: a package that RESOLVED but FAILED TO BUILD is still counted
PROVIDED here (its wheel RECORDs the module), so a build failure is a Phase-B
gap and is never misrouted to Phase-A under-declaration repair.

``resolved_record_coverage`` is pure (no Executor, no network); it is the piece
every fixpoint test exercises with an injected FAKE provider.
:func:`default_record_provider` is the production seam (see its docstring for the
honest limitation the fake in the tests papers over).
"""

from __future__ import annotations

from python_deps.depgraph.executor import Executor
from python_deps.depgraph.relink import PACKAGES_DIST_CMD, parse_packages_distributions
from python_deps.depgraph.repair import RecordProvider
from python_deps.depgraph.schema import Node, NodeType, State
from python_deps.import_mapping import normalize_package_name


def resolved_record_coverage(
    pkg_nodes: list[Node], record_provider: RecordProvider
) -> set[str]:
    """The lowercased UNION of top-level module names the RESOLVED packages ship.

    Iterates the ``Package`` nodes (skipping resolver diagnostic ``MISSING``
    placeholders, which have no wheel to read), asks the injected
    ``record_provider`` for each dist's top-level modules, and unions everything
    non-``None`` (a ``None`` return — no wheel / sdist-only / unknown — contributes
    nothing and falls through to the install/import backstop). Pure: no Executor,
    no network; the provider is the sole source of truth.
    """
    provided: set[str] = set()
    for node in pkg_nodes:
        if node.type is not NodeType.PACKAGE or node.state is State.MISSING:
            continue
        modules = record_provider(node.name)
        if modules:
            provided |= {module.lower() for module in modules}
    return provided


def default_record_provider(container_executor: Executor) -> RecordProvider:
    """Production ``RecordProvider`` — INTERIM post-install container dist-info.

    Builds ``dist -> {top-level modules}`` by inverting the container's
    ``importlib.metadata.packages_distributions()`` (run once, memoized, no
    network). A dist absent from the installed environment returns ``None``.

    KNOWN LIMITATION (P1.4 -> close in P2.1): this reads POST-INSTALL state, so
    (a) a resolved-but-failed-to-build dist reports ``None`` here rather than its
    RECORD modules, and (b) a not-yet-installed repair CANDIDATE also reports
    ``None`` — meaning ``choose_provider`` can never ``confirm`` a candidate, so
    production repair driven by THIS provider is effectively inert. The Phase-A
    fixpoint, Correction 3, and candidate grounding are all proven with injected
    FAKE providers in the unit tests; a faithful PRE-INSTALL host wheel-metadata
    reader (PyPI JSON + ``top_level.txt``/RECORD, the ``underdeclaration_repair_poc``
    shape) is the follow-up that makes production repair functional. Do not rely on
    the interim provider to actually repair a real build.
    """
    cache: dict[str, set[str]] = {}
    built = {"done": False}

    def provider(dist: str) -> "set[str] | None":
        if not built["done"]:
            built["done"] = True
            result = container_executor.run(PACKAGES_DIST_CMD)
            if result.ok:
                for module, dists in parse_packages_distributions(result.stdout).items():
                    for owner in dists:
                        cache.setdefault(normalize_package_name(owner), set()).add(module)
        return cache.get(normalize_package_name(dist))

    return provider
