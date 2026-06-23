"""Stage 1: static import scan -> Import + Test nodes.

Wraps :func:`python_deps.import_graph.scan_imports` (which classifies each
top-level import as ``stdlib`` / ``project_local`` / ``external``) and lifts the
``external`` findings into the concrete dependency graph of
``docs/DESIGN-static-probe-certified-dependency-graph.md`` section 5:

  * one ``Import`` node per external import
    (``type=IMPORT``, ``layer=NAMING``, ``discovered_by=STATIC_SCAN``,
    ``state=UNKNOWN``, ``provenance`` = the source file(s),
    ``check_command = python -c "import <name>"``);
  * one ``Test`` goal node (``TEST_NODE_ID``, ``layer=TESTS``,
    ``discovered_by=GOAL``, ``check_command = "python -m pytest -q"``) with a
    ``requires`` edge to every Import.

Static scanning is evidence, not completeness (design 4.1 / 10.1): dynamic and
plugin imports are not visible here.  This stage never sets ``state`` beyond
``UNKNOWN`` — only the host certifier (Task 8) flips it.
"""

from __future__ import annotations

from python_deps.import_graph import scan_imports

from .ids import TEST_NODE_ID, import_id
from .schema import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)

TEST_NODE_NAME = "repo_tests_pass"
TEST_CHECK_COMMAND = "python -m pytest -q"


def _import_check_command(name: str) -> str:
    return f'python -c "import {name}"'


def _build_test_node() -> Node:
    return Node(
        id=TEST_NODE_ID,
        type=NodeType.TEST,
        name=TEST_NODE_NAME,
        layer=Layer.TESTS,
        discovered_by=DiscoveredBy.GOAL,
        state=State.UNKNOWN,
        check_command=TEST_CHECK_COMMAND,
    )


def _build_import_node(name: str, source_files: tuple[str, ...]) -> Node:
    provenance = ", ".join(source_files) if source_files else None
    return Node(
        id=import_id(name),
        type=NodeType.IMPORT,
        name=name,
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.UNKNOWN,
        check_command=_import_check_command(name),
        provenance=provenance,
    )


def scan_to_nodes(repo_path: str) -> DepGraph:
    """Scan ``repo_path`` and return a graph of external Import nodes plus the
    Test goal node, joined by ``requires`` edges (Test -> each Import).

    Only ``external`` imports become nodes; stdlib and project-local imports are
    dropped (their classification is reused from ``scan_imports``).
    """
    findings, _project_local, _errors = scan_imports(repo_path)

    graph = DepGraph().with_node(_build_test_node())

    for finding in findings:
        if finding.classification != "external":
            continue
        graph = graph.with_node(
            _build_import_node(finding.import_name, finding.source_files)
        )
        graph = graph.with_edge(
            Edge(
                src=TEST_NODE_ID,
                dst=import_id(finding.import_name),
                relation=EdgeType.REQUIRES,
                origin="scan",
            )
        )

    return graph
