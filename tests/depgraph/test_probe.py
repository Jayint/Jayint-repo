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


def _predicted_syslib(apt: str) -> Node:
    return Node(
        id=syslib_id(apt),
        type=NodeType.SYSTEM_LIB,
        name=apt,
        layer=Layer.SYSTEM,
        discovered_by=DiscoveredBy.RESOLVER,  # a prediction
        state=State.UNKNOWN,
        check_command=f"dpkg -s {apt}",
        fix_candidates=(f"apt:{apt}",),
    )


def _predicted_tool(apt: str) -> Node:
    return Node(
        id=tool_id(apt),
        type=NodeType.TOOL,
        name=apt,
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.RESOLVER,  # a prediction
        state=State.UNKNOWN,
        check_command=f"dpkg -s {apt}",
        fix_candidates=(f"apt:{apt}",),
    )


# --------------------------------------------------------------------------- #
# Reconciliation: probe observation merges into a resolver prediction          #
# --------------------------------------------------------------------------- #
def test_import_probe_reconciles_predicted_syslib(fake_executor, make_result_fixture):
    # opencv predicted libgl1 (apt-keyed); probe observes libGL.so.1 (soname).
    pkg = _package("opencv-python", "4.9.0.80")
    imp = _import("cv2")
    predicted = _predicted_syslib("libgl1")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(imp)
        .with_node(predicted)
        .with_edge(Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="resolver"))
        .with_edge(Edge(src=pkg.id, dst=predicted.id, relation=EdgeType.REQUIRES, origin="resolver"))
    )
    fake_executor.responses = {
        "import cv2": make_result_fixture(
            returncode=1,
            stderr="ImportError: libGL.so.1: cannot open shared object file",
        )
    }

    out = import_probe(graph, fake_executor)

    # No duplicate observed node — the prediction is reconciled in place.
    assert out.get(syslib_id("libGL.so.1")) is None
    node = out.get(syslib_id("libgl1"))
    assert node is not None
    assert node.discovered_by is DiscoveredBy.RESOLVER  # discovery origin kept
    assert node.check_command == "ldconfig -p | grep libGL.so.1"  # real check
    assert "libGL.so.1" in (node.evidence or "")
    assert node.attempts and node.attempts[-1].outcome == "failed"
    # single requires edge from the owning package (deduped)
    libs = [d for d in out.requires_of(pkg.id) if d.id == syslib_id("libgl1")]
    assert len(libs) == 1


def test_install_closure_reconciles_predicted_tool(fake_executor, make_result_fixture):
    # psycopg2 predicted libpq-dev (apt-keyed); build observes pg_config (tool).
    pkg = _package("psycopg2", "2.9.9")
    predicted = _predicted_tool("libpq-dev")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(predicted)
        .with_edge(Edge(src=pkg.id, dst=predicted.id, relation=EdgeType.REQUIRES, origin="resolver"))
    )
    fake_executor.responses = {
        "pip install": make_result_fixture(
            returncode=1, stderr="Error: pg_config executable not found."
        )
    }

    out = install_closure(graph, fake_executor)

    assert out.get(tool_id("pg_config")) is None  # no duplicate observed node
    node = out.get(tool_id("libpq-dev"))
    assert node is not None
    assert node.discovered_by is DiscoveredBy.RESOLVER
    assert node.check_command == "command -v pg_config"
    assert "pg_config" in (node.evidence or "")
    tools = [d for d in out.requires_of(pkg.id) if d.id == tool_id("libpq-dev")]
    assert len(tools) == 1


def test_reconcile_skips_non_resolver_prediction(fake_executor, make_result_fixture):
    # A pre-existing node at the predicted id but discovered_by=PROBE is NOT a
    # resolver prediction: reconciliation is skipped and a fresh observed node is
    # created instead (the guard at _reconcile_predicted).
    pkg = _package("psycopg2", "2.9.9")
    stale = Node(
        id=tool_id("libpq-dev"),
        type=NodeType.TOOL,
        name="libpq-dev",
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.PROBE,  # not a prediction
        state=State.MISSING,
    )
    graph = DepGraph().with_node(pkg).with_node(stale)
    fake_executor.responses = {
        "pip install": make_result_fixture(
            returncode=1, stderr="Error: pg_config executable not found."
        )
    }

    out = install_closure(graph, fake_executor)

    observed = out.get(tool_id("pg_config"))
    assert observed is not None
    assert observed.discovered_by is DiscoveredBy.PROBE
    # the stale PROBE node was left untouched (no in-place reconcile)
    assert out.get(tool_id("libpq-dev")).discovered_by is DiscoveredBy.PROBE


def test_install_closure_wheel_for_attribution_beats_fallback(
    fake_executor, make_result_fixture
):
    # "Building wheel for X" attributes the build-time gap to X, NOT to the
    # native-risk fallback set (psycopg2).
    target = _package("somepkg", "1.0")
    native = _package("psycopg2", "2.9.9")  # native-risk fallback candidate
    graph = DepGraph().with_node(target).with_node(native)
    fake_executor.responses = {
        "pip install": make_result_fixture(
            returncode=1,
            stderr="Building wheel for somepkg\n  Error: pg_config executable not found.",
        )
    }

    out = install_closure(graph, fake_executor)

    tool = tool_id("pg_config")
    assert any(d.id == tool for d in out.requires_of(target.id))
    assert not any(d.id == tool for d in out.requires_of(native.id))


def test_import_probe_syslib_falls_back_to_import_node(
    fake_executor, make_result_fixture
):
    # An Import node with a native-lib failure and NO owning Package: the requires
    # edge originates from the Import id (the _edge_sources fallback).
    imp = _import("cv2")
    graph = DepGraph().with_node(imp)
    fake_executor.responses = {
        "import cv2": make_result_fixture(
            returncode=1,
            stderr="ImportError: libGL.so.1: cannot open shared object file",
        )
    }

    out = import_probe(graph, fake_executor)

    syslib = syslib_id("libGL.so.1")
    assert out.get(syslib) is not None
    assert any(d.id == syslib for d in out.requires_of(imp.id))


def test_install_closure_no_packages_returns_input(fake_executor):
    # No Package nodes -> early return of the same graph object (no executor call).
    graph = DepGraph().with_node(_import("cv2"))
    out = install_closure(graph, fake_executor)
    assert out is graph
    assert fake_executor.calls == []


def test_install_closure_excludes_resolver_missing_packages(
    fake_executor, make_result_fixture
):
    # A resolver-diagnosed MISSING package (unresolvable / conflict placeholder,
    # no real version) must NOT be added to the bulk `pip install` — including it
    # makes the single command fail and poisons the whole closure (every good
    # package then certifies MISSING). It must be installed without the bad one.
    from dataclasses import replace

    good = _package("requests", "2.31.0")
    bad = replace(
        _package("does-not-exist-zzz", ""), state=State.MISSING, version=None,
        evidence="not found in the package registry",
    )
    graph = DepGraph().with_node(good).with_node(bad)
    fake_executor.responses = {
        "pip install": make_result_fixture(returncode=0, stdout="Successfully installed")
    }

    out = install_closure(graph, fake_executor)

    install_calls = [c for c in fake_executor.calls if "pip install" in c]
    assert len(install_calls) == 1
    assert "requests==2.31.0" in install_calls[0]
    assert "does-not-exist-zzz" not in install_calls[0]
    # The good package still records its (successful) install attempt; the missing
    # node is left untouched (no spurious install attempt).
    assert out.get(good.id).attempts and out.get(good.id).attempts[0].outcome == "succeeded"
    assert out.get(bad.id).attempts == ()
    assert out.get(bad.id).state is State.MISSING


def test_install_closure_uses_generous_timeout(fake_executor, make_result_fixture):
    # A cold install of a large closure can exceed the 300s default and FALSE-fail,
    # which then certifies the whole graph MISSING (breaks honest certification).
    # The bulk install must therefore ask for generous headroom.
    from python_deps.depgraph.probe import INSTALL_TIMEOUT

    pkg = _package("requests", "2.31.0")
    graph = DepGraph().with_node(pkg)
    fake_executor.responses = {
        "pip install": make_result_fixture(returncode=0, stdout="Successfully installed")
    }

    install_closure(graph, fake_executor)

    install_calls = [
        i for i, c in enumerate(fake_executor.calls) if "pip install" in c
    ]
    assert install_calls, "expected a pip install call"
    assert fake_executor.timeouts[install_calls[0]] == INSTALL_TIMEOUT
    assert INSTALL_TIMEOUT >= 600


def test_import_probe_probes_each_name_once(fake_executor, make_result_fixture):
    # An Import node and a native-risk Package of the SAME name dedup to one probe.
    pkg = _package("psycopg2", "2.9.9")
    imp = _import("psycopg2")
    graph = (
        DepGraph()
        .with_node(pkg)
        .with_node(imp)
        .with_edge(
            Edge(src=imp.id, dst=pkg.id, relation=EdgeType.REQUIRES, origin="resolver")
        )
    )
    fake_executor.responses = {
        "import psycopg2": make_result_fixture(returncode=0, stdout="")
    }

    import_probe(graph, fake_executor)

    probes = [c for c in fake_executor.calls if 'import psycopg2' in c]
    assert len(probes) == 1


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


def test_import_probe_unknown_soname_uses_apt_file_fallback(fake_executor, make_result_fixture):
    # An import whose runtime gap is a soname NOT in the curated table.
    imp = _import("widget")
    graph = DepGraph().with_node(imp)
    fake_executor.responses = {
        'python -c "import widget"': make_result_fixture(
            returncode=1,
            stderr="ImportError: libwidget.so.3: cannot open shared object file",
        ),
        "sysconfig": make_result_fixture(stdout="x86_64-linux-gnu\n"),
        "apt-file search": make_result_fixture(
            stdout="libwidget3: /usr/lib/x86_64-linux-gnu/libwidget.so.3\n"
        ),
    }

    out = import_probe(graph, fake_executor)

    lib = out.get(syslib_id("libwidget.so.3"))
    assert lib is not None
    assert lib.type is NodeType.SYSTEM_LIB
    assert lib.state is State.MISSING
    assert lib.fix_candidates == ("apt:libwidget3",)
