"""P1.4 — Phase-A repair fixpoint: resolve -> install -> look -> repair loop.

Drives ``build_dep_graph`` over tiny fixture repos through the degraded
``uv pip compile`` fallback path (``uv lock`` returns non-ok, so no lock file is
produced), so resolution is fully controlled by a ``SequencedFakeExecutor`` whose
per-round output differs. The RECORD-union coverage oracle is driven by an
INJECTED fake ``record_provider`` (never the network, never a real container):
this is what proves the Corrections independently of any production reader.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from conftest import SequencedFakeExecutor  # type: ignore

from graph.core.build import (
    build_dep_graph,
    reconcile_packages,
)
from graph.python.lanes.install.coverage import resolved_record_coverage
from graph.contracts.executor import CommandResult
from graph.ids import import_id, package_id
from graph.python.lanes.install.resolve_errors import _offending_root_names
from graph.python.lanes.install.resolve_lock import _package_node
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
from graph.python.util.import_mapping import normalize_package_name


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _r(returncode=0, stdout="", stderr=""):
    return CommandResult(command="", returncode=returncode, stdout=stdout, stderr=stderr)


def _repo(tmp_path, app_src, pyproject=None):
    (tmp_path / "app.py").write_text(app_src)
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject)
    return str(tmp_path)


def _provider(mapping):
    """A fake RECORD provider: canon-dist -> set of top-level modules (else None)."""
    norm = {normalize_package_name(k): set(v) for k, v in mapping.items()}

    def provider(dist):
        return norm.get(normalize_package_name(dist))

    return provider


def _recording_provider(mapping):
    """Like :func:`_provider`, but records every dist the grounding layer queries.

    Lets a test PROVE a candidate actually REACHED grounding (the provider was
    consulted for it) rather than silently never being generated -- the teeth that
    keep the source-aware acceptance-gate guards from regressing to vacuous passes.
    """
    base = _provider(mapping)
    consulted: list[str] = []

    def provider(dist):
        consulted.append(dist)
        return base(dist)

    return provider, consulted


def _null_provider(_dist):
    return None


def _fallback_executor(compile_queue, *, install=None, packages_dist=None):
    """SequencedFakeExecutor that fails ``uv lock`` (forcing the pip-compile
    fallback) and returns queued ``uv pip compile`` closures per round."""
    responses = {
        "uv lock": [_r(1, stderr="lock unavailable")],
        "uv pip compile": [_r(0, stdout=text) for text in compile_queue],
    }
    if install is not None:
        responses["pip install"] = list(install)
    if packages_dist is not None:
        responses["packages_distributions"] = list(packages_dist)
    return SequencedFakeExecutor(responses=responses, default=_r(0))


def _build_counting(repo, ex, provider, **kwargs):
    """Run build_dep_graph, counting resolve_closure invocations (= rounds)."""
    import graph.core.build as build_mod

    counter = {"resolve": 0}
    orig = build_mod.resolve_closure

    def spy(*args, **kw):
        counter["resolve"] += 1
        return orig(*args, **kw)

    with patch.object(build_mod, "resolve_closure", side_effect=spy):
        graph = build_dep_graph(
            repo, ex, host_executor=ex, record_provider=provider, **kwargs
        )
    return graph, counter


def _packages(graph):
    return [n for n in graph.nodes if n.type is NodeType.PACKAGE]


def _audit_packages(graph):
    return [
        n
        for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.discovered_by is DiscoveredBy.AUDIT
    ]


# --------------------------------------------------------------------------- #
# coverage.py — resolved_record_coverage (Correction 3, pure)
# --------------------------------------------------------------------------- #
def _pkg(name, version="1.0", state=State.UNKNOWN):
    from dataclasses import replace

    return replace(_package_node(name, version), state=state)


def test_coverage_unions_provided_modules_over_resolved_dists():
    nodes = [_pkg("PyYAML"), _pkg("requests")]
    provider = _provider({"PyYAML": {"yaml"}, "requests": {"requests"}})
    assert resolved_record_coverage(nodes, provider) == {"yaml", "requests"}


def test_coverage_blind_dist_contributes_nothing():
    nodes = [_pkg("PyYAML"), _pkg("mystery")]
    provider = _provider({"PyYAML": {"yaml"}})  # mystery -> None
    assert resolved_record_coverage(nodes, provider) == {"yaml"}


def test_coverage_lowercases_module_names():
    nodes = [_pkg("Pillow")]
    provider = _provider({"Pillow": {"PIL"}})
    assert resolved_record_coverage(nodes, provider) == {"pil"}


def test_coverage_excludes_missing_placeholder_packages():
    nodes = [_pkg("PyYAML"), _pkg("ghost", version=None, state=State.MISSING)]
    provider = _provider({"PyYAML": {"yaml"}, "ghost": {"ghost"}})
    # The MISSING placeholder is never asked / never counted.
    assert resolved_record_coverage(nodes, provider) == {"yaml"}


def test_coverage_counts_resolved_but_failed_to_build_dist_as_provided():
    """Correction 3: a dist that resolved but would FAIL to build (absent from a
    post-install packages_distributions) is still PROVIDED here, because the
    oracle reads RECORD metadata via the injected provider, never install state.
    """
    nodes = [_pkg("psycopg2")]
    # The injected provider (RECORD/wheel-metadata) DOES provide it, even though
    # a post-install packages_distributions() would be empty (build failed).
    provider = _provider({"psycopg2": {"psycopg2"}})
    assert resolved_record_coverage(nodes, provider) == {"psycopg2"}


# --------------------------------------------------------------------------- #
# Fixpoint — convergence / no-op / bound / ambiguous / optional
# --------------------------------------------------------------------------- #
def test_fixpoint_converges_on_under_declaration(tmp_path):
    """Repo declares nothing but imports yaml. Round 1: empty closure -> missing
    {yaml} -> grounds PyYAML -> ACCEPT. Round 2: closure has PyYAML -> covered ->
    break. PyYAML enters as an AUDIT root; yaml is not flagged; loop == 2 rounds.
    """
    repo = _repo(tmp_path, "import yaml\n")
    # PyYAML installs (pip install -> default rc0), so the CONTAINER's post-install
    # packages_distributions() honestly reports yaml->PyYAML; Stage 4a (the sole
    # Import->Package source now) certifies that edge so yaml is not flagged.
    ex = _fallback_executor(
        ["PyYAML==6.0\n    # via -r -\n"],
        packages_dist=[_r(0, stdout='{"yaml": ["PyYAML"]}')],
    )
    provider = _provider({"PyYAML": {"yaml"}})

    graph, counter = _build_counting(repo, ex, provider)

    pyyaml = graph.get(package_id("PyYAML", "6.0"))
    assert pyyaml is not None
    assert pyyaml.discovered_by is DiscoveredBy.AUDIT
    assert graph.get(import_id("yaml")).data.get("unresolved") is not True
    assert counter["resolve"] == 2


def test_fixpoint_default_composite_provider_repairs_via_pypi_fetch(tmp_path):
    """P1.5 — the DEFAULT path repairs a real under-declaration (no injected
    record_provider). Repo imports ``yaml``, declares nothing. Build constructs
    its OWN composite default = ``composite_record_provider(default_record_provider
    (container), pypi_record_provider())``; the only thing stubbed is the network
    fetch seam (a fake wheel read). The post-install provider is blind for the
    not-yet-installed candidate, so the composite consults the fake PyPI fetch,
    grounds ``PyYAML``, and the fixpoint ACCEPTs it as an AUDIT root. This is the
    test P1.4 could not write — it proves production repair is no longer inert.
    """
    import graph.core.build as build_mod
    from graph.python.lanes.install.coverage import pypi_record_provider

    repo = _repo(tmp_path, "import yaml\n")
    # The FIRST packages_distributions read (the memoized post-install container
    # provider) is EMPTY: at round-1 repair time the pipreqs candidate (PyYAML) is
    # NOT yet installed, so the cheap container reader is honestly BLIND for it and
    # the composite MUST fall through to the PyPI wheel read to ground it (the whole
    # point of P1.5). The later read reports yaml->PyYAML once PyYAML installs, so
    # Stage 4a (sole Import->Package source) still certifies the edge and yaml is
    # not flagged.
    ex = _fallback_executor(
        ["PyYAML==6.0\n    # via -r -\n"],
        packages_dist=[_r(0, stdout="{}"), _r(0, stdout='{"yaml": ["PyYAML"]}')],
    )

    fetch_calls = {"n": 0}

    def fake_wheel_fetch(dist):
        fetch_calls["n"] += 1
        return {"yaml"} if normalize_package_name(dist) == "pyyaml" else None

    def fake_pypi_provider():
        # Same constructor build.py calls by default, but with the network seam
        # replaced by the fake — the composite wiring itself stays real.
        return pypi_record_provider(fetch=fake_wheel_fetch)

    with patch.object(build_mod, "pypi_record_provider", fake_pypi_provider):
        # NO record_provider injected -> build_dep_graph builds the composite default.
        graph = build_dep_graph(repo, ex, host_executor=ex)

    pyyaml = graph.get(package_id("PyYAML", "6.0"))
    assert pyyaml is not None
    assert pyyaml.discovered_by is DiscoveredBy.AUDIT
    assert graph.get(import_id("yaml")).data.get("unresolved") is not True
    assert fetch_calls["n"] >= 1  # the pre-install PyPI wheel read grounded the candidate


def test_fixpoint_well_declared_repo_does_zero_repair(tmp_path):
    """Declares + imports requests; coverage covers it round 1 -> break with one
    install and no AUDIT nodes."""
    repo = _repo(
        tmp_path,
        "import requests\n",
        '[project]\nname="fx"\nversion="0"\ndependencies=["requests"]\n',
    )
    ex = _fallback_executor(["requests==2.31.0\n    # via -r -\n"], install=[_r(0)])
    provider = _provider({"requests": {"requests"}})

    graph, counter = _build_counting(repo, ex, provider)

    assert counter["resolve"] == 1
    assert _audit_packages(graph) == []
    assert sum(1 for c in ex.calls if "pip install" in c) == 1


def test_fixpoint_bound_and_honest_residue(tmp_path, caplog):
    """Unresolvable import (provider None for every candidate) -> repair cannot
    progress -> loop stops, import flagged unresolved, no fabricated root, a
    warning is logged, no exception."""
    repo = _repo(tmp_path, "import zzznope\n")
    ex = _fallback_executor([""])  # closure stays empty (nothing to compile)
    provider = _null_provider

    with caplog.at_level(logging.WARNING):
        graph, _counter = _build_counting(repo, ex, provider)

    assert graph.get(import_id("zzznope")).data.get("unresolved") is True
    assert _packages(graph) == []
    assert any("phase-A" in rec.message for rec in caplog.records)


def test_fixpoint_ambiguous_does_not_pick(tmp_path):
    """TWO canon-distinct confirming dists -> AMBIGUOUS -> no root added.

    `attr` has no pipreqs entry, so an injected `llm_dist_guesser` supplies the two
    rival dists; the fake provider grounds BOTH, so `choose_provider` sees >1
    confirmed provider -> AMBIGUOUS -> the fixpoint picks NEITHER (never guesses a
    variant). (The AMBIGUOUS decision itself is also unit-covered in
    test_repair_grounding.py / test_repair_ladder.py; this drives it end-to-end
    through the fixpoint.)"""
    repo = _repo(tmp_path, "import attr\n")
    ex = _fallback_executor([""])
    # Injected guesser proposes two rival dists for the missing import; the fake
    # provider CONFIRMS both -> genuine ambiguity reached at the fixpoint level.
    guesser = lambda name, symbols: ["attr", "python-attr"] if name == "attr" else []  # noqa: E731
    provider, consulted = _recording_provider({"attr": {"attr"}, "python-attr": {"attr"}})

    graph, counter = _build_counting(repo, ex, provider, llm_dist_guesser=guesser)

    assert _audit_packages(graph) == []
    assert graph.get(package_id("attr", "1.0")) is None
    assert graph.get(package_id("attrs", "1.0")) is None
    assert not any(n.type is NodeType.PACKAGE for n in graph.nodes)
    # TEETH: both rival candidates reached grounding (AMBIGUOUS was genuinely hit,
    # not skipped for lack of candidates).
    assert any(normalize_package_name(d) == "attr" for d in consulted)
    assert any(normalize_package_name(d) == "python-attr" for d in consulted)
    # TEETH (effective no-pick): AMBIGUOUS must add NO root, so there is NO second
    # resolve. A wrongful "pick-first" would add a root and force a re-resolve
    # (resolve == 2); staying at 1 is what proves the fixpoint refrained.
    assert counter["resolve"] == 1


def test_fixpoint_optional_import_never_triggers_repair(tmp_path):
    """A guarded ``try: import ujson`` (tagged optional by the scan) is not in the
    missing set -> 0 repair rounds, not flagged."""
    repo = _repo(tmp_path, "try:\n    import ujson\nexcept ImportError:\n    pass\n")
    ex = _fallback_executor([""])
    provider = _null_provider  # no provider at all

    graph, counter = _build_counting(repo, ex, provider)

    assert counter["resolve"] == 1  # one look, no repair round
    assert _audit_packages(graph) == []
    uj = graph.get(import_id("ujson"))
    assert uj is not None
    assert uj.data.get("optional") is True
    assert uj.data.get("unresolved") is not True


def test_fixpoint_if_guarded_not_re_added_but_unconditional_still_rescued(tmp_path):
    """Precision fix AND its guard-rail in one repo. ``app.py`` imports ``yaml``
    unconditionally (a genuine under-declaration) and ``winloop`` ONLY under an
    ``if sys.platform == 'win32':`` guard. On the (linux) target the guarded
    import's declared-but-marker-excluded provider is correctly absent from the
    closure, so the audit must NOT re-add ``winloop`` -- while the unconditional
    ``yaml`` IS still rescued as an AUDIT root (the audit's legitimate job).
    ``winloop`` is even given a confirming provider entry to prove it is skipped
    because it is optional, not because grounding would have denied it."""
    repo = _repo(
        tmp_path,
        "import sys\n"
        "import yaml\n"
        "if sys.platform == 'win32':\n"
        "    import winloop\n",
    )
    ex = _fallback_executor(
        ["PyYAML==6.0\n    # via -r -\n"],
        packages_dist=[_r(0, stdout='{"yaml": ["PyYAML"]}')],
    )
    provider = _provider({"PyYAML": {"yaml"}, "winloop": {"winloop"}})

    graph, counter = _build_counting(repo, ex, provider)

    # yaml -> PyYAML rescued as an AUDIT root; winloop never re-added.
    assert sorted(n.name for n in _audit_packages(graph)) == ["PyYAML"]
    assert graph.get(package_id("PyYAML", "6.0")).discovered_by is DiscoveredBy.AUDIT
    assert not any(
        n.type is NodeType.PACKAGE and "winloop" in n.name.lower() for n in graph.nodes
    )
    # winloop import is optional, so it is neither repaired nor flagged unresolved.
    win = graph.get(import_id("winloop"))
    assert win is not None
    assert win.data.get("optional") is True
    assert win.data.get("unresolved") is not True
    assert counter["resolve"] == 2  # look -> repair yaml -> converge


# --------------------------------------------------------------------------- #
# "Also fix" bullet 2 — the repair ladder must never re-admit a
# `[tool.uv.sources]`-carrying dependency through the ACCEPTANCE gate, even
# when it only reaches candidacy via the `normalize` rung (independent of the
# `declared_metadata` rung `_declared_package_names_for_repair` already
# excludes it from).
# --------------------------------------------------------------------------- #
def test_fixpoint_never_repairs_uv_sourced_dependency_via_normalize_rung(tmp_path, caplog):
    """`hogli` is declared ONLY in a non-activated optional-dependency group
    with a `[tool.uv.sources]` git override -- `select_roots` correctly keeps
    it OUT of this resolve's roots (Task 7 scope rule). The repo's app code
    nonetheless imports it unconditionally, so Phase-A's coverage audit flags
    it as an under-declaration and the repair ladder tries to ground it.
    `hogli` has NO pipreqs entry, so an injected `llm_dist_guesser` supplies the
    PyPI NAMESAKE `hogli` as a candidate -- and the fake RECORD provider CONFIRMS
    it, so `choose_provider` returns ACCEPT with the candidate fully grounded. The
    fixpoint's acceptance gate (`_canon(dist) not in uv_sourced_names`) must STILL
    refuse it: `hogli` must never become an AUDIT root, which would resolve the
    unrelated public PyPI package of that name instead of the pinned git fork."""
    repo = _repo(
        tmp_path,
        "import requests\nimport hogli\n",
        "[project]\n"
        'name = "posthog"\n'
        'version = "0.1.0"\n'
        'dependencies = ["requests"]\n'
        "\n"
        "[project.optional-dependencies]\n"
        'extra = ["hogli"]\n'
        "\n"
        "[tool.uv.sources]\n"
        'hogli = { git = "https://github.com/example/hogli-fork" }\n',
    )
    ex = _fallback_executor(
        ["requests==2.31.0\n    # via -r -\n"],
        packages_dist=[_r(0, stdout='{"requests": ["requests"]}')],
    )
    # The fake RECORD provider CONFIRMS hogli provides top-level "hogli" -- a real
    # grounded confirm, proving the refusal is the acceptance gate, not a grounding
    # failure. `consulted` records every dist grounding queried (the teeth below).
    provider, consulted = _recording_provider({"requests": {"requests"}, "hogli": {"hogli"}})
    # Inject the PyPI namesake as the guessed candidate (pipreqs has no `hogli`),
    # so a candidate IS generated and REACHES grounding -- the only thing that then
    # stops it becoming a root is the uv-sources acceptance gate.
    guesser = lambda name, symbols: ["hogli"] if name == "hogli" else []  # noqa: E731

    with caplog.at_level(logging.WARNING):
        graph, counter = _build_counting(repo, ex, provider, llm_dist_guesser=guesser)

    assert graph.get(package_id("hogli", None)) is None
    assert not any(n.name == "hogli" for n in _audit_packages(graph))
    assert not any(n.name == "hogli" and n.type is NodeType.PACKAGE for n in graph.nodes)
    # No second resolve round -- the candidate was rejected at acceptance, so
    # the ladder never treated it as "a new pair" worth re-resolving for.
    assert counter["resolve"] == 1
    assert any("phase-A" in rec.message for rec in caplog.records)
    # TEETH: the namesake candidate genuinely REACHED grounding (provider consulted
    # for it) and ground-CONFIRMED -- so the refusal above is the uv-sources
    # acceptance gate, NOT an absent candidate. Delete the gate and hogli repairs.
    assert any(normalize_package_name(d) == "hogli" for d in consulted)


# --------------------------------------------------------------------------- #
# Fix 1 (docs/superpowers/plans/2026-07-14-post-measurement-fixes.md): the SAME
# acceptance-gate protection, pinned for a PEP 508 direct reference instead of
# a `[tool.uv.sources]` override -- both feed the identical
# `_uv_sourced_dist_names` union, so this proves the union actually reaches
# the acceptance gate through the real `build_dep_graph` pipeline, not just
# the unit-level frozenset.
# --------------------------------------------------------------------------- #
def test_fixpoint_never_repairs_direct_reference_dependency_via_normalize_rung(tmp_path, caplog):
    """`kivymd` is declared ONLY in a non-activated optional-dependency group
    as a PEP 508 direct reference (`kivymd @ git+...`) -- `select_roots`
    correctly keeps it OUT of this resolve's roots. The repo's app code
    nonetheless imports it unconditionally, so Phase-A's coverage audit flags
    it as an under-declaration and the repair ladder tries to ground it.
    `kivymd` has NO pipreqs entry, so an injected `llm_dist_guesser` supplies the
    PyPI NAMESAKE `kivymd` as a candidate -- and the fake RECORD provider CONFIRMS
    it, so `choose_provider` returns ACCEPT with the candidate fully grounded. The
    fixpoint's acceptance gate must STILL refuse it: `kivymd` must never become an
    AUDIT root, which would resolve the unrelated public PyPI package of that name
    instead of the git-pinned fork."""
    repo = _repo(
        tmp_path,
        "import requests\nimport kivymd\n",
        "[project]\n"
        'name = "archipelago"\n'
        'version = "0.1.0"\n'
        'dependencies = ["requests"]\n'
        "\n"
        "[project.optional-dependencies]\n"
        'extra = ["kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d"]\n',
    )
    ex = _fallback_executor(
        ["requests==2.31.0\n    # via -r -\n"],
        packages_dist=[_r(0, stdout='{"requests": ["requests"]}')],
    )
    # The fake RECORD provider CONFIRMS kivymd provides top-level "kivymd" -- a real
    # grounded confirm, proving the refusal is the acceptance gate, not a grounding
    # failure. `consulted` records every dist grounding queried (the teeth below).
    provider, consulted = _recording_provider({"requests": {"requests"}, "kivymd": {"kivymd"}})
    # Inject the PyPI namesake as the guessed candidate (pipreqs has no `kivymd`),
    # so a candidate IS generated and REACHES grounding -- the direct-reference
    # acceptance gate is then the only thing that can stop it becoming a root.
    guesser = lambda name, symbols: ["kivymd"] if name == "kivymd" else []  # noqa: E731

    with caplog.at_level(logging.WARNING):
        graph, counter = _build_counting(repo, ex, provider, llm_dist_guesser=guesser)

    assert graph.get(package_id("kivymd", None)) is None
    assert not any(n.name == "kivymd" for n in _audit_packages(graph))
    assert not any(n.name == "kivymd" and n.type is NodeType.PACKAGE for n in graph.nodes)
    assert counter["resolve"] == 1
    assert any("phase-A" in rec.message for rec in caplog.records)
    # TEETH: the namesake candidate genuinely REACHED grounding (provider consulted
    # for it) and ground-CONFIRMED -- so the refusal above is the direct-reference
    # acceptance gate, NOT an absent candidate. Delete the gate and kivymd repairs.
    assert any(normalize_package_name(d) == "kivymd" for d in consulted)


# --------------------------------------------------------------------------- #
# Correction 2b — attempted-set termination (oscillation)
# --------------------------------------------------------------------------- #
def test_fixpoint_attempted_set_stops_oscillation(tmp_path, caplog):
    """A grounded candidate is ACCEPTED then evicted by resolution (re-appears
    missing). The attempted-set prevents re-adding the same pair, so the loop
    stops (bounded), residue flagged, oscillation warning logged, no exception."""
    repo = _repo(tmp_path, "import yaml\n")
    # Round 2 closure is EMPTY (the added root failed to materialize == evicted).
    ex = _fallback_executor([""])
    # pipreqs maps yaml->pyyaml (a real non-identity entry the map carries), and
    # this provider RECORD-confirms it, so the candidate is genuinely ACCEPTED in
    # round 1 -- which is what makes the round-2 re-proposal hit the attempted-set.
    provider = _provider({"pyyaml": {"yaml"}})

    with caplog.at_level(logging.WARNING):
        graph, counter = _build_counting(repo, ex, provider)

    # yaml->pyyaml was accepted once (round 1) then the pair was not re-added
    # (round 2) because it is already in the attempted-set.
    assert counter["resolve"] <= 2
    assert graph.get(import_id("yaml")).data.get("unresolved") is True
    assert _packages(graph) == []
    assert any("phase-A" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- #
# Correction 2c — per-round Package node/edge reconcile (version shift)
# --------------------------------------------------------------------------- #
def test_reconcile_packages_drops_stale_versioned_node():
    old = _package_node("foo", "1.0")
    other = _package_node("libr", "1.0")
    graph = DepGraph().with_node(other).with_node(old).with_edge(
        Edge(src=other.id, dst=old.id, relation=EdgeType.REQUIRES, origin="resolver")
    )
    prev_pkg_ids = {old.id, other.id}
    new_foo = _package_node("foo", "2.0")
    new_edges = [
        Edge(src=other.id, dst=new_foo.id, relation=EdgeType.REQUIRES, origin="resolver")
    ]

    out = reconcile_packages(graph, [other, new_foo], new_edges, prev_pkg_ids)

    assert out.get(package_id("foo", "1.0")) is None
    assert out.get(package_id("foo", "2.0")) is not None
    # No dangling edge to the removed v1 node survives.
    assert not any(package_id("foo", "1.0") in (e.src, e.dst) for e in out.edges)


def test_reconcile_packages_drops_stale_edge_between_survivors():
    a = _package_node("a", "1.0")
    b = _package_node("b", "1.0")
    graph = DepGraph().with_node(a).with_node(b).with_edge(
        Edge(src=a.id, dst=b.id, relation=EdgeType.REQUIRES, origin="resolver")
    )
    prev_pkg_ids = {a.id, b.id}
    # New resolve emits both nodes but NO a->b edge anymore.
    out = reconcile_packages(graph, [a, b], [], prev_pkg_ids)

    assert out.get(a.id) is not None and out.get(b.id) is not None
    assert not any(e.src == a.id and e.dst == b.id for e in out.edges)


def test_fixpoint_reconciles_stale_node_across_version_shift(tmp_path):
    """Two sequenced resolves where a transitive package's version changes ->
    the round-1 ``pkg:foo==1.0`` node is ABSENT after round 2 (only ==2.0)."""
    repo = _repo(
        tmp_path,
        "import yaml\n",
        '[project]\nname="fx"\nversion="0"\ndependencies=["libr"]\n',
    )
    ex = _fallback_executor(
        [
            "libr==1.0\n    # via -r -\nfoo==1.0\n    # via libr\n",
            "libr==1.0\n    # via -r -\nfoo==2.0\n    # via libr\nPyYAML==6.0\n    # via -r -\n",
        ]
    )
    provider = _provider({"PyYAML": {"yaml"}})

    graph, counter = _build_counting(repo, ex, provider)

    assert counter["resolve"] == 2
    assert graph.get(package_id("foo", "1.0")) is None
    assert graph.get(package_id("foo", "2.0")) is not None
    assert not any(package_id("foo", "1.0") in (e.src, e.dst) for e in graph.edges)


# --------------------------------------------------------------------------- #
# Correction 3 — RECORD-union oracle, build-failure not misrouted (fixpoint)
# --------------------------------------------------------------------------- #
def test_fixpoint_build_failure_not_misrouted_to_repair(tmp_path):
    """A resolved dist FAILS to install (empty post-install packages_distributions)
    but the injected record_provider DOES provide the import -> coverage marks it
    PROVIDED -> NOT missing -> repair fabricates no alternative."""
    repo = _repo(
        tmp_path,
        "import themod\n",
        '[project]\nname="fx"\nversion="0"\ndependencies=["somepkg"]\n',
    )
    ex = _fallback_executor(
        ["somepkg==1.0\n    # via -r -\n"],
        install=[_r(1, stderr="Failed building wheel for somepkg")],
        packages_dist=[_r(0, stdout="{}")],  # install failed -> empty
    )
    provider = _provider({"somepkg": {"themod"}})

    graph, counter = _build_counting(repo, ex, provider)

    # Coverage (RECORD-union) counts somepkg as providing themod despite the build
    # failure, so the fixpoint does NO repair round and fabricates NO alternative
    # package (the build failure is a Phase-B gap, not a Phase-A under-declaration).
    assert counter["resolve"] == 1  # covered on the first look -> no repair round
    assert _audit_packages(graph) == []
    assert graph.get(package_id("somepkg", "1.0")) is not None
    assert {n.name for n in _packages(graph)} == {"somepkg"}  # no fabricated alt


# --------------------------------------------------------------------------- #
# Correction 2a — _offending_root_names declared-drop-priority
# --------------------------------------------------------------------------- #
from types import SimpleNamespace  # noqa: E402


def _conflict(pkg, left_imp, right_imp):
    return SimpleNamespace(
        package=pkg,
        left=SimpleNamespace(imposed_by=left_imp),
        right=SimpleNamespace(imposed_by=right_imp),
    )


def test_offending_prefers_audit_over_declared_imposer():
    """A transitive conflict imposed by a DECLARED root and an AUDIT root drops
    the AUDIT root, never the declared one."""
    diag = SimpleNamespace(missing=[], conflicts=[_conflict("shared", "declared-d", "audit-a")])
    names = _offending_root_names(
        diag, {"declared-d", "audit-a"}, audit_root_names=frozenset({"audit-a"})
    )
    assert "audit-a" in names
    assert "declared-d" not in names


def test_offending_backward_compatible_without_audit_set():
    """With no audit set, behavior matches today (alphabetical drop of one root
    imposer)."""
    diag = SimpleNamespace(missing=[], conflicts=[_conflict("shared", "package-b", "package-c")])
    names = _offending_root_names(diag, {"package-b", "package-c"})
    assert len(names & {"package-b", "package-c"}) == 1


def test_offending_drops_shared_pin_when_no_audit_alternative():
    """The shared/conflicted pin is still dropped when it is a root and no AUDIT
    imposer is droppable."""
    diag = SimpleNamespace(missing=[], conflicts=[_conflict("a", "project", "package-b")])
    names = _offending_root_names(diag, {"a", "package-b"}, audit_root_names=frozenset())
    assert "a" in names
    assert "package-b" not in names


def test_resolve_closure_threads_audit_root_names(tmp_path):
    """resolve_closure forwards its audit_root_names down to _offending_root_names."""
    import graph.python.lanes.install.resolve as resolve_mod
    from graph.python.read.target_env import TargetEnv

    captured = {}
    orig = resolve_mod._offending_root_names

    def spy(diag, current_root_names, audit_root_names=frozenset()):
        captured["audit"] = audit_root_names
        return orig(diag, current_root_names, audit_root_names)

    env = TargetEnv(
        python_full="3.11.0",
        python_version="3.11",
        platform_machine="x86_64",
        sys_platform="linux",
        os_name="posix",
        platform_system="Linux",
        python_platform_tag="x86_64-manylinux_2_28",
    )
    ex = SequencedFakeExecutor(
        responses={"uv lock": [_r(1, stderr="x was not found in the registry")]},
        default=_r(1),
    )
    with patch.object(resolve_mod, "_offending_root_names", side_effect=spy):
        resolve_mod.resolve_closure(
            [(None, "x")],
            ex,
            target_env=env,
            project_dir=str(tmp_path),
            audit_root_names=frozenset({"x"}),
        )
    assert captured.get("audit") == frozenset({"x"})


def test_build_threads_repaired_into_resolve_audit_root_names(tmp_path):
    """After a repair adds an AUDIT root, build_dep_graph passes the repaired set
    as audit_root_names to the next resolve_closure call."""
    import graph.core.build as build_mod

    repo = _repo(tmp_path, "import yaml\n")
    ex = _fallback_executor(["PyYAML==6.0\n    # via -r -\n"])
    provider = _provider({"PyYAML": {"yaml"}})

    seen = []
    orig = build_mod.resolve_closure

    def spy(*args, **kwargs):
        seen.append(kwargs.get("audit_root_names"))
        return orig(*args, **kwargs)

    with patch.object(build_mod, "resolve_closure", side_effect=spy):
        build_dep_graph(repo, ex, host_executor=ex, record_provider=provider)

    # Round 2's resolve carries the repaired dist (canon) in audit_root_names.
    assert any(a and "pyyaml" in a for a in seen)


# --------------------------------------------------------------------------- #
# Stage B Task 7 — lane-aware `_missing_import_nodes` (module-routed + deferred)
# --------------------------------------------------------------------------- #
def test_missing_excludes_module_routed_and_deferred():
    from graph.core.build import _missing_import_nodes  # extracted pure helper
    from graph.model import DepGraph, Node, NodeType, Layer, DiscoveredBy

    def imp(name, **data):
        return Node(id=f"import:{name}", type=NodeType.IMPORT, name=name,
                    layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN, data=data)

    graph = (DepGraph()
             .with_node(imp("requests"))
             .with_node(imp("myapp", routed_provider="module"))
             .with_node(imp("items")))
    got = {n.name for n in _missing_import_nodes(graph, provided=frozenset(), deferred=frozenset({"items"}))}
    assert got == {"requests"}   # myapp is module-routed; items is deferred; only requests is missing


def test_missing_filter_is_byte_identical_without_lanes():
    # With no module-routed nodes and empty deferred, the new helper equals the
    # old comprehension exactly (behavior-preserving gate for Stage C).
    from graph.core.build import _missing_import_nodes
    from graph.model import DepGraph, Node, NodeType, Layer, DiscoveredBy
    imp = lambda n, **d: Node(id=f"import:{n}", type=NodeType.IMPORT, name=n,
                              layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN, data=d)
    graph = DepGraph().with_node(imp("requests")).with_node(imp("flask", optional=True))
    got = {n.name for n in _missing_import_nodes(graph, provided=frozenset(), deferred=frozenset())}
    assert got == {"requests"}   # optional dropped; nothing else excluded
