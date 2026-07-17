# tests/depgraph/corpus_integrate.py
"""Hand-labeled failures. Labeling forces the contract: for each failure we state
exactly what node it must resolve to, whether it should append or match, the edge,
the causal chain, and — for negatives — that NOTHING is added."""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.exec_trace import ParsedFailure
from graph.model import Node, NodeType, Layer, State, Strength, DiscoveredBy
from graph.ids import package_id, TEST_NODE_ID


@dataclass(frozen=True)
class Case:
    name: str
    parsed: ParsedFailure
    starting_nodes: tuple[Node, ...] = ()      # graph state BEFORE (Test node added by the test)
    expect_add: bool = True                    # should a graph node/edge be added?
    expect_node_id: str | None = None          # resolved provider / demand node id
    expect_edge: tuple[str, str] | None = None # (src, dst) requires edge, or None
    expect_via: tuple[str, ...] = ()           # edge.data["via"]
    expect_blast: frozenset[str] = frozenset()
    expect_unbound: bool = False               # demand node, NO provider edge
    match_existing: bool = False               # must annotate, not duplicate


_PKG_PSY = Node(id=package_id("psycopg2", "2.9.9"), type=NodeType.PACKAGE, name="psycopg2",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED,
                strength=Strength.HARD, version="2.9.9")

CASES: tuple[Case, ...] = (
    # 1. external missing import, resolvable, graph empty -> append pkg + edge
    Case(
        name="module_not_found_append",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:psycopg2", causal="import:psycopg2",
                             chain=(("target:tests/test_x.py", "imports", "import:psycopg2"),),
                             blast_radius=frozenset({"tests/test_x.py"}),
                             raw_span="E ModuleNotFoundError: No module named 'psycopg2'"),
        expect_add=True, expect_node_id=package_id("psycopg2", None),
        expect_edge=(TEST_NODE_ID, package_id("psycopg2", None)),
        expect_blast=frozenset({"tests/test_x.py"}),
    ),
    # 2. same import, but the pkg is ALREADY in the graph (static-resolved) -> MATCH, no twin
    Case(
        name="module_not_found_match_existing",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:psycopg2", causal="import:psycopg2",
                             chain=(("target:tests/test_x.py", "imports", "import:psycopg2"),),
                             raw_span="E ModuleNotFoundError: No module named 'psycopg2'"),
        starting_nodes=(_PKG_PSY,),
        expect_add=True, match_existing=True, expect_node_id=package_id("psycopg2", "2.9.9"),
        expect_edge=(TEST_NODE_ID, package_id("psycopg2", "2.9.9")),
    ),
    # 3. cascading import -> chain + blast recorded, via = middle frames
    Case(
        name="cascading_import_chain",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:psycopg2", causal="import:psycopg2",
                             chain=(("target:tests/test_x.py", "imports", "module:myapp"),
                                    ("module:myapp", "imports", "module:myapp.db"),
                                    ("module:myapp.db", "imports", "import:psycopg2")),
                             blast_radius=frozenset({"tests/test_x.py"}),
                             raw_span="myapp/db.py:1: E ModuleNotFoundError: No module named 'psycopg2'"),
        expect_add=True, expect_node_id=package_id("psycopg2", None),
        expect_edge=(TEST_NODE_ID, package_id("psycopg2", None)),
        expect_via=("module:myapp", "module:myapp.db"),
        expect_blast=frozenset({"tests/test_x.py"}),
    ),
    # 4. native runtime lib -> syslib node
    Case(
        name="native_library_missing",
        parsed=ParsedFailure(phase="runtime", failure_type="native_library_missing",
                             terminal="syslib:libGL.so.1", causal="syslib:libGL.so.1",
                             chain=(("target:tests/test_render.py", "loads", "syslib:libGL.so.1"),),
                             raw_span="ImportError: libGL.so.1: cannot open shared object file"),
        expect_add=True, expect_node_id="syslib:libGL.so.1",
        expect_edge=(TEST_NODE_ID, "syslib:libGL.so.1"),
    ),
    # 5. build tool -> binary: capability (FRACTURE GUARD: not tool:)
    Case(
        name="build_tool_pg_config",
        parsed=ParsedFailure(phase="build", failure_type="not_dependency_related",
                             terminal="binary:pg_config", causal="binary:pg_config",
                             chain=(("target:project", "builds", "binary:pg_config"),),
                             raw_span="Error: pg_config executable not found."),
        expect_add=True, expect_node_id="binary:pg_config",
        expect_edge=(TEST_NODE_ID, "binary:pg_config"),
    ),
    # 6. repo-local import -> REFUSE (false-add guard: the `items`/`azure` bug)
    Case(
        name="repo_local_refuse",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:myapp", causal="import:myapp",
                             chain=(("target:tests/test_x.py", "imports", "import:myapp"),),
                             raw_span="E ModuleNotFoundError: No module named 'myapp'"),
        expect_add=False,
    ),
    # 7. unmappable import -> demand-only node, NO provider edge
    Case(
        name="unmappable_unbound",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:frobnicate9000", causal="import:frobnicate9000",
                             chain=(("target:tests/test_x.py", "imports", "import:frobnicate9000"),),
                             raw_span="E ModuleNotFoundError: No module named 'frobnicate9000'"),
        expect_add=True, expect_unbound=True, expect_node_id="import:frobnicate9000",
        expect_edge=None,
    ),
)
