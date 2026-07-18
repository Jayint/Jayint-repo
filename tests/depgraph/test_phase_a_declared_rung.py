"""Task 3 — the DECLARED repair rung inside ``_phase_a_fixpoint``.

For a missing (under-declared) import the audit flags, the fixpoint now PREPENDS
repo-declared distributions that RECORD-cover that import — ``"declared"``-source
candidates ahead of the pipreqs/LLM candidates — so an identity-named dep the repo
declared in a *soft* requirements file (e.g. ``fastapi`` in a ``requirements.txt``
with no matching pyproject entry) is repaired.

These tests drive ``_phase_a_fixpoint`` directly, monkeypatching the module-scope
``resolve_closure`` / ``install_closure`` seams (both are module globals of
``fixpoint`` called by bare name inside the loop, so a ``monkeypatch.setattr`` on
the string target reaches the call site — the same pattern the sibling
``test_phase_a_fixpoint.py`` uses). The RECORD provider is an injected fake: no
Docker, no network.

Fixtures build a ``DepGraph`` with ``Test --requires--> import:<name>`` (unresolved,
no Package nodes) and a no-missing variant whose only import is optional.
"""

from __future__ import annotations

import pytest

from graph.model import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    TEST_NODE_ID,
    import_id,
)
from graph.python.fixpoint import _phase_a_fixpoint


# --------------------------------------------------------------------------- #
# fixtures — tiny import-only graphs (no resolved Package layer)
# --------------------------------------------------------------------------- #
def _test_node() -> Node:
    return Node(
        id=TEST_NODE_ID,
        type=NodeType.TEST,
        name="repo_tests_pass",
        layer=Layer.TESTS,
        discovered_by=DiscoveredBy.GOAL,
    )


def _import_node(name: str, **data) -> Node:
    return Node(
        id=import_id(name),
        type=NodeType.IMPORT,
        name=name,
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        data=data,
    )


def _graph_with_import(name: str, **data) -> DepGraph:
    """``Test --requires--> import:<name>`` with no Package nodes."""
    test = _test_node()
    imp = _import_node(name, **data)
    return (
        DepGraph()
        .with_node(test)
        .with_node(imp)
        .with_edge(Edge(src=test.id, dst=imp.id, relation=EdgeType.REQUIRES, origin="scan"))
    )


@pytest.fixture
def import_only_graph() -> DepGraph:
    """Test -> import:fastapi (unresolved); nothing resolves it -> audit flags it."""
    return _graph_with_import("fastapi")


@pytest.fixture
def import_only_graph_foo() -> DepGraph:
    """Test -> import:foo (unresolved); used for the variant-tie safety case."""
    return _graph_with_import("foo")


@pytest.fixture
def graph_no_missing() -> DepGraph:
    """Healthy graph: its only import is OPTIONAL, so the missing-set stays empty
    every round -> the declared rung is never demanded."""
    return _graph_with_import("ujson", optional=True)


# --------------------------------------------------------------------------- #
# Step 1 — the declared rung repairs an identity-named import
# --------------------------------------------------------------------------- #
def test_declared_rung_repairs_identity_import(monkeypatch, import_only_graph):
    # import_only_graph: a DepGraph with Test -> import:fastapi (unresolved), no pkgs.
    # Round 1 resolves nothing; audit finds import:fastapi missing; the declared rung
    # proposes fastapi (declared), the fake provider confirms it -> added as audit root.
    calls = {"roots": []}
    def fake_resolve(roots, *a, **k):
        calls["roots"].append([d for _i, d in roots])
        return ([], [])           # no closure until fastapi becomes a root; simplified
    monkeypatch.setattr("graph.python.fixpoint.resolve_closure", fake_resolve)
    monkeypatch.setattr("graph.python.fixpoint.install_closure", lambda g, e: g)

    provider = lambda dist: {"fastapi"} if dist.lower() == "fastapi" else None

    _phase_a_fixpoint(
        import_only_graph, roots=[], host_executor=None, container_executor=None,
        record_provider=provider, target_env=None, exclude_newer=None,
        needed_extras=frozenset(), declared_dists=frozenset({"fastapi"}),
    )
    # fastapi was proposed from the declaration and accepted as an audit root
    assert any("fastapi" in r for r in calls["roots"])


# --------------------------------------------------------------------------- #
# Step 5a — AMBIGUOUS safety: a declared confirm must NOT break a variant tie
# --------------------------------------------------------------------------- #
def test_declared_does_not_break_variant_tie(monkeypatch, import_only_graph_foo):
    # import:foo; declared {"foo"}; an injected llm guesser also proposes python-foo,
    # and the fake provider RECORD-confirms BOTH -> two canon-distinct confirms ->
    # AMBIGUOUS. The declared candidate must NOT tip the tie: neither variant is
    # accepted, so no root is ever added (and there is no second resolve).
    calls = {"roots": []}
    def fake_resolve(roots, *a, **k):
        calls["roots"].append([d for _i, d in roots])
        return ([], [])
    monkeypatch.setattr("graph.python.fixpoint.resolve_closure", fake_resolve)
    monkeypatch.setattr("graph.python.fixpoint.install_closure", lambda g, e: g)

    def provider(dist):
        # BOTH foo (declared) and python-foo (llm) ship top-level `foo`.
        return {"foo"} if dist.lower() in {"foo", "python-foo"} else None

    guesser = lambda name, symbols: ["python-foo"] if name == "foo" else []  # noqa: E731

    _phase_a_fixpoint(
        import_only_graph_foo, roots=[], host_executor=None, container_executor=None,
        record_provider=provider, target_env=None, exclude_newer=None,
        needed_extras=frozenset(), declared_dists=frozenset({"foo"}), llm=guesser,
    )
    # AMBIGUOUS -> neither variant becomes a root.
    assert not any("foo" in r or "python-foo" in r for r in calls["roots"])
    # TEETH: no acceptance means no re-resolve — exactly one resolve, empty roots.
    assert calls["roots"] == [[]]


# --------------------------------------------------------------------------- #
# Step 5b — demand-gating: an un-demanded declared dist stays dormant, and the
# lazy declared-coverage build never touches the (network-backed) provider.
# --------------------------------------------------------------------------- #
def test_undemanded_declared_dist_stays_dormant(monkeypatch, graph_no_missing):
    # No missing imports -> the lazy `declared_coverage(...)` build is never reached,
    # so `record_provider` (which fetches candidate wheels = network) is NEVER called.
    # This pins the demand-gated/lazy build: an eager pre-loop build would regress it.
    calls = {"roots": []}
    def fake_resolve(roots, *a, **k):
        calls["roots"].append([d for _i, d in roots])
        return ([], [])
    monkeypatch.setattr("graph.python.fixpoint.resolve_closure", fake_resolve)
    monkeypatch.setattr("graph.python.fixpoint.install_closure", lambda g, e: g)

    provider_calls = []
    def provider(dist):
        provider_calls.append(dist)
        return {"whatever"}

    _phase_a_fixpoint(
        graph_no_missing, roots=[], host_executor=None, container_executor=None,
        record_provider=provider, target_env=None, exclude_newer=None,
        needed_extras=frozenset(), declared_dists=frozenset({"somedist"}),
    )
    # Demand-gated: the provider is never consulted when nothing is missing.
    assert provider_calls == []
    # Byte-identical to the no-declared behavior: one resolve, empty roots.
    assert calls["roots"] == [[]]


# --------------------------------------------------------------------------- #
# V5 — wiring tripwire: the declared rung must stay wired into the loop.
# --------------------------------------------------------------------------- #
def test_declared_dists_are_consumed_not_dormant(monkeypatch, import_only_graph):
    # A declared dist that RECORD-covers a missing import MUST reach the roots.
    # Fails loudly if a future refactor drops the declared rung from the loop
    # (its predecessor went silently dormant — this is the permanent guard).
    seen_roots = []
    def fake_resolve(roots, *a, **k):
        seen_roots.append([d for _i, d in roots])
        return ([], [])
    monkeypatch.setattr("graph.python.fixpoint.resolve_closure", fake_resolve)
    monkeypatch.setattr("graph.python.fixpoint.install_closure", lambda g, e: g)

    provider = lambda dist: {"fastapi"} if dist.lower() == "fastapi" else None

    _phase_a_fixpoint(
        import_only_graph, roots=[], host_executor=None, container_executor=None,
        record_provider=provider, target_env=None, exclude_newer=None,
        needed_extras=frozenset(), declared_dists=frozenset({"fastapi"}),
    )
    # The declared dist was consumed by the rung and threaded into a resolve as a root.
    assert any("fastapi" in roots for roots in seen_roots), (
        "declared rung went dormant: `fastapi` never reached resolve_closure roots"
    )
    # And it forced a re-resolve (the repaired root drives round 2), proving the rung
    # is live inside the loop, not a no-op that leaves the closure untouched.
    assert len(seen_roots) == 2
