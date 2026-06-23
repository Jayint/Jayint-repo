"""Task 8 — host certification (``certify.py``).

The certification invariant (design 3.1): only a host-run ``check_command`` flips
a node's ``state``.  ``certify`` runs one node's check; ``certify_all`` walks the
graph in layer order (interpreter -> system -> toolchain -> pip -> naming ->
tests).  All tests use the ``FakeExecutor`` (no Docker/network).
"""

from __future__ import annotations

from python_deps.depgraph.certify import certify, certify_all
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.ids import TEST_NODE_ID, import_id, package_id, syslib_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Layer,
    Node,
    NodeType,
    State,
)


def _import(name: str) -> Node:
    return Node(
        id=import_id(name),
        type=NodeType.IMPORT,
        name=name,
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        check_command=f'python -c "import {name}"',
    )


def _package(name: str, version: str) -> Node:
    return Node(
        id=package_id(name, version),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        version=version,
        check_command=f"python -m pip show {name}",
    )


def _syslib(soname: str) -> Node:
    return Node(
        id=syslib_id(soname),
        type=NodeType.SYSTEM_LIB,
        name=soname,
        layer=Layer.SYSTEM,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        check_command=f"ldconfig -p | grep {soname}",
    )


def _test_node() -> Node:
    return Node(
        id=TEST_NODE_ID,
        type=NodeType.TEST,
        name="repo_tests_pass",
        layer=Layer.TESTS,
        discovered_by=DiscoveredBy.GOAL,
        check_command="python -m pytest -q",
    )


def test_certify_satisfied_on_rc0(fake_executor, make_result_fixture):
    imp = _import("cv2")
    graph = DepGraph().with_node(imp)
    fake_executor.responses = {"import cv2": make_result_fixture(returncode=0)}

    out = certify(graph, imp.id, fake_executor, cycle=3)

    node = out.get(imp.id)
    assert node.state is State.SATISFIED
    assert node.certified_cycle == 3
    # original graph untouched (immutability)
    assert graph.get(imp.id).state is State.UNKNOWN


def test_certify_missing_on_nonzero_with_stderr_evidence(
    fake_executor, make_result_fixture
):
    imp = _import("cv2")
    graph = DepGraph().with_node(imp)
    fake_executor.responses = {
        "import cv2": make_result_fixture(
            returncode=1, stderr="ModuleNotFoundError: No module named 'cv2'"
        )
    }

    out = certify(graph, imp.id, fake_executor, cycle=3)

    node = out.get(imp.id)
    assert node.state is State.MISSING
    assert "No module named 'cv2'" in (node.evidence or "")


def test_certify_no_check_command_stays_unknown(fake_executor):
    node = Node(
        id=import_id("dynamic"),
        type=NodeType.IMPORT,
        name="dynamic",
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        check_command=None,
    )
    graph = DepGraph().with_node(node)

    out = certify(graph, node.id, fake_executor)

    assert out.get(node.id).state is State.UNKNOWN
    assert fake_executor.calls == []  # host never ran a check


def test_certify_unknown_node_returns_graph_unchanged(fake_executor):
    graph = DepGraph().with_node(_import("cv2"))
    out = certify(graph, "import:nope", fake_executor)
    assert out is graph
    assert fake_executor.calls == []


def test_certify_runs_the_nodes_check_command(fake_executor, make_result_fixture):
    imp = _import("cv2")
    graph = DepGraph().with_node(imp)
    fake_executor.responses = {"import cv2": make_result_fixture(returncode=0)}

    certify(graph, imp.id, fake_executor)

    assert fake_executor.calls == ['python -c "import cv2"']


def test_certify_revocation_satisfied_back_to_missing(make_result_fixture):
    from conftest import FakeExecutor  # type: ignore

    imp = _import("cv2")
    graph = DepGraph().with_node(imp)

    green = FakeExecutor(responses={"import cv2": make_result_fixture(returncode=0)})
    certified = certify(graph, imp.id, green, cycle=1)
    assert certified.get(imp.id).state is State.SATISFIED

    # A later mutation breaks the import; re-certifying flips it back.
    red = FakeExecutor(
        responses={"import cv2": make_result_fixture(returncode=1, stderr="broke")}
    )
    revoked = certify(certified, imp.id, red, cycle=2)
    assert revoked.get(imp.id).state is State.MISSING


def test_certify_all_walks_layers_in_order(make_result_fixture):
    from conftest import FakeExecutor  # type: ignore

    graph = (
        DepGraph()
        .with_node(_syslib("libGL.so.1"))  # system
        .with_node(_package("numpy", "1.26.4"))  # pip
        .with_node(_import("numpy"))  # naming
        .with_node(_test_node())  # tests
    )
    ex = FakeExecutor(default=make_result_fixture(returncode=0))

    certify_all(graph, ex)

    # The recorded command order must respect layer priority:
    # system (ldconfig) -> pip (pip show) -> naming (import) -> tests (pytest).
    order = [
        ("ldconfig", next(i for i, c in enumerate(ex.calls) if "ldconfig" in c)),
        ("pip show", next(i for i, c in enumerate(ex.calls) if "pip show" in c)),
        ("import", next(i for i, c in enumerate(ex.calls) if "import numpy" in c)),
        ("pytest", next(i for i, c in enumerate(ex.calls) if "pytest" in c)),
    ]
    indices = [i for _name, i in order]
    assert indices == sorted(indices)


def test_certify_all_certifies_each_node(make_result_fixture):
    from conftest import FakeExecutor  # type: ignore

    graph = (
        DepGraph()
        .with_node(_package("numpy", "1.26.4"))
        .with_node(_import("numpy"))
    )
    responses = {
        "pip show numpy": make_result_fixture(returncode=0),
        "import numpy": make_result_fixture(returncode=1, stderr="boom"),
    }
    ex = FakeExecutor(responses=responses)

    out = certify_all(graph, ex, cycle=5)

    assert out.get(package_id("numpy", "1.26.4")).state is State.SATISFIED
    assert out.get(package_id("numpy", "1.26.4")).certified_cycle == 5
    assert out.get(import_id("numpy")).state is State.MISSING
