"""Tests for graph_enrich (pure; no Docker, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

# `Cause` lives under `src/react_repair/`, which needs the repo ROOT on sys.path, not just
# `src/` — copy the two-parent form from tests/react_repair/test_pytest_summary.py:1-6.
_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from graph.diagnose import RepoContext
from graph.graph_enrich import certify_only, enrich, owner_node_for_command
from graph.ids import TEST_NODE_ID, package_id
from graph.model import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)
from src.agent.pytest_summary import Cause


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


# ── enrich() / certify_only() ─────────────────────────────────────────────────


class _Result:
    def __init__(self, ok=True, failing_command=None, output=""):
        self.ok, self.failing_command, self.output = ok, failing_command, output


class _FakeExec:
    """Minimal Executor. NOTE the real CommandResult fields (executor.py:21-30) are
    `command, returncode, stdout, stderr` — there is NO `rc` field. `certify()` reads
    `.ok` (a property: returncode == 0) and `.stderr`."""
    def __init__(self, returncode=0):
        self.returncode, self.seen = returncode, []

    def run(self, command, **_kw):
        self.seen.append(command)
        from graph.contracts.executor import CommandResult
        return CommandResult(command, self.returncode, "", "" if self.returncode == 0 else "boom")


def test_build_failure_anchors_the_discovery_at_the_OWNER_not_the_test_node():
    """The whole point of owner_node_for_command. Without it this edge would be
    test:repo_tests_pass -> binary:pg_config -- a flat star with no depth."""
    g = _graph()
    result = _Result(ok=False, failing_command="pip install psycopg2==2.9.12",
                     output="Error: pg_config executable not found")
    new, new_ids = enrich(g, result, causes=[], ctx=RepoContext())
    owners = {e.src for e in new.edges
              if e.relation is EdgeType.REQUIRES and "pg_config" in e.dst}
    assert owners == {"pkg:psycopg2==2.9.12"}
    assert TEST_NODE_ID not in owners
    assert any("pg_config" in nid for nid in new_ids)


def test_a_call_phase_cause_never_mutates_the_graph():
    """§4.3 — 'the test's own code decided it was wrong' IS the line between no-env-fix
    and env-fix. An AssertionError must never become a node, however its message reads."""
    cause = Cause(exc="AssertionError", detail="No module named 'ghost'", count=3,
                  outcome="FAILED", module="tests/t.py", phase="call")
    g = _graph()
    new, new_ids = enrich(g, _Result(ok=True), causes=[cause], ctx=RepoContext())
    assert new_ids == []
    assert len(new.nodes) == len(g.nodes)


def test_a_collect_phase_cause_may_append_a_node():
    # NOTE (plan discrepancy): the plan's own draft used `detail="No module named 'patchright'"`
    # here. `patchright` is NOT in `import_mapping.CURATED_IMPORT_TO_PACKAGE`, and
    # `RepoContext` carries no `declared_package_names` for the runtime classifier to consult
    # (`map_import_to_package` is called with no second arg — see `runtime_classify.py`), so
    # `map_import_to_package("patchright")` returns `unresolved_result` -> `Discovery(name=None,
    # ...)` -> `_annotate_or_append` returns the graph UNCHANGED (`runtime_ingest.py:112-116`,
    # "never fabricate a `pkg:None` node"). The plan's test asserted a node WOULD appear; it
    # would not, with today's classifier. `yaml` -> `PyYAML` IS in the curated table, so it
    # demonstrates the same phase-gate behaviour honestly.
    cause = Cause(exc="ModuleNotFoundError", detail="No module named 'yaml'", count=1,
                  outcome="ERROR", module="tests/t.py", phase="collect")
    new, new_ids = enrich(_graph(), _Result(ok=True), causes=[cause], ctx=RepoContext())
    assert any("yaml" in nid.lower() for nid in new_ids)


def test_a_teardown_phase_cause_never_mutates_the_graph():
    """§4.3 gates BOTH non-env phases, not just `call` — a fixture finalizer error is not
    a live requirement of the test either."""
    cause = Cause(exc="ModuleNotFoundError", detail="No module named 'ghost'", count=1,
                  outcome="ERROR", module="tests/t.py", phase="teardown")
    g = _graph()
    new, new_ids = enrich(g, _Result(ok=True), causes=[cause], ctx=RepoContext())
    assert new_ids == []
    assert len(new.nodes) == len(g.nodes)


def test_a_setup_phase_cause_may_append_a_node():
    """§4.3 — a fixture-time failure (e.g. a DB connection fixture) IS an env failure,
    just like collect. Both `collect` and `setup` are in `_ENV_PHASES`.

    Uses `cv2` (curated -> `opencv-python`), not `redis`: `redis` is NOT in
    `import_mapping.CURATED_IMPORT_TO_PACKAGE` either, so it would hit the same
    unresolved-import no-op as `patchright` above.
    """
    cause = Cause(exc="ModuleNotFoundError", detail="No module named 'cv2'", count=1,
                  outcome="ERROR", module="tests/t.py", phase="setup")
    new, new_ids = enrich(_graph(), _Result(ok=True), causes=[cause], ctx=RepoContext())
    assert any("opencv" in nid.lower() for nid in new_ids)


def test_enrich_is_idempotent_across_turns():
    """The script re-runs from base every turn, so the SAME failure recurs. Enriching twice
    must not duplicate nodes or edges."""
    g = _graph()
    result = _Result(ok=False, failing_command="pip install psycopg2==2.9.12",
                     output="Error: pg_config executable not found")
    once, _ = enrich(g, result, [], RepoContext())
    twice, ids2 = enrich(once, result, [], RepoContext())
    assert len(twice.nodes) == len(once.nodes)
    assert len(twice.edges) == len(once.edges)
    assert ids2 == []                      # nothing NEW the second time


def test_enrich_never_raises_on_garbage_output():
    new, ids = enrich(_graph(), _Result(ok=False, failing_command="pip install x",
                                        output="\x00\xff not a real error"),
                      [], RepoContext())
    assert isinstance(ids, list)


def test_enrich_with_no_result_and_no_causes_is_a_no_op():
    """`result=None` on the very first turn (no run yet) must not raise."""
    g = _graph()
    new, ids = enrich(g, None, [], RepoContext())
    assert ids == []
    assert len(new.nodes) == len(g.nodes)
    assert len(new.edges) == len(g.edges)


def test_certify_only_touches_just_the_named_nodes():
    g = _graph().with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RUNTIME,
                                state=State.UNKNOWN, check_command="command -v pg_config"))
    ex = _FakeExec(returncode=0)
    new = certify_only(g, ["binary:pg_config"], ex)
    assert new.get("binary:pg_config").state is State.SATISFIED
    assert ex.seen == ["command -v pg_config"]          # NOT every node in the graph


def test_certify_only_with_no_new_ids_is_a_no_op():
    ex = _FakeExec()
    g = _graph()
    assert certify_only(g, [], ex) is g
    assert ex.seen == []


def test_certify_only_ignores_an_id_no_longer_in_the_graph():
    """Defensive: a stale id (e.g. from a prior turn) must not crash the narrow pass."""
    ex = _FakeExec()
    g = _graph()
    new = certify_only(g, ["binary:does-not-exist"], ex)
    assert ex.seen == []
    assert new.get("binary:does-not-exist") is None
