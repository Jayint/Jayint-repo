"""Stage 4 probing: discover SystemLib / Tool nodes from install/import output.

All tests use ``FakeExecutor`` (from conftest) — no Docker, no network, no real
``pip``/``python``.  Probing is discovery-only here (no remediation loop); the
host certifies the fix later (Task 8).
"""

from __future__ import annotations

from python_deps.depgraph.ids import import_id, package_id, syslib_id, tool_id
from python_deps.depgraph.probe import import_probe, install_closure
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
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
        fix_candidates=(f"pip:{name}",),
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


# --------------------------------------------------------------------------- #
# install_closure: build-time gaps -> Tool nodes                              #
# --------------------------------------------------------------------------- #
def test_install_closure_build_gap_creates_tool_node(fake_executor, make_result_fixture):
    pkg = _package("psycopg2", "2.9.9")
    graph = DepGraph().with_node(pkg)
    fake_executor.responses = {
        "pip install": make_result_fixture(
            returncode=1,
            stderr="    Error: pg_config executable not found.\n",
        )
    }

    out = install_closure(graph, fake_executor)

    tool = out.get(tool_id("pg_config"))
    assert tool is not None
    assert tool.type is NodeType.TOOL
    assert tool.name == "pg_config"
    assert tool.layer is Layer.TOOLCHAIN
    assert tool.discovered_by is DiscoveredBy.PROBE
    assert tool.state is State.MISSING
    assert tool.fix_candidates == ("apt:libpq-dev",)
    assert "pg_config executable not found" in (tool.evidence or "")
    assert tool.check_command == "command -v pg_config"


def test_install_closure_links_tool_from_owning_package(fake_executor, make_result_fixture):
    pkg = _package("psycopg2", "2.9.9")
    graph = DepGraph().with_node(pkg)
    fake_executor.responses = {
        "pip install": make_result_fixture(
            returncode=1, stderr="Error: pg_config executable not found."
        )
    }

    out = install_closure(graph, fake_executor)

    deps = out.requires_of(pkg.id)
    assert any(d.id == tool_id("pg_config") for d in deps)


def test_install_closure_records_attempt_on_packages(fake_executor, make_result_fixture):
    pkg = _package("psycopg2", "2.9.9")
    graph = DepGraph().with_node(pkg)
    fake_executor.responses = {
        "pip install": make_result_fixture(
            returncode=1, stderr="Error: pg_config executable not found."
        )
    }

    out = install_closure(graph, fake_executor)

    node = out.get(pkg.id)
    assert len(node.attempts) == 1
    assert node.attempts[0].command.startswith("python -m pip install")
    assert node.attempts[0].outcome == "failed"
    # the command pins the resolved version
    assert "psycopg2==2.9.9" in node.attempts[0].command


def test_install_closure_clean_install_no_tool_nodes(fake_executor, make_result_fixture):
    pkg = _package("requests", "2.31.0")
    graph = DepGraph().with_node(pkg)
    fake_executor.responses = {
        "pip install": make_result_fixture(returncode=0, stdout="Successfully installed")
    }

    out = install_closure(graph, fake_executor)

    assert not [n for n in out.nodes if n.type is NodeType.TOOL]
    node = out.get(pkg.id)
    assert node.attempts[0].outcome == "succeeded"


def test_install_closure_unknown_tool_has_empty_fix(fake_executor, make_result_fixture):
    # ``cc`` substring must NOT spuriously match inside ``gcc`` (word-boundary).
    pkg = _package("psycopg2", "2.9.9")
    graph = DepGraph().with_node(pkg)
    fake_executor.responses = {
        "pip install": make_result_fixture(
            returncode=1, stderr="gcc: command not found"
        )
    }

    out = install_closure(graph, fake_executor)

    assert out.get(tool_id("gcc")) is not None
    assert out.get(tool_id("cc")) is None  # not a false positive from "gcc"


# --------------------------------------------------------------------------- #
# import_probe: run-time gaps -> SystemLib nodes                              #
# --------------------------------------------------------------------------- #
def test_import_probe_native_lib_creates_syslib(fake_executor, make_result_fixture):
    pkg = _package("opencv-python", "4.9.0.80")
    imp = _import("cv2")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(imp)
        .with_edge(Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="resolver"))
    )
    fake_executor.responses = {
        'import cv2': make_result_fixture(
            returncode=1,
            stderr="ImportError: libGL.so.1: cannot open shared object file: "
            "No such file or directory",
        )
    }

    out = import_probe(graph, fake_executor)

    syslib = out.get(syslib_id("libGL.so.1"))
    assert syslib is not None
    assert syslib.type is NodeType.SYSTEM_LIB
    assert syslib.name == "libGL.so.1"
    assert syslib.layer is Layer.SYSTEM
    assert syslib.discovered_by is DiscoveredBy.PROBE
    assert syslib.state is State.MISSING
    assert syslib.fix_candidates == ("apt:libgl1",)
    assert "libGL.so.1" in (syslib.evidence or "")
    assert syslib.check_command == "ldconfig -p | grep libGL.so.1"


def test_import_probe_links_syslib_from_owning_package(fake_executor, make_result_fixture):
    pkg = _package("opencv-python", "4.9.0.80")
    imp = _import("cv2")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(imp)
        .with_edge(Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="resolver"))
    )
    fake_executor.responses = {
        'import cv2': make_result_fixture(
            returncode=1,
            stderr="ImportError: libGL.so.1: cannot open shared object file",
        )
    }

    out = import_probe(graph, fake_executor)

    deps = out.requires_of(pkg.id)
    assert any(d.id == syslib_id("libGL.so.1") for d in deps)


def test_import_probe_records_attempt_on_import(fake_executor, make_result_fixture):
    pkg = _package("opencv-python", "4.9.0.80")
    imp = _import("cv2")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(imp)
        .with_edge(Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="resolver"))
    )
    fake_executor.responses = {
        'import cv2': make_result_fixture(
            returncode=1, stderr="ImportError: libGL.so.1: cannot open shared object file"
        )
    }

    out = import_probe(graph, fake_executor)

    node = out.get(imp.id)
    assert len(node.attempts) == 1
    assert node.attempts[0].command == 'python -c "import cv2"'
    assert node.attempts[0].outcome == "failed"


def test_import_probe_clean_import_no_syslib(fake_executor, make_result_fixture):
    pkg = _package("numpy", "1.26.4")
    imp = _import("numpy")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(imp)
        .with_edge(Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="resolver"))
    )
    fake_executor.responses = {
        'import numpy': make_result_fixture(returncode=0, stdout="")
    }

    out = import_probe(graph, fake_executor)

    assert not [n for n in out.nodes if n.type is NodeType.SYSTEM_LIB]
    node = out.get(imp.id)
    assert node.attempts[0].outcome == "succeeded"


def test_import_probe_non_native_failure_creates_no_syslib(
    fake_executor, make_result_fixture
):
    # A plain ModuleNotFoundError is NOT a native-lib gap -> no SystemLib node.
    pkg = _package("lxml", "5.2.1")
    imp = _import("lxml")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(imp)
        .with_edge(Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="resolver"))
    )
    fake_executor.responses = {
        'import lxml': make_result_fixture(
            returncode=1, stderr="ModuleNotFoundError: No module named 'lxml'"
        )
    }

    out = import_probe(graph, fake_executor)

    assert not [n for n in out.nodes if n.type is NodeType.SYSTEM_LIB]


def test_import_probe_native_risk_package_probed_by_name(fake_executor, make_result_fixture):
    # psycopg2 is a native-risk package whose import name == package name; it is
    # probed even with no static Import node, and its syslib links back to it.
    pkg = _package("psycopg2", "2.9.9")
    graph = DepGraph().with_node(pkg)
    fake_executor.responses = {
        'import psycopg2': make_result_fixture(
            returncode=1,
            stderr="ImportError: libpq.so.5: cannot open shared object file",
        )
    }

    out = import_probe(graph, fake_executor)

    syslib = out.get(syslib_id("libpq.so.5"))
    assert syslib is not None
    assert syslib.fix_candidates == ("apt:libpq5",)
    deps = out.requires_of(pkg.id)
    assert any(d.id == syslib_id("libpq.so.5") for d in deps)


def test_probe_returns_new_graph_originals_unchanged(fake_executor, make_result_fixture):
    pkg = _package("opencv-python", "4.9.0.80")
    imp = _import("cv2")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(imp)
        .with_edge(Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="resolver"))
    )
    fake_executor.responses = {
        'import cv2': make_result_fixture(
            returncode=1, stderr="ImportError: libGL.so.1: cannot open shared object file"
        )
    }

    out = import_probe(graph, fake_executor)

    assert out is not graph
    assert graph.get(syslib_id("libGL.so.1")) is None  # original untouched
    assert len(graph.get(imp.id).attempts) == 0
