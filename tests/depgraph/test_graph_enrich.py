"""Tests for graph_enrich (pure; no Docker, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.graph_enrich import owner_node_for_command
from python_deps.depgraph.ids import TEST_NODE_ID, package_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


def _pkg(name: str, version: str | None = None) -> Node:
    return Node(
        id=package_id(name, version),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        version=version,
        state=State.MISSING,
    )


def _graph() -> DepGraph:
    return (
        DepGraph()
        .with_node(Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL))
        .with_node(_pkg("psycopg2", "2.9.12"))
        .with_node(_pkg("charset-normalizer", "3.3.2"))
    )


def test_pinned_pip_install_resolves_to_the_package_node():
    assert owner_node_for_command(_graph(), "pip install psycopg2==2.9.12") == "pkg:psycopg2==2.9.12"


def test_unpinned_pip_install_still_resolves_by_name():
    # The command carries no version; the NODE does. Match on canonical name, not on the id.
    assert owner_node_for_command(_graph(), "pip install psycopg2") == "pkg:psycopg2==2.9.12"


def test_name_is_canonicalized_underscore_vs_hyphen():
    # PEP 503: charset_normalizer and charset-normalizer are the same distribution.
    got = owner_node_for_command(_graph(), "pip install charset_normalizer")
    assert got == "pkg:charset-normalizer==3.3.2"


def test_batch_install_is_not_attributable():
    assert owner_node_for_command(_graph(), "pip install psycopg2 asyncpg") is None


def test_requirements_file_install_is_not_attributable():
    assert owner_node_for_command(_graph(), "pip install -r requirements.txt") is None


def test_editable_install_is_not_attributable():
    assert owner_node_for_command(_graph(), "pip install -e .") is None


def test_apt_command_is_not_a_package_owner():
    # apt installs a system package, not a pip Package node — there is no pkg: owner.
    # NOTE: `_provider_from_command` itself returns "apt:libpq-dev" (not None) for
    # this command; it is `owner_node_for_command`'s `pip:`-prefix guard that turns
    # it into None here, not an upstream None.
    assert owner_node_for_command(_graph(), "apt-get install -y libpq-dev") is None


def test_unknown_package_has_no_node():
    assert owner_node_for_command(_graph(), "pip install patchright") is None


def test_empty_and_none_command_are_safe():
    assert owner_node_for_command(_graph(), "") is None
    assert owner_node_for_command(_graph(), None) is None


# ── two nodes, one canonical name: never GUESS a version ─────────────────────
#
# DepGraph is upsert-only and Package ids bake the version (`pkg:name==version`), so two nodes
# for the SAME canonical name but DIFFERENT versions can coexist as distinct ids — a stale
# round's node that the caller has not run `build.reconcile_packages` over yet. Attaching a
# discovery to the wrong version is the worst outcome available here: it is a confidently
# wrong owner edge. Losing the owner (falling back to the Test node) is merely a loss of depth.


def _two_versions() -> DepGraph:
    # The FRESH node first, the STALE one appended after — so any "first match" or "last match"
    # positional rule picks a version rather than reading the one the command actually names.
    return _graph().with_node(_pkg("psycopg2", "2.9.9"))


def test_pinned_command_resolves_to_the_node_at_THAT_version():
    # THE regression. `_provider_from_command` throws the version away (req_slice.py:52 does
    # `toks[0].split("==")[0]`), so a name-only match had to fall back to a positional tie-break
    # — and "last appended" returned the STALE 2.9.9 node for a command that says 2.9.12.
    graph = _two_versions()
    assert owner_node_for_command(graph, "pip install psycopg2==2.9.12") == "pkg:psycopg2==2.9.12"
    assert owner_node_for_command(graph, "pip install psycopg2==2.9.9") == "pkg:psycopg2==2.9.9"


def test_pinned_command_is_order_independent():
    # Same two nodes, appended the other way round. A positional rule flips; a version-matching
    # rule does not.
    graph = (DepGraph()
             .with_node(_pkg("psycopg2", "2.9.9"))
             .with_node(_pkg("psycopg2", "2.9.12")))
    assert owner_node_for_command(graph, "pip install psycopg2==2.9.12") == "pkg:psycopg2==2.9.12"


def test_pinned_command_with_no_node_at_that_version_has_no_owner():
    # Do not silently hand back a same-named node at a DIFFERENT version.
    assert owner_node_for_command(_graph(), "pip install psycopg2==9.9.9") is None


def test_pinned_command_matches_a_node_that_records_no_version():
    # `package_id(name, None)` -> `pkg:psycopg2`. The node records no version, but it is
    # unambiguously the one the command names.
    graph = DepGraph().with_node(_pkg("psycopg2", None))
    assert owner_node_for_command(graph, "pip install psycopg2==2.9.12") == "pkg:psycopg2"


def test_unpinned_command_with_two_candidate_versions_has_no_owner():
    # Nothing in the command says which. Refuse rather than guess.
    assert owner_node_for_command(_two_versions(), "pip install psycopg2") is None
