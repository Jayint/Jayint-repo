# src/python_deps/pkg_layer/construct.py
"""Construction wiring: Contract + Usage -> PackageLayer -> Alignment — Task 7.

``build_package_layer`` is the thin, pure glue that assembles the planes
built in Tasks 1-5 into one call: read the declared Contract (T3), scan the
repo's Usage (T2), narrow the contract to what's actually IN SCOPE for the
requested extras (via the contract-only ``select_roots``, T3), package it all
into a ``PackageLayer`` (T1) with any injected ``ProvidedEdge``s, and run
Alignment (T5) over the result.

No resolve source is consulted here -- there is no live PyPI/uv call in this
module -- so ``closure`` is always ``()``; a caller that has a resolved
closure builds it separately (``pkg_layer.closure.build_closure``) and passes
a full ``PackageLayer`` through ``align`` directly. ``provided`` defaults to
empty and is only ever an injected fixed input (e.g. certified Environment-
plane edges), never computed here. Pure/no-Docker: this function does no I/O
beyond the two plane reads (``read_contract`` walks manifests, ``scan_usage``
walks the repo's ``.py`` files) and is exercised in tests with real fixtures
on ``tmp_path`` -- no fakes needed since nothing external is invoked.

Built SEPARATE from ``python_deps.depgraph`` (per plan) so the two designs
can be A/B-evaluated; this module does not modify anything under
``depgraph/``.
"""
from __future__ import annotations

from python_deps.pkg_layer.align import Alignment, align
from python_deps.pkg_layer.contract import read_contract, select_roots
from python_deps.pkg_layer.planes import PackageLayer, ProvidedEdge, _canon
from python_deps.pkg_layer.usage import scan_usage


def build_package_layer(
    repo_dir: str,
    *,
    needed_extras: frozenset[str] = frozenset(),
    provided: tuple[ProvidedEdge, ...] = (),
) -> tuple[PackageLayer, Alignment]:
    """Wire Contract (T3) + Usage (T2) into a ``PackageLayer`` (T1), aligned (T5).

    ``needed_extras`` gates which ``[project.optional-dependencies]`` /
    ``extras_require`` groups are in scope, exactly as
    ``contract.select_roots`` defines it: every runtime (non-optional)
    declared dep is always in scope; an optional-dependency dep is in scope
    only when its group is a member of ``needed_extras``. The resulting
    canon-token roots are matched back onto the FULL declared set so
    ``PackageLayer.contract`` carries the real ``DeclaredDep`` rows (not just
    names) for exactly that in-scope subset -- an excluded optional group's
    deps never leak into the layer, and imports are never consulted for this
    decision (structurally ruling out the ``depgraph.roots`` gap-fill bug).
    """
    contract = read_contract(repo_dir)
    imports = scan_usage(repo_dir)

    in_scope_canons = frozenset(select_roots(contract, needed_extras))
    in_scope_contract = tuple(
        dep for dep in contract if _canon(dep.name) in in_scope_canons
    )

    layer = PackageLayer(
        contract=in_scope_contract,
        closure=(),
        imports=imports,
        provided=tuple(provided),
    )
    alignment = align(layer)
    return layer, alignment
