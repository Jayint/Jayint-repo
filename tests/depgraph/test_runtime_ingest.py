"""Tests for runtime_ingest.ingest_runtime_failures (pure, no Docker)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.ids import (
    TEST_NODE_ID, config_id, package_id, service_id, syslib_id, tool_id,
)
from python_deps.depgraph.runtime_classify import Discovery
from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, EdgeType, Layer, Node, NodeType, State,
)


def _test_node() -> Node:
    return Node(
        id=TEST_NODE_ID,
        type=NodeType.TEST,
        name="repo_tests_pass",
        layer=Layer.TESTS,
        discovered_by=DiscoveredBy.GOAL,
    )


def _base_graph() -> DepGraph:
    return DepGraph().with_node(_test_node())


# ── append-new ───────────────────────────────────────────────────────────────

def test_append_new_package_node():
    graph = _base_graph()
    obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)

    node = new_graph.get(package_id("requests", None))
    assert node is not None
    assert node.type is NodeType.PACKAGE
    assert node.discovered_by is DiscoveredBy.RUNTIME
    assert node.check_command == 'python3 -c "import requests"'
    assert node.data.get("runtime_confidence") == "runtime-deterministic"
    assert len(discoveries) == 1


def test_append_new_syslib_node():
    graph = _base_graph()
    obs = [("python app.py", "ImportError: libGL.so.1: cannot open shared object file")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)

    node = new_graph.get(syslib_id("libGL.so.1"))
    assert node is not None
    assert node.type is NodeType.SYSTEM_LIB
    assert node.discovered_by is DiscoveredBy.RUNTIME
    assert node.check_command == "ldconfig -p | grep -q libGL.so.1"


def test_append_new_tool_node():
    graph = _base_graph()
    obs = [("make all", "make: command not found")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)

    node = new_graph.get(tool_id("make"))
    assert node is not None
    assert node.type is NodeType.TOOL
    assert node.check_command == "command -v make"


def test_explicit_apt_hint_makes_runtime_tool_deterministically_emittable():
    graph = _base_graph()
    output = "FLAC utility unavailable; run `apt-get install flac`"
    new_graph, discoveries = ingest_runtime_failures(
        graph, [("python -m pytest", output)]
    )

    node = new_graph.get(syslib_id("flac"))
    assert discoveries and node is not None
    assert node.chosen_fix == "apt:flac"
    assert node.fix_candidates == ("apt:flac",)
    assert node.check_command == "dpkg -s flac >/dev/null 2>&1"
    assert (
        "apt-get install -y --no-install-recommends flac"
        in render_build_script(new_graph)
    )


def test_known_missing_executable_uses_curated_apt_provider():
    graph = _base_graph()
    output = (
        "AudioConverter threw FileNotFoundError with message: "
        "[Errno 2] No such file or directory: 'ffprobe'"
    )
    new_graph, discoveries = ingest_runtime_failures(
        graph, [("python -m pytest", output)]
    )

    node = new_graph.get(tool_id("ffprobe"))
    assert discoveries and node is not None
    assert node.chosen_fix == "apt:ffmpeg"
    assert node.fix_candidates == ("apt:ffmpeg",)
    assert node.check_command == "command -v ffprobe"
    assert (
        "apt-get install -y --no-install-recommends ffmpeg"
        in render_build_script(new_graph)
    )


def test_append_new_config_node():
    graph = _base_graph()
    obs = [("python app.py", "KeyError: 'DATABASE_URL'")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)

    node = new_graph.get(config_id("DATABASE_URL"))
    assert node is not None
    assert node.type is NodeType.CONFIG
    assert node.check_command == "printenv DATABASE_URL"


# ── service advisory (no check_command flip) ─────────────────────────────────

def test_append_service_node_advisory():
    graph = _base_graph()
    obs = [("python manage.py migrate", "psycopg2.OperationalError: could not connect to server")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)

    node = new_graph.get(service_id("postgres"))
    assert node is not None
    assert node.type is NodeType.SERVICE
    assert node.discovered_by is DiscoveredBy.RUNTIME
    # Services are advisory — check_command stays None (certify skip-guards them)
    assert node.check_command is None
    assert node.state is State.UNKNOWN


def test_append_runtime_redis_node_is_confirmed_and_certifiable():
    graph = _base_graph()
    obs = [(
        "python -m pytest",
        "redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379",
    )]
    new_graph, _ = ingest_runtime_failures(graph, obs)

    node = new_graph.get(service_id("redis"))
    assert node is not None
    assert "6379" in node.check_command
    assert node.data["service_confidence"] == "confirmed"
    assert node.data["start_recipe"]["system_package"] == "redis-server"
    prerequisite = new_graph.get(syslib_id("redis-server"))
    assert prerequisite is not None
    assert prerequisite.chosen_fix == "apt:redis-server"
    assert (node.id, prerequisite.id, "runtime-service") in {
        (edge.src, edge.dst, edge.origin) for edge in new_graph.edges
    }


def test_runtime_redis_promotes_existing_advisory_service():
    existing = Node(
        id=service_id("redis"), type=NodeType.SERVICE, name="redis",
        layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.UNKNOWN, check_command=None,
        data={"service_confidence": "inferred"},
    )
    graph = _base_graph().with_node(existing)
    new_graph, _ = ingest_runtime_failures(graph, [(
        "python -m pytest",
        "redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379",
    )])

    node = new_graph.get(service_id("redis"))
    assert node.check_command is not None and "6379" in node.check_command
    assert node.data["service_confidence"] == "confirmed"
    assert node.data["start_recipe"]["start"] == "redis-server --daemonize yes"


# ── edge attribution (Test --requires--> node, origin="runtime") ─────────────

def test_runtime_edge_hangs_off_test_node():
    graph = _base_graph()
    obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
    new_graph, _ = ingest_runtime_failures(graph, obs)

    edges = [e for e in new_graph.edges
             if e.src == TEST_NODE_ID and e.relation is EdgeType.REQUIRES
             and e.origin == "runtime"]
    assert len(edges) == 1
    assert edges[0].dst == package_id("requests", None)


# ── annotate-existing (idempotent across two passes) ─────────────────────────

def test_annotate_existing_node_is_idempotent():
    graph = _base_graph()
    obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]

    graph1, discoveries1 = ingest_runtime_failures(graph, obs)
    graph2, discoveries2 = ingest_runtime_failures(graph1, obs)

    # Same number of nodes both times — no duplicate appended
    assert len(graph2.nodes) == len(graph1.nodes)
    # Edge deduped — still exactly one runtime edge to the package
    runtime_edges = [e for e in graph2.edges
                     if e.src == TEST_NODE_ID and e.origin == "runtime"]
    assert len(runtime_edges) == 1
    # Both passes returned a discovery
    assert len(discoveries1) == 1
    assert len(discoveries2) == 1


def test_annotate_existing_sets_runtime_confidence():
    """A package already in the graph (static) gets runtime_confidence annotated."""
    existing = Node(
        id=package_id("requests", None),
        type=NodeType.PACKAGE,
        name="requests",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN,
    )
    graph = _base_graph().with_node(existing)
    obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)

    node = new_graph.get(package_id("requests", None))
    assert node.data.get("runtime_confidence") == "runtime-deterministic"
    # discovered_by must not be silently downgraded
    # (runtime evidence is stronger; annotated node picks up RUNTIME provenance)
    assert node.discovered_by is DiscoveredBy.RUNTIME
    assert len(discoveries) == 1


# ── ignore-set produces no mutation ──────────────────────────────────────────

def test_no_matching_distribution_ignored():
    graph = _base_graph()
    obs = [("pip install flask", "No matching distribution found for flask==99.0")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)

    assert new_graph is graph or len(new_graph.nodes) == len(graph.nodes)
    assert discoveries == []


def test_assertion_error_ignored():
    graph = _base_graph()
    obs = [("python -m pytest", "AssertionError: assert 1 == 2")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)
    assert discoveries == []


# ── original graph never mutated (immutability) ──────────────────────────────

def test_original_graph_unchanged():
    graph = _base_graph()
    original_node_count = len(graph.nodes)
    obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
    ingest_runtime_failures(graph, obs)
    assert len(graph.nodes) == original_node_count


# ── T3a: per-observation classifier exception never bubbles ──────────────────

def test_classifier_exception_never_raises():
    """T3a: if a classifier raises, ingest must return (graph_unchanged, []) without raising."""
    graph = _base_graph()

    def _boom(cmd, out):
        raise RuntimeError("boom")

    new_graph, found = ingest_runtime_failures(
        graph,
        [("python app.py", "ModuleNotFoundError: No module named 'requests'")],
        classifiers=(_boom,),
    )
    assert found == []
    # Graph is unchanged (no nodes added beyond the original Test node)
    assert len(new_graph.nodes) == len(graph.nodes)


# ── C3: versioned static node annotated, no unversioned duplicate ─────────────

def test_versioned_static_package_annotated_no_duplicate():
    """C3: a runtime ModuleNotFoundError for 'requests' must annotate the versioned
    static node pkg:requests==2.31.0, not append a duplicate unversioned pkg:requests."""
    versioned = Node(
        id=package_id("requests", "2.31.0"),
        type=NodeType.PACKAGE,
        name="requests",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN,
    )
    graph = _base_graph().with_node(versioned)
    obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
    new_graph, discoveries = ingest_runtime_failures(graph, obs)

    # (a) versioned node is annotated with runtime provenance and confidence
    annotated = new_graph.get(package_id("requests", "2.31.0"))
    assert annotated is not None
    assert annotated.discovered_by is DiscoveredBy.RUNTIME
    assert annotated.data.get("runtime_confidence") == "runtime-deterministic"

    # (b) no unversioned duplicate was appended
    assert new_graph.get(package_id("requests", None)) is None, (
        "must not append unversioned pkg:requests when versioned pkg:requests==2.31.0 exists"
    )

    # (c) node count unchanged (Test + one versioned pkg)
    assert len(new_graph.nodes) == len(graph.nodes)
    assert len(discoveries) == 1
