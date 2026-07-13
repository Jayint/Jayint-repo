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


def test_ambiguous_canonical_name_prefers_the_most_recently_added_node():
    # DepGraph is upsert-only and Package ids bake the version (`pkg:name==version`),
    # so two nodes for the SAME canonical name but DIFFERENT versions can coexist as
    # distinct ids (e.g. a stale round's node the caller hasn't reconciled away yet
    # — build.py's `reconcile_packages` does this at construction time, but nothing
    # guarantees every caller of this function has run that pass first). A linear
    # scan that returns on the FIRST match would silently depend on insertion order;
    # this asserts the resolution is deterministic and prefers the LAST-inserted
    # (most-recently-discovered) node, matching the tie-break `resolve_link.py`'s
    # `canon_to_pkg` lookup already uses for the same kind of collision.
    graph = _graph().with_node(_pkg("psycopg2", "2.9.11"))
    assert owner_node_for_command(graph, "pip install psycopg2") == "pkg:psycopg2==2.9.11"
