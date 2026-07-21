# tests/eval/graph_repair_ablation/corpus_grounding.py
"""Hand-labeled captured-failure fixtures for the grounding arm. Each case is a REAL
pytest-collection / runtime-import failure text + the graph as construction left it +
the node parse->integrate MUST ground to (correct_anchor). correct_anchor="" == REFUSE
(the correct grounding adds NO graph node)."""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.model import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy, package_id, syslib_id,
)
from graph.python.enrich.diagnose import RepoContext


@dataclass(frozen=True)
class GCase:
    name: str
    failure_class: str
    cause_text: str            # the single error line the baseline regex reads
    command: str               # the failing command
    failure_output: str        # full captured stderr/stdout parse() walks
    starting_nodes: tuple[Node, ...]
    ctx: RepoContext
    correct_anchor: str        # node parse->integrate SHOULD produce; "" == REFUSE
    expect_grounded_hit: bool  # arm G grades correct?
    expect_baseline_hit: bool  # arm B (PACKAGE-only) grades correct?


def _pkg(name: str) -> Node:
    return Node(id=package_id(name, None), type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED)


_CTX_NONE = RepoContext(local_names=frozenset())
_CTX_MYAPP = RepoContext(local_names=frozenset({"myapp"}))

GCASES: tuple[GCase, ...] = (
    # 1. plain module-not-found, pkg already in graph -> BOTH arms hit (agreement case)
    GCase(
        name="mnf_resolvable_agreement", failure_class="MODULE_NOT_FOUND",
        cause_text="ModuleNotFoundError: No module named 'psycopg2'",
        command="python3 -m pytest --collect-only -q",
        failure_output="tests/test_db.py:1: in <module>\n    import psycopg2\n"
                       "E   ModuleNotFoundError: No module named 'psycopg2'\n",
        starting_nodes=(_pkg("psycopg2"),), ctx=_CTX_NONE,
        correct_anchor=package_id("psycopg2", None),
        expect_grounded_hit=True, expect_baseline_hit=True,
    ),
    # 2. native runtime syslib -> G hits syslib:, B (PACKAGE-only regex) MISSES. THE DELTA.
    GCase(
        name="native_syslib_delta", failure_class="SYSLIB_MISSING",
        cause_text="ImportError: libGL.so.1: cannot open shared object file: No such file or directory",
        command="python3 -c 'import cv2'",
        failure_output="Traceback (most recent call last):\n"
                       '  File "<string>", line 1, in <module>\n'
                       "ImportError: libGL.so.1: cannot open shared object file: No such file or directory\n",
        starting_nodes=(_pkg("opencv-python"),), ctx=_CTX_NONE,
        correct_anchor=syslib_id("libGL.so.1"),
        expect_grounded_hit=True, expect_baseline_hit=False,
    ),
    # 3. cascading import through a LOCAL module -> both hit pkg:psycopg2 (anchor same;
    #    G additionally records the via-chain, not graded here).
    GCase(
        name="cascading_import_agreement", failure_class="MODULE_NOT_FOUND",
        cause_text="ModuleNotFoundError: No module named 'psycopg2'",
        command="python3 -m pytest --collect-only -q",
        failure_output="tests/test_x.py:2: in <module>\n    from myapp import thing\n"
                       "myapp/db.py:1: in <module>\n    import psycopg2\n"
                       "E   ModuleNotFoundError: No module named 'psycopg2'\n",
        starting_nodes=(_pkg("psycopg2"),), ctx=_CTX_MYAPP,
        correct_anchor=package_id("psycopg2", None),
        expect_grounded_hit=True, expect_baseline_hit=True,
    ),
    # 4. repo-local import -> REFUSE. G must add NOTHING; B (PACKAGE-only) finds no node.
    GCase(
        name="repo_local_refuse", failure_class="REFUSE",
        cause_text="ModuleNotFoundError: No module named 'myapp'",
        command="python3 -m pytest --collect-only -q",
        failure_output="tests/test_x.py:1: in <module>\n    import myapp\n"
                       "E   ModuleNotFoundError: No module named 'myapp'\n",
        starting_nodes=(), ctx=_CTX_MYAPP,
        correct_anchor="",
        expect_grounded_hit=True, expect_baseline_hit=True,
    ),
)
