"""Tests for graph_context edge semantics (pure; no Docker, no network)."""
from __future__ import annotations

import pytest
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from graph.emit.emit import _conflicted_ids, _is_emittable
from graph.graph_context import (
    ACTIONABLE, BLOCKED, SATISFIED_OK, UNCERTIFIED, WAITING, blocks, in_conflict,
    render_graph_context, verdict,
)
# Aliased on import: the real name `tests_hidden` starts with "test", which is pytest's own
# `python_functions` collection prefix (default `["test"]`, matched via `name.startswith`,
# per `_pytest.python.PyCollector._matches_prefix_or_glob_option`). A bare
# `from ... import tests_hidden` would land a 2-argument callable directly in this module's
# namespace and pytest would try to collect IT as a test item too -- the exact false-positive
# this module's own regex is built to mirror. Import it under a name that does not match.
from graph.graph_context import tests_hidden as _tests_hidden
from graph.ids import package_id
from graph.model import (
    Attempt, DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)


def _pkg(name, version="1.0", state=State.MISSING, build_from_source=None) -> Node:
    return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                version=version, state=state, build_from_source=build_from_source)


def _tool(name, state=State.MISSING) -> Node:
    return Node(id=f"binary:{name}", type=NodeType.TOOL, name=name,
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=state)


def _syslib(name, state=State.MISSING) -> Node:
    return Node(id=f"syslib:{name}", type=NodeType.SYSTEM_LIB, name=name,
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=state)


def _requires(src: str, dst: str, **kw) -> Edge:
    return Edge(src=src, dst=dst, relation=EdgeType.REQUIRES, origin="resolver", **kw)


def _edge_of(graph: DepGraph, src: str, dst: str) -> Edge:
    return next(e for e in graph.edges if e.src == src and e.dst == dst)


def test_actionable_when_nothing_beneath_is_missing():
    g = DepGraph().with_node(_tool("pg_config"))
    assert verdict(g, g.get("binary:pg_config")) == ACTIONABLE


def test_waiting_when_a_hard_prerequisite_is_missing():
    g = (DepGraph()
         .with_node(_pkg("psycopg2"))
         .with_node(_tool("pg_config"))
         .with_edge(_requires("pkg:psycopg2==1.0", "binary:pg_config")))
    assert verdict(g, g.get("pkg:psycopg2==1.0")) == WAITING
    assert verdict(g, g.get("binary:pg_config")) == ACTIONABLE


def test_satisfied_prerequisite_does_not_make_the_owner_wait():
    g = (DepGraph()
         .with_node(_pkg("psycopg2"))
         .with_node(_tool("pg_config", state=State.SATISFIED))
         .with_edge(_requires("pkg:psycopg2==1.0", "binary:pg_config")))
    assert verdict(g, g.get("pkg:psycopg2==1.0")) == ACTIONABLE


# ── THE BUG THIS TASK FIXES ──────────────────────────────────────────────────

def test_conflicted_node_is_BLOCKED_even_with_zero_missing_prerequisites():
    """The exact shape that fooled the old root definition.

    `pkg:pydantic` is MISSING and has NO missing prerequisite, so "MISSING with no
    MISSING prerequisite" calls it a root and tells the agent `pip install pydantic`.
    It CANNOT be installed at any version — emit._is_emittable already refuses to emit
    a conflicted node. It must be BLOCKED, never ACTIONABLE.
    """
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11"))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    node = g.get("pkg:pydantic==2.11")
    assert in_conflict(g, node) is True
    assert verdict(g, node) == BLOCKED
    assert verdict(g, node) != ACTIONABLE


def test_conflict_blocks_BOTH_endpoints():
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11"))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    assert verdict(g, g.get("pkg:fastapi==0.115")) == BLOCKED


def test_a_conflicts_edge_is_not_a_prerequisite():
    # CONFLICTS_WITH must never be traversed as a requires edge.
    g = (DepGraph()
         .with_node(_pkg("a"))
         .with_node(_pkg("b"))
         .with_edge(Edge(src="pkg:a==1.0", dst="pkg:b==1.0",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    assert blocks(g, _edge_of(g, "pkg:a==1.0", "pkg:b==1.0")) is False


def test_a_satisfied_but_conflicted_node_is_SATISFIED_OK():
    # Order check: SATISFIED wins over BLOCKED. The node is installed — whatever the resolver
    # said about it, there is nothing for the agent to do, so it must not get a "cannot
    # install" record.
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11", state=State.SATISFIED))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    assert verdict(g, g.get("pkg:pydantic==2.11")) == SATISFIED_OK


# ── agreement with emit, the incumbent authority ─────────────────────────────
#
# emit._toolchain_ready is what the BUILD SCRIPT RENDERER already uses to decide what may be
# installed. If the arm disagrees with it, we send the agent to fix something the renderer
# would have installed anyway — and every wasted turn is a full container rebuild.

def test_a_missing_build_TOOL_does_not_block_a_KNOWN_WHEEL():
    """emit._toolchain_ready:78 — `dep.type is TOOL and pkg.build_from_source is not False`.

    A wheel needs no compiler, so a missing build tool is irrelevant to installing it. The
    renderer emits the package regardless; the arm must not tell the agent to apt-get a tool
    it will never invoke.
    """
    g = (DepGraph()
         .with_node(_pkg("Pillow", "10.3", build_from_source=False))   # a KNOWN wheel
         .with_node(_tool("gcc"))
         .with_edge(_requires("pkg:Pillow==10.3", "binary:gcc")))
    pkg = g.get("pkg:Pillow==10.3")
    assert blocks(g, _edge_of(g, "pkg:Pillow==10.3", "binary:gcc")) is False
    assert verdict(g, pkg) == ACTIONABLE
    # ...and that is exactly what the incumbent says:
    assert _is_emittable(g, pkg, _conflicted_ids(g)) is True


def test_a_missing_build_TOOL_blocks_a_SOURCE_build_and_an_UNKNOWN_build_mode():
    for build_from_source in (True, None):     # None = build mode not yet known
        g = (DepGraph()
             .with_node(_pkg("psycopg2", "2.9.12", build_from_source=build_from_source))
             .with_node(_tool("pg_config"))
             .with_edge(_requires("pkg:psycopg2==2.9.12", "binary:pg_config")))
        assert verdict(g, g.get("pkg:psycopg2==2.9.12")) == WAITING, build_from_source


def test_a_missing_SYSTEM_LIB_blocks_even_a_KNOWN_WHEEL():
    # emit._toolchain_ready:76-77 — "runtime lib: wheel & sdist". A wheel dlopens the .so at
    # import time, so a missing SystemLib defeats it just as it defeats a source build. This is
    # the case a build-mode-only rule would wrongly wave through.
    g = (DepGraph()
         .with_node(_pkg("Pillow", "10.3", build_from_source=False))
         .with_node(_syslib("libjpeg.so.8"))
         .with_edge(_requires("pkg:Pillow==10.3", "syslib:libjpeg.so.8")))
    assert verdict(g, g.get("pkg:Pillow==10.3")) == WAITING


def test_a_missing_PACKAGE_dependency_does_not_block():
    # `pip install psycopg2` resolves and installs psycopg2's own dependencies, so a missing
    # pip dep is not something the agent must fix first. emit does not gate on these either —
    # it topologically orders them instead.
    g = (DepGraph()
         .with_node(_pkg("psycopg2", "2.9.12"))
         .with_node(_pkg("typing-extensions", "4.12"))
         .with_edge(_requires("pkg:psycopg2==2.9.12", "pkg:typing-extensions==4.12")))
    assert verdict(g, g.get("pkg:psycopg2==2.9.12")) == ACTIONABLE


# ── soft edges ───────────────────────────────────────────────────────────────

def test_soft_edge_does_not_block():
    # emit.py:69-70 — "soft requires edges never block (invariant #10)".
    g = (DepGraph()
         .with_node(_pkg("a"))
         .with_node(_syslib("libfoo.so.1"))
         .with_edge(_requires("pkg:a==1.0", "syslib:libfoo.so.1", data={"hard": False})))
    assert blocks(g, _edge_of(g, "pkg:a==1.0", "syslib:libfoo.so.1")) is False


def test_soft_missing_prerequisite_leaves_the_owner_actionable():
    # NOTE: DiscoveredBy has NO `LLM` member. The codebase's name for an LLM-admitted node is
    # CLASSIFIER (schema.py:64-74; see patch_gate.py:250).
    g = (DepGraph()
         .with_node(_pkg("app"))
         .with_node(Node(id="config:DATABASE_URL", type=NodeType.CONFIG,
                         name="DATABASE_URL", layer=Layer.CONFIG,
                         discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING))
         .with_edge(Edge(src="pkg:app==1.0", dst="config:DATABASE_URL",
                         relation=EdgeType.REQUIRES, origin="llm", data={"hard": False})))
    assert verdict(g, g.get("pkg:app==1.0")) == ACTIONABLE


def test_hard_edge_is_the_default_when_the_key_is_absent():
    g = (DepGraph()
         .with_node(_pkg("a"))
         .with_node(_syslib("libfoo.so.1"))
         .with_edge(_requires("pkg:a==1.0", "syslib:libfoo.so.1")))
    assert blocks(g, _edge_of(g, "pkg:a==1.0", "syslib:libfoo.so.1")) is True


# ── a SATISFIED node is not "actionable"; an UNKNOWN one is not either ───────

def test_a_satisfied_node_is_never_actionable():
    """A SATISFIED leaf has no missing prerequisites, so a verdict() that only looked at
    PREREQUISITES would call it ACTIONABLE and hand it a record — telling the agent to
    'fix' something that is already fine. verdict() must check the node's OWN state first."""
    g = DepGraph().with_node(_tool("pkg-config", state=State.SATISFIED))
    assert verdict(g, g.get("binary:pkg-config")) == SATISFIED_OK
    assert verdict(g, g.get("binary:pkg-config")) != ACTIONABLE


def test_an_UNKNOWN_node_is_never_actionable():
    """Spec §6.4: "UNKNOWN never masquerades as MISSING."

    An UNKNOWN node was never certified against the container — typically it has no
    check_command. It has no missing prerequisites either, so a MISSING-blind verdict calls it
    ACTIONABLE and hands the agent an uncertified guess dressed up as a measurement.
    emit._is_emittable refuses every non-MISSING node for exactly this reason.
    """
    g = DepGraph().with_node(_tool("mystery", state=State.UNKNOWN))
    node = g.get("binary:mystery")
    assert verdict(g, node) == UNCERTIFIED
    assert verdict(g, node) != ACTIONABLE
    assert _is_emittable(g, node, _conflicted_ids(g)) is False   # the incumbent agrees


# ── markers ──────────────────────────────────────────────────────────────────
#
# The dst is a SystemLib in each case: it is the only dep type that blocks unconditionally, so
# the marker is the sole remaining variable and these tests cannot pass for the wrong reason.

def _marker_graph(marker: str) -> DepGraph:
    return (DepGraph()
            .with_node(_pkg("a"))
            .with_node(_syslib("libfoo.so.1"))
            .with_edge(_requires("pkg:a==1.0", "syslib:libfoo.so.1", marker=marker)))


def test_marker_that_does_not_hold_is_skipped():
    g = _marker_graph('python_version < "3.9"')
    e = _edge_of(g, "pkg:a==1.0", "syslib:libfoo.so.1")
    assert blocks(g, e, target_env={"python_version": "3.12"}) is False


def test_marker_that_holds_is_traversed():
    g = _marker_graph('python_version >= "3.9"')
    e = _edge_of(g, "pkg:a==1.0", "syslib:libfoo.so.1")
    assert blocks(g, e, target_env={"python_version": "3.12"}) is True


def test_marker_is_conservatively_traversed_when_no_target_env_is_known():
    # Without a target we cannot evaluate — do NOT silently drop a real prerequisite.
    g = _marker_graph('python_version < "3.9"')
    e = _edge_of(g, "pkg:a==1.0", "syslib:libfoo.so.1")
    assert blocks(g, e, target_env=None) is True


def test_unparseable_marker_is_conservatively_traversed():
    g = _marker_graph("this is not a marker")
    e = _edge_of(g, "pkg:a==1.0", "syslib:libfoo.so.1")
    assert blocks(g, e, target_env={"python_version": "3.12"}) is True


# ── tests_hidden — the weight a collection error cannot report ──────────────

@pytest.fixture
def outside_module(tmp_path):
    """A real Python file OUTSIDE the repo root, holding a test pytest would count.

    The containment tests need a target that would return a NUMBER if the guard leaked.
    Pointing them at /etc/passwd proves nothing: it holds no `def test_`, so it returns None
    whether the guard is there or not, and the test would keep passing after someone deleted
    the check.
    """
    path = tmp_path.parent / f"{tmp_path.name}_outside.py"
    path.write_text("def test_secret():\n    pass\n")
    yield path
    path.unlink(missing_ok=True)

def test_tests_hidden_counts_sync_and_async_test_defs(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_db.py").write_text(
        "import psycopg2\n"
        "\n"
        "def test_one():\n"
        "    pass\n"
        "\n"
        "async def test_two():\n"
        "    pass\n"
        "\n"
        "def helper():\n"           # not a test
        "    pass\n"
        "\n"
        "def test_three(conn):\n"
        "    pass\n"
    )
    assert _tests_hidden(str(tmp_path), "tests/test_db.py") == 3


def test_tests_hidden_counts_indented_methods_in_test_classes(tmp_path):
    (tmp_path / "t.py").write_text(
        "class TestThing:\n"
        "    def test_a(self):\n"
        "        pass\n"
        "    def test_b(self):\n"
        "        pass\n"
    )
    assert _tests_hidden(str(tmp_path), "t.py") == 2


def test_tests_hidden_returns_None_for_a_missing_file(tmp_path):
    assert _tests_hidden(str(tmp_path), "nope.py") is None


def test_tests_hidden_returns_None_when_the_file_has_no_tests(tmp_path):
    (tmp_path / "t.py").write_text("def helper():\n    pass\n")
    assert _tests_hidden(str(tmp_path), "t.py") is None


def test_tests_hidden_returns_None_without_a_repo_path(tmp_path):
    assert _tests_hidden(None, "tests/test_db.py") is None


def test_tests_hidden_never_escapes_the_repo(tmp_path, outside_module):
    # `module` comes from parsed pytest output — treat it as untrusted input.
    # NOTE the target: pointing these at /etc/passwd would pass even with the guard REMOVED,
    # because /etc/passwd contains no `def test_` and the count would be 0 -> None anyway. The
    # target has to be a file that WOULD return a number if it were read.
    escape = "../" * 12 + outside_module.name
    assert _tests_hidden(str(tmp_path), escape) is None


def test_tests_hidden_never_escapes_via_an_absolute_module_path(tmp_path, outside_module):
    # pathlib quirk: `Path("/repo") / "/abs/path" == Path("/abs/path")` -- the right operand
    # being absolute discards the left one entirely. Confirm the containment check
    # (`relative_to`, which raises on a path with no common root) catches this rather than
    # silently reading whatever absolute path pytest's output happened to mention.
    assert _tests_hidden(str(tmp_path), str(outside_module)) is None


def test_tests_hidden_never_escapes_via_a_symlink(tmp_path):
    # A module path that is legitimately *inside* the repo but symlinks out must not leak
    # the target's contents either -- `path.resolve()` follows the link before the
    # containment check runs, so the escape is caught the same way a `..` escape is.
    outside = tmp_path.parent / f"{tmp_path.name}_symlink_target.py"
    outside.write_text("def test_secret():\n    pass\n")
    try:
        (tmp_path / "link.py").symlink_to(outside)
        assert _tests_hidden(str(tmp_path), "link.py") is None
    finally:
        outside.unlink()


def test_tests_hidden_is_rendered_as_an_estimate_via_its_docstring():
    # This is not a numeric assertion -- it locks in the contract that callers MUST render
    # the value as an estimate (spec: "~200 tests hidden, est."), never as a measured count.
    assert "ESTIMATE" in _tests_hidden.__doc__


# ── what pytest would ACTUALLY collect ───────────────────────────────────────
#
# This number's only job is RANKING, so a systematic over-count is the failure mode that
# matters: it can hand a module a big weight it does not deserve and bury the real root cause.

def test_tests_hidden_ignores_test_methods_in_a_NON_Test_class(tmp_path):
    # pytest only descends into classes matching `python_classes = ["Test"]`. A shared helper
    # class full of `def test_*` methods is never collected -- but a line regex counts every
    # one of them, which is enough to manufacture the large estimate we then rank on.
    (tmp_path / "t.py").write_text(
        "class Helper:\n"
        "    def test_a(self):\n"
        "        pass\n"
        "    def test_b(self):\n"
        "        pass\n"
        "\n"
        "def test_real():\n"
        "    pass\n"
    )
    assert _tests_hidden(str(tmp_path), "t.py") == 1


def test_tests_hidden_ignores_a_test_def_nested_inside_another_function(tmp_path):
    # Never collected: it is a local, not a module attribute.
    (tmp_path / "t.py").write_text(
        "def make_fixture():\n"
        "    def test_inner():\n"
        "        pass\n"
        "    return test_inner\n"
        "\n"
        "def test_real():\n"
        "    pass\n"
    )
    assert _tests_hidden(str(tmp_path), "t.py") == 1


def test_tests_hidden_ignores_a_test_def_inside_a_string(tmp_path):
    # Not code. The regex cannot tell; an AST walk can.
    (tmp_path / "t.py").write_text(
        'EXAMPLE = """\n'
        "def test_documented():\n"
        "    pass\n"
        '"""\n'
        "\n"
        "def test_real():\n"
        "    pass\n"
    )
    assert _tests_hidden(str(tmp_path), "t.py") == 1


def test_tests_hidden_counts_decorated_and_async_methods_in_a_Test_class(tmp_path):
    (tmp_path / "t.py").write_text(
        "import pytest\n"
        "\n"
        "class TestThing:\n"
        "    @pytest.mark.slow\n"
        "    def test_a(self):\n"
        "        pass\n"
        "    async def test_b(self):\n"
        "        pass\n"
        "    def helper(self):\n"          # not a test
        "        pass\n"
    )
    assert _tests_hidden(str(tmp_path), "t.py") == 2


def test_tests_hidden_falls_back_to_the_regex_when_the_module_does_not_parse(tmp_path):
    # A collection error can absolutely BE a SyntaxError, and that is precisely the file we
    # still want a weight for. AST parsing raises; the line regex still yields a rough count.
    (tmp_path / "t.py").write_text(
        "def test_one():\n"
        "    pass\n"
        "\n"
        "def test_two(:\n"                 # <- syntax error
        "    pass\n"
    )
    assert _tests_hidden(str(tmp_path), "t.py") == 2


# ── render_graph_context — the subgraph edge-list + per-node records (Task 5) ───────────────
#
# `Cause` lives in `src/react_repair/pytest_summary.py`. `tests/depgraph/conftest.py` puts only
# `src/` on sys.path (for the bare `python_deps....` imports above), not the repo ROOT, so
# `from src.react_repair...` needs the two-parent form used by tests/react_repair/*.py.

try:
    from src.react_repair.pytest_summary import Cause
except ImportError:                       # tests/depgraph/ does not add the repo root
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from src.react_repair.pytest_summary import Cause


class _Result:
    def __init__(self, ok=True, failing_command=None, output=""):
        self.ok, self.failing_command, self.output = ok, failing_command, output


def _pg_graph() -> DepGraph:
    """psycopg2 and asyncpg both need pg_config. pkg-config is fine. THE collapse case."""
    g = (DepGraph()
         .with_node(_pkg("psycopg2", "2.9.12"))
         .with_node(_pkg("asyncpg", "0.30.0"))
         .with_node(_tool("pkg-config", state=State.SATISFIED))
         .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                         layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RUNTIME,
                         state=State.MISSING,
                         check_command="command -v pg_config",
                         chosen_fix="apt:libpq-dev",
                         evidence='pip install psycopg2: "pg_config executable not found"'))
         .with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver"))
         .with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="binary:pkg-config",
                         relation=EdgeType.REQUIRES, origin="resolver"))
         .with_edge(Edge(src="pkg:asyncpg==0.30.0", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver")))
    return g


def _collect_cause(name, count=1, module="tests/test_db.py"):
    return Cause(exc="ModuleNotFoundError", detail=f"No module named '{name}'",
                 count=count, outcome="ERROR", module=module, phase="collect")


def test_shared_root_appears_as_two_edges_but_ONE_record():
    out = render_graph_context(
        _pg_graph(), _Result(ok=True),
        [_collect_cause("psycopg2"), _collect_cause("asyncpg", module="tests/test_a.py")],
        prev_states={},
    )
    # Two edge lines converge on the same node -- that IS the collapse.
    assert out.count("--requires-->  binary:pg_config") == 2
    # ...but the record is printed exactly once.
    assert out.count("★ binary:pg_config") == 1


def test_requires_and_required_by_are_separate_sections():
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("psycopg2")],
                               prev_states={})
    assert "REQUIRES" in out
    assert "REQUIRED BY" in out
    assert out.index("REQUIRES") < out.index("REQUIRED BY")


def test_satisfied_prerequisite_is_shown_inline_and_gets_no_record():
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("psycopg2")],
                               prev_states={})
    assert "binary:pkg-config" in out          # in the edge list...
    assert "★ binary:pkg-config" not in out    # ...but never as a record


def test_waiting_node_gets_no_record():
    # psycopg2 is MISSING but WAITING on pg_config -- no action available, so no record.
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("psycopg2")],
                               prev_states={})
    assert "★ pkg:psycopg2" not in out


def test_every_actionable_root_gets_a_record_no_top_n_cap():
    """Actionable roots are INDEPENDENT -- the agent should batch all of them in one patch."""
    g = DepGraph()
    causes = []
    for i in range(6):
        g = g.with_node(Node(id=f"binary:t{i}", type=NodeType.TOOL, name=f"t{i}",
                             layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                             state=State.MISSING, check_command=f"command -v t{i}",
                             chosen_fix=f"apt-get install -y t{i}"))
        g = g.with_node(_pkg(f"p{i}"))
        g = g.with_edge(Edge(src=f"pkg:p{i}==1.0", dst=f"binary:t{i}",
                             relation=EdgeType.REQUIRES, origin="resolver"))
        causes.append(_collect_cause(f"p{i}"))
    out = render_graph_context(g, _Result(ok=True), causes, prev_states={})
    for i in range(6):
        assert f"★ binary:t{i}" in out, f"root t{i} was truncated away"


def test_conflicted_node_renders_as_BLOCKED_never_as_actionable():
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11"))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver",
                         data={"summary": "pydantic>=2.11 needs typing-ext>=4.12"})))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("pydantic")], prev_states={})
    assert "✖ pkg:pydantic==2.11" in out
    assert "CANNOT INSTALL" in out
    assert "★ pkg:pydantic==2.11" not in out


def test_call_phase_failure_never_consults_the_graph():
    cause = Cause(exc="AssertionError", detail="assert 3 == 4", count=23,
                  outcome="FAILED", module="tests/test_math.py", phase="call")
    out = render_graph_context(_pg_graph(), _Result(ok=True), [cause], prev_states={})
    assert "NOT AN ENVIRONMENT FAILURE" in out
    assert "AssertionError" in out
    assert "REQUIRES" not in out          # no subgraph was walked for it


def test_cause_with_no_graph_node_says_so_instead_of_inventing_one():
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("patchright")],
                               prev_states={})
    assert "NO GRAPH EXPLANATION" in out
    assert "patchright" in out


def test_build_failure_anchors_at_the_command_owner_when_causes_is_empty():
    """loop.py:202 only runs pytest when the build is GREEN. On a build-fail turn `causes`
    is empty and the ONLY failure to anchor at is the failing command."""
    out = render_graph_context(
        _pg_graph(),
        _Result(ok=False, failing_command="pip install psycopg2==2.9.12",
                output="Error: pg_config executable not found"),
        causes=[], prev_states={},
    )
    assert "BUILD FAILED" in out
    assert "pkg:psycopg2==2.9.12" in out
    assert "★ binary:pg_config" in out
    assert "TESTS DID NOT RUN" in out


def test_state_delta_reports_a_regression_the_agent_caused():
    g = _pg_graph()
    prev = {"binary:pg_config": State.SATISFIED}
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states=prev)
    assert "SINCE YOUR LAST EDIT" in out
    assert "SATISFIED → MISSING" in out


def test_fields_self_select_resolver_node_omits_why_and_source():
    g = (DepGraph()
         .with_node(_pkg("psycopg2", "2.9.12"))
         .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                         layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                         state=State.MISSING, check_command="command -v pg_config",
                         chosen_fix="apt:libpq-dev"))
         .with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver")))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states={})
    assert "check " in out and "fix " in out
    assert "why " not in out          # resolver-sourced -> nothing to justify
    assert "tried " not in out        # no attempts


def test_fields_self_select_contested_node_shows_why_and_tried():
    g = _pg_graph()
    node = g.get("binary:pg_config")
    from dataclasses import replace
    g = g.with_node(replace(node, attempts=(
        Attempt(command="apt-get install postgresql-dev", outcome="failed", cycle=3),)))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states={})
    assert "why " in out                              # runtime-discovered -> must justify itself
    assert "tried " in out
    assert "postgresql-dev" in out                    # the ANTI-THRASH field


def test_collect_weight_is_an_estimate_from_the_file(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_db.py").write_text(
        "".join(f"def test_{i}():\n    pass\n" for i in range(200)))
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("psycopg2")],
                               prev_states={}, repo_path=str(tmp_path))
    assert "~200 tests hidden, est." in out


def test_pip_closure_is_summarized_not_enumerated():
    g = _pg_graph()
    for i in range(37):
        g = g.with_node(_pkg(f"dep{i}", state=State.SATISFIED))
        g = g.with_edge(Edge(src="pkg:psycopg2==2.9.12", dst=f"pkg:dep{i}==1.0",
                             relation=EdgeType.REQUIRES, origin="resolver"))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states={})
    assert "37 transitive pip deps" in out
    assert "pkg:dep0==1.0" not in out                 # never enumerated


def test_a_MISSING_pip_dep_is_promoted_out_of_the_closure_summary():
    g = _pg_graph()
    g = g.with_node(_pkg("zipp", state=State.MISSING))
    g = g.with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="pkg:zipp==1.0",
                         relation=EdgeType.REQUIRES, origin="resolver"))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states={})
    assert "pkg:zipp==1.0" in out                     # promoted -- it is actionable
    assert "★ pkg:zipp==1.0" in out


def test_implausible_frontier_warns_instead_of_dumping_records():
    g = DepGraph()
    causes = []
    for i in range(30):
        g = g.with_node(Node(id=f"pkg:p{i}==1.0", type=NodeType.PACKAGE, name=f"p{i}",
                             layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                             version="1.0", state=State.MISSING,
                             check_command=f"python -c 'import p{i}'",
                             chosen_fix=f"pip install p{i}"))
        causes.append(_collect_cause(f"p{i}"))
    out = render_graph_context(g, _Result(ok=True), causes, prev_states={})
    assert "ACTIONABLE" in out and "implausible" in out.lower()
    assert out.count("★ pkg:p") <= 5                 # 5 largest, NOT 30 records
    assert "treat the graph as unreliable" in out.lower()


def test_empty_graph_and_no_causes_renders_nothing():
    assert render_graph_context(DepGraph(), _Result(ok=True), [], prev_states={}) == ""


# ── review fixes: the four defects gpt-5.6-terra found in the first cut ──────

def test_a_build_failure_with_NO_graph_owner_still_says_something():
    """§6.7 in person. `loop.py` runs pytest only on a GREEN build, so on a build-fail turn
    `causes` is EMPTY. If the failing command also has no owner node — an apt command, a batch
    install, a package we never modelled — the renderer used to return "" and go completely
    silent on exactly the turns where the install tier is broken.
    """
    out = render_graph_context(
        _pg_graph(),
        _Result(ok=False, failing_command="apt-get install -y libpq-dev"),
        causes=[], prev_states={},
    )
    assert out != ""
    assert "apt-get install -y libpq-dev" in out
    assert "BUILD FAILED" in out
    assert "TESTS DID NOT RUN" in out


def test_a_soft_edge_is_shown_as_advisory_and_never_stars_a_root():
    """§6.3: a soft edge "traverses, marked advisory" but "never confers root status alone".

    Starring it would tell the agent to install something on the strength of a guess — the LLM's
    Config/Service edges are all SOFT.
    """
    g = (DepGraph()
         .with_node(_pkg("app", "1.0"))
         .with_node(_tool("optional-thing"))
         .with_edge(_requires("pkg:app==1.0", "binary:optional-thing", data={"hard": False})))
    out = render_graph_context(
        g, _Result(ok=True),
        [Cause(exc="ModuleNotFoundError", detail="No module named 'app'", count=1,
               outcome="ERROR", module="tests/test_app.py", phase="collect")],
        prev_states={},
    )
    assert "binary:optional-thing" in out          # shown...
    assert "(advisory)" in out                     # ...marked advisory...
    assert "★ binary:optional-thing" not in out    # ...and never a "fix this" record.


def test_the_frontier_guard_keeps_the_LARGEST_roots_not_the_first_five():
    """The header says "Showing the N largest". It had better be true.

    Slicing in insertion order while claiming "the largest" is a lie in the output, and it
    buries the root that matters: a 100-test root at index 15 was dropped in favour of five
    1-test roots.
    """
    g = DepGraph()
    causes = []
    for i in range(16):
        g = g.with_node(_pkg(f"p{i}", "1.0"))
        g = g.with_node(Node(id=f"import:p{i}", type=NodeType.IMPORT, name=f"p{i}",
                             layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                             state=State.MISSING))
        causes.append(Cause(exc="ModuleNotFoundError", detail=f"No module named 'p{i}'",
                            count=100 if i == 15 else 1, outcome="ERROR",
                            module=f"tests/test_p{i}.py", phase="call"))
    # phase="call" would be routed to NOT-AN-ENV-FAILURE, so re-key them as run-phase setup
    # errors, which DO anchor on the graph.
    causes = [Cause(exc=c.exc, detail=c.detail, count=c.count, outcome=c.outcome,
                    module=c.module, phase="setup") for c in causes]
    out = render_graph_context(g, _Result(ok=True), causes, prev_states={})
    assert "implausible" in out
    # p15 blocks 100 tests; every other root blocks 1. It must survive the cut.
    assert "★ pkg:p15==1.0" in out


def test_output_is_not_written_in_broken_english():
    # The prompt is read by an LLM; "1 tests" is noise that costs it a beat.
    g = (DepGraph()
         .with_node(_pkg("solo", "1.0"))
         .with_node(Node(id="import:solo", type=NodeType.IMPORT, name="solo", layer=Layer.PIP,
                         discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING))
         .with_edge(_requires("import:solo", "pkg:solo==1.0")))
    out = render_graph_context(
        g, _Result(ok=True),
        [Cause(exc="ModuleNotFoundError", detail="No module named 'solo'", count=1,
               outcome="ERROR", module="tests/test_solo.py", phase="setup")],
        prev_states={},
    )
    assert "1 tests" not in out
    assert "class(es)" not in out


def test_the_fix_field_is_a_RUNNABLE_COMMAND_not_a_provider_id():
    """`chosen_fix` is stored as a PROVIDER ID — `build_deps._capability_node` writes
    `f"apt:{top.package}"`, and `emit._apt_name` strips that prefix when it renders setup.sh.

    The record has to do the same. A `fix` the agent cannot paste into setup.sh is not a fix, it
    is a riddle — and the agent falls back to guessing an apt name out of the error text, which is
    precisely the behaviour the graph exists to replace. Every unit test here used to hand-build
    its nodes with a pre-cooked command string, so the renderer was never asked to convert; the
    FakeSandbox demo is what caught it.
    """
    g = (DepGraph()
         .with_node(_pkg("psycopg2", "2.9.12"))
         .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                         layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                         state=State.MISSING, check_command="command -v pg_config",
                         chosen_fix="apt:libpq-dev",              # <- as the real system stores it
                         fix_candidates=("apt:libpq-dev", "apt:postgresql-server-dev-all")))
         .with_edge(_requires("pkg:psycopg2==2.9.12", "binary:pg_config")))
    out = render_graph_context(
        g, _Result(ok=False, failing_command="pip install psycopg2==2.9.12"),
        causes=[], prev_states={},
    )
    assert "fix      apt-get install -y libpq-dev" in out
    assert "alt: apt-get install -y postgresql-server-dev-all" in out
    assert "apt:libpq-dev" not in out          # the internal id must never reach the agent
