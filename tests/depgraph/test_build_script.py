from python_deps.depgraph.schema import (
    DepGraph, Node, Edge, NodeType, Layer, State, DiscoveredBy, EdgeType,
)
from python_deps.depgraph.build_script import render_build_script


def test_empty_graph_emits_preamble():
    out = render_build_script(DepGraph())
    lines = out.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT." in lines
    assert "set -Eeuo pipefail" in lines
    assert out.endswith("\n")


def _pkg(id_, name, version, layer=Layer.PIP, **kw):
    return Node(id=id_, type=NodeType.PACKAGE, name=name, layer=layer,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                version=version, **kw)


def _apt(id_, name, fix, type_=NodeType.SYSTEM_LIB, layer=Layer.SYSTEM, **kw):
    return Node(id=id_, type=type_, name=name, layer=layer,
                discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                chosen_fix=fix, **kw)


def test_deterministic_core_sections_and_commands():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9", evidence="ev:import:psycopg2"),
    ))
    g = g.with_edge(Edge(src="pkg:psycopg2", dst="syslib:libpq-dev",
                         relation=EdgeType.REQUIRES))
    out = render_build_script(g)

    # one hoisted apt-get update, exactly once
    assert out.count("apt-get update") == 1
    # section headers present and SYSTEM precedes PIP
    assert (out.index("# ==================== SYSTEM ====================")
            < out.index("# ==================== PIP ===================="))
    # the real commands
    assert "apt-get install -y --no-install-recommends libpq-dev" in out
    assert ("python3 -m pip install --break-system-packages --no-deps "
            "psycopg2==2.9.9") in out
    # annotation provenance is present
    assert "#@node pkg:psycopg2  version=2.9.9  requires=syslib:libpq-dev" in out
    assert "evidence=ev:import:psycopg2" in out
    # libpq-dev install line appears before psycopg2 install line (topo)
    assert out.index("libpq-dev\n") < out.index("psycopg2==2.9.9")


def test_apt_update_hoisted_iff_system_nodes_present():
    # POSITIVE: emitted for a system node (fails against the Task 1 stub -> real RED)
    g_sys = DepGraph(nodes=(_apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),))
    assert "apt-get update" in render_build_script(g_sys)
    # NEGATIVE: not emitted for a pip-only graph
    g_pip = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"),))
    assert "apt-get update" not in render_build_script(g_pip)


def test_apt_update_hoisted_once_for_multiple_system_nodes():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _apt("syslib:build-essential", "build-essential", "apt:build-essential"),
    ))
    out = render_build_script(g)
    assert out.count("apt-get update") == 1
    assert out.count("export DEBIAN_FRONTEND=noninteractive") == 1
    update_pos = out.index("apt-get update")
    assert update_pos < out.index("libpq-dev\n")
    assert update_pos < out.index("build-essential\n")


def test_node_check_command_emitted_between_annotation_and_install():
    g = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9",
             check_command="python -m pip show psycopg2"),
    ))
    out = render_build_script(g)
    node_idx = out.index("#@node pkg:psycopg2")
    check_idx = out.index("#@check python -m pip show psycopg2")
    install_idx = out.index("psycopg2==2.9.9")
    assert node_idx < check_idx < install_idx
