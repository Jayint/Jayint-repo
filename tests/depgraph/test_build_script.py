from python_deps.depgraph.schema import (
    DepGraph, Node, Edge, NodeType, Layer, State, DiscoveredBy, EdgeType,
)
from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.block import Block


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
    assert "provider=apt:libpq-dev" in out
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


def _need(id_, type_, name, layer, **kw):
    return Node(id=id_, type=type_, name=name, layer=layer,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING, **kw)


def test_need_stubs_are_comment_only():
    g = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES,
              check_command="pg_isready -q", evidence="ev:readme:db"),
        _need("config:DATABASE_URL", NodeType.CONFIG, "DATABASE_URL", Layer.CONFIG,
              evidence="ev:settings:DATABASE_URL"),
    ))
    out = render_build_script(g)
    assert "#@need service:postgres  state=missing" in out
    assert "#@check pg_isready -q" in out
    assert "#@need config:DATABASE_URL  state=missing" in out
    # services/config render AFTER pip (highest layer rank)
    assert out.index("psycopg2==2.9.9") < out.index("#@need service:postgres")
    # the stub carries NO real command. SERVICES is the last layer, so the
    # service stub runs to EOF; every non-blank line there must be a comment.
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith("#@need service:postgres"))
    body = lines[start:]
    assert any("(no command" in ln for ln in body)
    for ln in body:
        if ln.strip():
            assert ln.startswith("#"), f"non-comment line in #@need stub: {ln!r}"

    # exhaustively prove EVERY #@need stub (config + service) is comment-only:
    # anchor at the first #@need line — from there to EOF there must be no
    # executable line (every non-blank line starts with '#').
    first_need = next(i for i, ln in enumerate(lines) if ln.startswith("#@need "))
    for ln in lines[first_need:]:
        if ln.strip():
            assert ln.startswith("#"), f"non-comment line in #@need region: {ln!r}"


def test_manual_block_renders_and_suppresses_its_need():
    g = DepGraph(nodes=(
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES,
              check_command="pg_isready -q"),
    ))
    blk = Block(
        block_id="svc:postgres-init", wave="services",
        commands=("pg_ctl init && pg_ctl start",),
        target_node_ids=("service:postgres",),
        check_commands=("pg_isready -q",),
        evidence_refs=("ev:readme:db",),
    )
    out = render_build_script(g, manual_blocks=(blk,))
    # the LLM block is rendered with provenance
    assert ("#@block svc:postgres-init  source=llm-patch  "
            "targets=service:postgres") in out
    assert "pg_ctl init && pg_ctl start" in out
    # the covered node is NOT also a #@need
    assert "#@need service:postgres" not in out


def test_uncovered_need_still_stubbed_with_block_present():
    g = DepGraph(nodes=(
        _need("config:DATABASE_URL", NodeType.CONFIG, "DATABASE_URL", Layer.CONFIG),
    ))
    blk = Block(block_id="svc:x", wave="services", commands=("true",),
                target_node_ids=("service:other",))
    out = render_build_script(g, manual_blocks=(blk,))
    assert "#@need config:DATABASE_URL" in out


def test_block_appears_in_its_wave_section_after_pip():
    g = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES),
    ))
    blk = Block(block_id="svc:pg-init", wave="services",
                commands=("pg_ctl start",), target_node_ids=("service:postgres",))
    out = render_build_script(g, manual_blocks=(blk,))
    assert out.index("psycopg2==2.9.9") < out.index("pg_ctl start")


def test_block_with_empty_targets_renders_and_covers_nothing():
    g = DepGraph(nodes=(
        _need("config:DATABASE_URL", NodeType.CONFIG, "DATABASE_URL", Layer.CONFIG),
    ))
    blk = Block(block_id="meta:setup", wave="config", commands=("echo setup",),
                target_node_ids=())
    out = render_build_script(g, manual_blocks=(blk,))
    assert "#@block meta:setup" in out
    assert "echo setup" in out
    assert "#@need config:DATABASE_URL" in out          # empty targets -> no coverage


def test_block_with_unknown_wave_lands_in_catch_all():
    blk = Block(block_id="post:warm", wave="post-install", commands=("true",),
                target_node_ids=())
    out = render_build_script(DepGraph(), manual_blocks=(blk,))
    assert "(UNSCHEDULED BLOCKS)" in out
    assert "#@block post:warm" in out
    assert "true" in out


def test_manifest_counts_hash_and_meta():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9",
             resolved_python="3.11", resolved_platform="linux/amd64",
             exclude_newer="2026-06-01"),
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES),
    ))
    out = render_build_script(g)
    preamble = out[:out.index("set -Eeuo pipefail")]
    assert "#   nodes: 2 reciped (1 system, 1 pip) + 1 needs (1 service)" in preamble
    assert "#   graph-hash: sha256:" in preamble
    # meta fields live in the comment header, before the set line (not in body)
    for needle in ("python: 3.11", "platform: linux/amd64", "exclude-newer: 2026-06-01"):
        assert any(needle in ln and ln.startswith("#")
                   for ln in preamble.splitlines()), needle


def test_determinism_with_mixed_tier_insertion_order():
    nodes = (
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _apt("tool:gcc", "gcc", "apt:gcc", type_=NodeType.TOOL, layer=Layer.TOOLCHAIN),
    )
    g1 = DepGraph(nodes=nodes)
    g2 = DepGraph(nodes=tuple(reversed(nodes)))
    assert render_build_script(g1) == render_build_script(g2)   # insertion-order invariant
    assert render_build_script(g1) == render_build_script(g1)   # pure: same in, same out


def test_closure_meta_is_insertion_order_invariant():
    a = _pkg("pkg:aaa", "aaa", "1.0", resolved_python="3.10")
    b = _pkg("pkg:zzz", "zzz", "2.0", resolved_python="3.11")
    g1 = DepGraph(nodes=(a, b))
    g2 = DepGraph(nodes=(b, a))
    assert render_build_script(g1) == render_build_script(g2)
    # lowest-id package wins deterministically -> python 3.10
    assert "python: 3.10" in render_build_script(g1)
