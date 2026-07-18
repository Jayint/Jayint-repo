import re

from graph.model import (
    DepGraph, Node, Edge, NodeType, Layer, State, DiscoveredBy, EdgeType,
)
from graph.compile.build_script import render_build_script, _LAYER_ORDER
from graph.patch.block import Block, compile_replay_blocks
from graph.compile.emit import _is_reciped, _apt_name, _pip_spec


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


def _project(name="myproj", installable=True):
    return Node(id=f"project:{name}", type=NodeType.PROJECT, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN, data={"installable": installable})


_EDITABLE = "python3 -m pip install --break-system-packages --no-deps -e ."


def test_installable_project_editable_install_emitted_last():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _project(),
    ))
    g = g.with_edge(Edge(src="pkg:psycopg2", dst="syslib:libpq-dev",
                         relation=EdgeType.REQUIRES))
    out = render_build_script(g)
    # emitted exactly once, as the LAST install command (after every apt + pip line)
    assert out.count(_EDITABLE) == 1
    assert out.index(_EDITABLE) > out.index("psycopg2==2.9.9")
    assert out.index(_EDITABLE) > out.index("libpq-dev")
    # under its own PROJECT section header, and annotated so render_fidelity sees it
    assert "# ==================== PROJECT" in out
    assert out.index("# ==================== PROJECT") < out.index(_EDITABLE)
    assert "#@node project:myproj" in out


def test_non_installable_project_not_emitted():
    g = DepGraph(nodes=(
        _pkg("pkg:requests", "requests", "2.31.0"),
        _project(installable=False),
    ))
    out = render_build_script(g)
    assert _EDITABLE not in out
    assert "#@node project:myproj" not in out
    assert "# ==================== PROJECT" not in out


def test_installable_project_capstone_is_non_fatal_wrapped():
    # FIX B6: the capstone must be wrapped so its failure can never trip
    # `set -Eeuo pipefail` — the exact mechanism that killed pre-commit's
    # capstone under B5's suppression must not be replaced with a NEW way to
    # abort the build. A command inside an `if` CONDITION is exempt from
    # errexit regardless of its exit code, so this is the load-bearing check:
    # the raw `-e .` line must appear ONLY as an `if` condition, never bare.
    g = DepGraph(nodes=(_project(),))
    out = render_build_script(g)
    assert "set -Eeuo pipefail" in out
    lines = out.splitlines()
    editable_lines = [i for i, ln in enumerate(lines) if _EDITABLE in ln]
    assert editable_lines, "capstone must be emitted"
    for i in editable_lines:
        assert lines[i] == f"if {_EDITABLE} || " + (
            "python3 -m pip install --break-system-packages --no-deps ."
        )
        assert lines[i + 1] == "then"
        assert lines[i + 3] == "else"
        assert "V3_NODE_INSTALL_FAILED" in lines[i + 4]
        assert lines[i + 5] == "fi"


def test_installable_project_capstone_renders_for_previously_rejected_shape(tmp_path):
    # FIX B6: the pre-commit/pre-commit case. B5(a)'s static heuristic would
    # have flagged this exact shape (>=2 top-level importable dirs, no src/
    # layout) as "not safely installable" and suppressed the capstone —
    # dropping EBSR from 1.0 to 0 for a project whose capstone actually
    # works. B6 deletes the predictor: the capstone must render regardless.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1.0.0"\n'
    )
    (tmp_path / "ee").mkdir()
    (tmp_path / "ee" / "__init__.py").write_text("")
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "__init__.py").write_text("")
    proj = Node(
        id="project:myproj", type=NodeType.PROJECT, name="myproj", layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
        data={"installable": True}, provenance=str(tmp_path / "pyproject.toml"),
    )
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"), proj))
    out = render_build_script(g)
    assert _EDITABLE in out
    assert "#@pythonpath-env" not in out
    assert out.index(_EDITABLE) > out.index("requests==2.31.0")


def test_render_build_script_deterministic_with_installable_project():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _project(),
    ))
    g = g.with_edge(Edge(src="pkg:psycopg2", dst="syslib:libpq-dev",
                         relation=EdgeType.REQUIRES))
    assert render_build_script(g) == render_build_script(g)


def test_git_sourced_package_never_rendered_as_bare_pip_install():
    """Gate 2, end-to-end reachability: a package Gate 1 marks MISSING because
    its real ``uv.lock`` source is a git fork (never the default PyPI
    registry) must never be rendered as an install line by
    ``render_build_script`` -- ``populate_setup_commands``'s ``_is_reciped``
    gate (which already excludes ``data['uninstallable']``, wired by Fix A
    and reused by Gate 1's ``_missing_source_node``) closes this without any
    change needed in this module."""
    from graph.python.lanes.install.resolve_lock import _missing_source_node

    git_sourced = _missing_source_node(
        "infi-clickhouse-orm",
        "2.1.0",
        "'infi-clickhouse-orm' is sourced from git+https://github.com/PostHog/"
        "infi.clickhouse_orm@abc123 (uv.lock), not the default PyPI registry",
    )
    g = DepGraph(nodes=(
        git_sourced,
        _pkg("pkg:requests", "requests", "2.31.0"),
    ))
    out = render_build_script(g)
    assert "requests==2.31.0" in out
    assert "infi-clickhouse-orm" not in out
    assert re.search(r"pip install.*infi", out) is None


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
    assert out.index("libpq-dev") < out.index("psycopg2==2.9.9")


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
    assert update_pos < out.index("libpq-dev")
    assert update_pos < out.index("build-essential")


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


def test_block_with_unknown_wave_raises():
    import pytest
    from graph.patch.block import Block
    blk = Block(block_id="blk:x", wave="post-install", commands=("echo hi",),
                target_node_ids=(), provider_ids=(), check_commands=(), evidence_refs=())
    with pytest.raises(ValueError, match="illegal waves"):
        render_build_script(DepGraph(), (blk,))


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


def _rich_graph():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _apt("tool:gcc", "gcc", "apt:gcc", type_=NodeType.TOOL, layer=Layer.TOOLCHAIN,
             evidence="ev:build:psycopg2"),
        _pkg("pkg:typing-extensions", "typing-extensions", "4.11.0",
             evidence="ev:resolver"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9", build_from_source=True,
             evidence="ev:import:psycopg2"),
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES,
              check_command="pg_isready -q", evidence="ev:readme:db"),
        _need("config:DATABASE_URL", NodeType.CONFIG, "DATABASE_URL", Layer.CONFIG,
              evidence="ev:settings:DATABASE_URL"),
    ))
    for src, dst in (("pkg:psycopg2", "syslib:libpq-dev"),
                     ("pkg:psycopg2", "tool:gcc")):
        g = g.with_edge(Edge(src=src, dst=dst, relation=EdgeType.REQUIRES))
    return g


def test_every_reciped_node_installed_exactly_once():
    g = _rich_graph()
    out = render_build_script(g)
    for n in g.nodes:
        if not _is_reciped(n):
            continue
        if _apt_name(n) is not None:
            cmd = f"apt-get install -y --no-install-recommends {_apt_name(n)}"
        else:
            cmd = (f"python3 -m pip install --break-system-packages --no-deps "
                   f"{_pip_spec(n)}")
        assert out.count(cmd) == 1, f"{n.id}: expected 1 install line, got {out.count(cmd)}"


def test_build_from_source_and_toolchain_flags_in_annotations():
    out = render_build_script(_rich_graph())
    psycopg2_line = next(ln for ln in out.splitlines()
                         if ln.startswith("#@node pkg:psycopg2"))
    assert "build-from-source" in psycopg2_line
    gcc_line = next(ln for ln in out.splitlines()
                    if ln.startswith("#@node tool:gcc"))
    assert "toolchain" in gcc_line


def test_requires_edge_orders_lines():
    out = render_build_script(_rich_graph())
    assert out.index("libpq-dev") < out.index("psycopg2==2.9.9")
    assert out.index("gcc") < out.index("psycopg2==2.9.9")


def test_install_target_parity_with_compile_replay_blocks():
    g = _rich_graph()
    replay_targets = {nid for b in compile_replay_blocks(g) for nid in b.target_node_ids}
    out = render_build_script(g)
    # every replay target appears as a #@node annotation in the artifact
    for nid in replay_targets:
        assert f"#@node {nid}" in out
    # and the artifact introduces no extra #@node beyond the reciped set
    node_ids = {ln.split()[1] for ln in out.splitlines() if ln.startswith("#@node ")}
    assert node_ids == replay_targets


def test_golden_snapshot_byte_for_byte():
    out = render_build_script(_rich_graph())
    # mask the opaque digest (its value is covered by the determinism test)
    normalized = re.sub(r"sha256:[0-9a-f]{12}", "sha256:<HASH>", out)
    expected = (
        "#!/usr/bin/env bash\n"
        "#\n"
        "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.\n"
        "# Edit the graph and re-render; this file is an artifact, not a source.\n"
        "#\n"
        "#   nodes: 4 reciped (1 system, 1 toolchain, 2 pip) + 2 needs (1 service, 1 config)\n"
        "#   graph-hash: sha256:<HASH>\n"
        "#\n"
        "set -Eeuo pipefail\n"
        "\n"
        "# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.\n"
        'command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python\n'
        "\n"
        "# Ensure the pytest test-runner (testability-gate precondition; not a graph node).\n"
        'python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest\n'
        "\n"
        "# ==================== SYSTEM ====================\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "apt-get update\n"
        "#@node syslib:libpq-dev  provider=apt:libpq-dev  requires=-  unblocks=pkg:psycopg2\n"
        "if apt-get install -y --no-install-recommends libpq-dev\n"
        "then\n"
        "    :\n"
        "else\n"
        '    echo "V3_NODE_INSTALL_FAILED syslib:libpq-dev" >> /tmp/v3_failed_nodes.log\n'
        "fi\n"
        "\n"
        "# ==================== TOOLCHAIN ====================\n"
        "#@node tool:gcc  provider=apt:gcc  requires=-  unblocks=pkg:psycopg2  toolchain  evidence=ev:build:psycopg2\n"
        "if apt-get install -y --no-install-recommends gcc\n"
        "then\n"
        "    :\n"
        "else\n"
        '    echo "V3_NODE_INSTALL_FAILED tool:gcc" >> /tmp/v3_failed_nodes.log\n'
        "fi\n"
        "\n"
        "# ==================== PIP ====================\n"
        "#@node pkg:psycopg2  version=2.9.9  requires=syslib:libpq-dev,tool:gcc  build-from-source  evidence=ev:import:psycopg2\n"
        "if python3 -m pip install --break-system-packages --no-deps psycopg2==2.9.9\n"
        "then\n"
        "    :\n"
        "else\n"
        '    echo "V3_NODE_INSTALL_FAILED pkg:psycopg2" >> /tmp/v3_failed_nodes.log\n'
        "fi\n"
        "#@node pkg:typing-extensions  version=4.11.0  requires=-  evidence=ev:resolver\n"
        "if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.11.0\n"
        "then\n"
        "    :\n"
        "else\n"
        '    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions" >> /tmp/v3_failed_nodes.log\n'
        "fi\n"
        "\n"
        "# ==================== CONFIG ====================\n"
        "#\n"
        "#@need config:DATABASE_URL  state=missing\n"
        "#@evidence ev:settings:DATABASE_URL\n"
        "#     (no command — propose a governed block to satisfy this)\n"
        "\n"
        "# ==================== SERVICES ====================\n"
        "#\n"
        "#@need service:postgres  state=missing\n"
        "#@check pg_isready -q\n"
        "#@evidence ev:readme:db\n"
        "#     (no command — propose a governed block to satisfy this)\n"
    )
    assert normalized == expected


def test_python_shim_baked_after_pipefail():
    out = render_build_script(_rich_graph())
    shim = 'command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python'
    assert out.count(shim) == 1
    assert out.index("set -Eeuo pipefail") < out.index(shim)
    # shim precedes the first section header (runs before any node install/check)
    assert out.index(shim) < out.index("# ====================")


def test_python_shim_present_for_empty_graph():
    for g in (None, DepGraph()):
        out = render_build_script(g)
        assert 'ln -sf "$(command -v python3)" /usr/local/bin/python' in out


def test_runtime_and_interpreter_precede_pip():
    # base python (RUNTIME) + interpreter floor must be laid down BEFORE pip installs
    assert _LAYER_ORDER.index(Layer.RUNTIME) < _LAYER_ORDER.index(Layer.PIP)
    assert _LAYER_ORDER.index(Layer.INTERPRETER) < _LAYER_ORDER.index(Layer.PIP)


def test_config_precedes_tests():
    # env/config tier must be set up before the test tier runs
    assert _LAYER_ORDER.index(Layer.CONFIG) < _LAYER_ORDER.index(Layer.TESTS)


def test_build_script_order_agrees_with_certify_on_shared_tiers():
    from graph.core.certify import EXECUTION_LAYER_ORDER  # created in Step 3

    positions = [_LAYER_ORDER.index(L) for L in EXECUTION_LAYER_ORDER]
    assert positions == sorted(positions), (
        "build_script section order must not contradict certify execution order")


# ---------------------------------------------------------------------------
# FIX B1 — CONFIG needs with a KNOWN value render a machine-parseable
# `#@config-env VAR=value` marker (multi_docker_eval_adapter turns it into a
# Dockerfile ENV; setup.sh itself CANNOT persist an export past its own RUN
# layer). No value known -> unchanged advisory-comment-only stub.
# ---------------------------------------------------------------------------

def test_config_need_with_known_value_emits_config_env_marker():
    g = DepGraph(nodes=(
        _need("config:DJANGO_SETTINGS_MODULE", NodeType.CONFIG,
              "DJANGO_SETTINGS_MODULE", Layer.CONFIG,
              data={"config_value": "settings"}),
    ))
    out = render_build_script(g)
    assert "#@config-env DJANGO_SETTINGS_MODULE=settings" in out
    # still comment-only: setup.sh runs in a Docker RUN layer, so `export` here
    # would never persist into the later container the tests run in — the
    # marker is a hand-off for the Dockerfile renderer, not a live command.
    lines = out.splitlines()
    marker_line = next(ln for ln in lines if ln.startswith("#@config-env"))
    assert marker_line.startswith("#")


def test_config_need_without_value_has_no_marker():
    g = DepGraph(nodes=(
        _need("config:DATABASE_URL", NodeType.CONFIG, "DATABASE_URL", Layer.CONFIG),
    ))
    out = render_build_script(g)
    assert "#@config-env" not in out
    assert "#     (no command — propose a governed block to satisfy this)" in out


def test_config_need_with_unknown_sentinel_has_no_marker():
    g = DepGraph(nodes=(
        _need("config:DEBUG", NodeType.CONFIG, "DEBUG", Layer.CONFIG,
              data={"config_value": "?"}),
    ))
    out = render_build_script(g)
    assert "#@config-env" not in out


def test_config_env_marker_skips_secret_named_vars():
    g = DepGraph(nodes=(
        _need("config:DJANGO_SECRET_KEY", NodeType.CONFIG, "DJANGO_SECRET_KEY",
              Layer.CONFIG, data={"config_value": "insecure-dev-key"}),
    ))
    out = render_build_script(g)
    assert "#@config-env" not in out


def test_config_env_marker_skips_denylisted_incidentals():
    g = DepGraph(nodes=(
        _need("config:PYTHONPATH", NodeType.CONFIG, "PYTHONPATH", Layer.CONFIG,
              data={"config_value": "/app"}),
    ))
    out = render_build_script(g)
    assert "#@config-env" not in out


def test_config_env_marker_skips_value_containing_newline():
    # A multi-line value cannot round-trip through a single "#@config-env
    # VAR=value" comment line; the refusal guard must still hold now that the
    # value is read from `data["config_value"]` instead of `chosen_fix`.
    g = DepGraph(nodes=(
        _need("config:MULTILINE_VAR", NodeType.CONFIG, "MULTILINE_VAR", Layer.CONFIG,
              data={"config_value": "line1\nline2"}),
    ))
    out = render_build_script(g)
    assert "#@config-env" not in out


def test_need_stubs_with_config_env_marker_still_comment_only():
    # Regression-proof the exhaustive comment-only invariant from
    # test_need_stubs_are_comment_only above, now that CONFIG needs can carry
    # an extra `#@config-env` line.
    g = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _need("config:DJANGO_SETTINGS_MODULE", NodeType.CONFIG,
              "DJANGO_SETTINGS_MODULE", Layer.CONFIG,
              data={"config_value": "settings"}),
    ))
    out = render_build_script(g)
    lines = out.splitlines()
    first_need = next(i for i, ln in enumerate(lines) if ln.startswith("#@need "))
    for ln in lines[first_need:]:
        if ln.strip():
            assert ln.startswith("#"), f"non-comment line in #@need region: {ln!r}"


# ---------------------------------------------------------------------------
# Task 3 — SOFT requirements files (evidence.soft_requirements_files, carried
# on the PROJECT node's data) render as best-effort, closure-constrained,
# non-fatal installs — see build.py's _add_project_node + build_script.py.
# ---------------------------------------------------------------------------

_SOFT_HEADER = "# ---- Soft requirements (best-effort; may ADD packages, never MOVE a pinned one) ----"
_SOFT_CONSTRAINTS_PATH = "/tmp/v3_closure_constraints.txt"


def _project_with_soft(name="myproj", installable=True, soft=()):
    return Node(id=f"project:{name}", type=NodeType.PROJECT, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN,
                data={"installable": installable,
                      "soft_requirements_files": tuple(soft)})


def test_soft_requirements_render_constraints_and_installs():
    g = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _pkg("pkg:requests", "requests", "2.31.0"),
        _project_with_soft(soft=("worlds/aquaria/requirements.txt",
                                  "WebHostLib/requirements.txt")),
    ))
    out = render_build_script(g)
    assert f"cat > {_SOFT_CONSTRAINTS_PATH} <<'V3_EOF'" in out
    assert "psycopg2==2.9.9" in out
    assert "requests==2.31.0" in out
    assert "V3_EOF" in out
    assert ("python3 -m pip install --break-system-packages "
            f"-r worlds/aquaria/requirements.txt -c {_SOFT_CONSTRAINTS_PATH} "
            "|| true") in out
    assert ("python3 -m pip install --break-system-packages "
            f"-r WebHostLib/requirements.txt -c {_SOFT_CONSTRAINTS_PATH} "
            "|| true") in out


def test_soft_requirements_section_ordered_after_layers_before_project():
    g = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _project_with_soft(soft=("worlds/aquaria/requirements.txt",)),
    ))
    out = render_build_script(g)
    last_layer_idx = out.index("psycopg2==2.9.9")
    soft_idx = out.index(_SOFT_HEADER)
    project_idx = out.index("# ==================== PROJECT")
    assert last_layer_idx < soft_idx < project_idx


def test_no_soft_files_renders_nothing_and_stays_byte_identical():
    with_key = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _project_with_soft(soft=()),
    ))
    without_key = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _project(),  # no soft_requirements_files key at all — pre-existing shape
    ))
    for g in (with_key, without_key):
        out = render_build_script(g)
        assert _SOFT_HEADER not in out
        assert "v3_closure_constraints" not in out
    # byte-identical regardless of whether the (empty) key is present at all
    assert render_build_script(with_key) == render_build_script(without_key)


def test_soft_requirements_omit_unversioned_package():
    unversioned = Node(id="pkg:nover", type=NodeType.PACKAGE, name="nover",
                        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                        state=State.MISSING, version=None)
    g = DepGraph(nodes=(
        unversioned,
        _pkg("pkg:requests", "requests", "2.31.0"),
        _project_with_soft(soft=("worlds/x/requirements.txt",)),
    ))
    out = render_build_script(g)
    assert "requests==2.31.0" in out
    assert re.search(r"^nover\b", out, re.MULTILINE) is None  # never a bare "name=="


def test_soft_requirements_omit_unhonored_source_package():
    """Gate 1/2 hardening: a package whose real source is not the default
    PyPI registry (``data['uninstallable']`` -- see
    ``resolve_lock._missing_source_node``) must never be pinned at its REAL
    version in the soft-requirements constraints file -- an unrelated nested
    requirements.txt listing that same bare name could otherwise latch onto
    that pin and get installed from public PyPI through this constraints
    mechanism. Fix 2 (2026-07-14): it is not simply omitted anymore either --
    see test_excluded_package_gets_unsatisfiable_constraint_pin below for why
    a silent omission reopened the exact hole via the soft file's OWN
    unconstrained bare requirement."""
    unhonored = Node(
        id="pkg:hogli", type=NodeType.PACKAGE, name="hogli",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING, version="0.1.0",
        data={"uninstallable": True, "unhonored_source": True},
    )
    g = DepGraph(nodes=(
        unhonored,
        _pkg("pkg:requests", "requests", "2.31.0"),
        _project_with_soft(soft=("worlds/x/requirements.txt",)),
    ))
    out = render_build_script(g)
    assert "requests==2.31.0" in out
    # never pinned at its real version -- that would protect the real one
    assert "hogli==0.1.0" not in out


def test_soft_requirements_no_pinned_packages_still_emits_constraints_and_installs():
    g = DepGraph(nodes=(
        _project_with_soft(soft=("worlds/x/requirements.txt",)),
    ))
    out = render_build_script(g)
    assert f"cat > {_SOFT_CONSTRAINTS_PATH} <<'V3_EOF'" in out
    assert "V3_EOF" in out
    assert ("python3 -m pip install --break-system-packages "
            f"-r worlds/x/requirements.txt -c {_SOFT_CONSTRAINTS_PATH} || true") in out


def test_soft_requirements_paths_are_shell_quoted():
    g = DepGraph(nodes=(
        _project_with_soft(soft=("worlds/my world/requirements.txt",)),
    ))
    out = render_build_script(g)
    assert "'worlds/my world/requirements.txt'" in out


def test_soft_requirements_deterministic_and_sorted_regardless_of_input_order():
    g1 = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _project_with_soft(soft=("worlds/b/requirements.txt", "worlds/a/requirements.txt")),
    ))
    g2 = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _project_with_soft(soft=("worlds/a/requirements.txt", "worlds/b/requirements.txt")),
    ))
    out1 = render_build_script(g1)
    out2 = render_build_script(g2)
    assert out1 == out2
    assert out1.index("worlds/a/requirements.txt") < out1.index("worlds/b/requirements.txt")
    # pure: same graph in, same script out
    assert render_build_script(g1) == render_build_script(g1)


# ---------------------------------------------------------------------------
# Fix 2 (2026-07-14 post-measurement) — a soft requirements file must not
# launder an excluded package. `_pinned_constraint_lines` correctly omits
# `uninstallable` Package nodes (git/url/direct-reference/non-default-index
# sources -- see `_excluded_uv_source_node`/`_excluded_direct_reference_node`/
# `resolve_lock._missing_source_node`) from the *pinned* lines. But a nested
# soft requirements.txt (e.g. Archipelago's `worlds/sc2/requirements.txt`)
# naming that same bare dist name by coincidence was then COMPLETELY
# unconstrained -- `pip install -r` happily resolved the real public PyPI
# package of that name. The fix: every excluded name is ALSO emitted into the
# constraints file, pinned to a specifier that can never resolve on public
# PyPI, so pip fails loudly on that soft file instead of installing the wrong
# code. See build_script._EXCLUDED_CONSTRAINT_VERSION for the chosen specifier.
# ---------------------------------------------------------------------------

from graph.compile.build_script import _EXCLUDED_CONSTRAINT_VERSION


def _excluded_pkg(id_, name, version=None):
    return Node(id=id_, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                version=version, check_command=None,
                data={"uninstallable": True})


def test_excluded_package_gets_unsatisfiable_constraint_pin():
    g = DepGraph(nodes=(
        _excluded_pkg("pkg:kivymd", "kivymd"),  # git dep -- version=None
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _pkg("pkg:requests", "requests", "2.31.0"),
        _project_with_soft(soft=("worlds/sc2/requirements.txt",)),
    ))
    out = render_build_script(g)
    # normal pins for the honestly-resolved packages
    assert "psycopg2==2.9.9" in out
    assert "requests==2.31.0" in out
    # the excluded package IS in the constraints file now, but pinned
    # unsatisfiably -- never at a version that could match a real release
    excluded_line = f"kivymd=={_EXCLUDED_CONSTRAINT_VERSION}"
    assert excluded_line in out
    # and it must land inside the constraints heredoc, not as an install line
    open_line = f"cat > {_SOFT_CONSTRAINTS_PATH} <<'V3_EOF'"
    heredoc_start = out.index(open_line) + len(open_line)
    heredoc_end = out.index("V3_EOF", heredoc_start)
    assert heredoc_start < out.index(excluded_line) < heredoc_end
    assert re.search(r"pip install.*kivymd", out) is None


def test_excluded_package_with_real_version_also_gets_unsatisfiable_pin():
    """Fix 2 covers BOTH immunity shapes: version=None (uv-sources/direct-ref
    exclusion) and a real resolved version that is still non-default-source
    (resolve_lock._missing_source_node, e.g. `hogli` from PostHog). Either
    way the constraints file must protect against the bare name, never the
    node's own (possibly real, possibly None) version."""
    unhonored = Node(
        id="pkg:hogli", type=NodeType.PACKAGE, name="hogli",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING, version="0.1.0",
        data={"uninstallable": True, "unhonored_source": True},
    )
    g = DepGraph(nodes=(
        unhonored,
        _pkg("pkg:requests", "requests", "2.31.0"),
        _project_with_soft(soft=("worlds/x/requirements.txt",)),
    ))
    out = render_build_script(g)
    assert f"hogli=={_EXCLUDED_CONSTRAINT_VERSION}" in out
    assert "hogli==0.1.0" not in out


def test_no_exclusions_stays_byte_identical_to_pre_fix2_render():
    """No regression: a graph with zero `uninstallable` nodes renders the
    EXACT pre-Fix-2 constraints heredoc -- just the two `name==version`
    pins, nothing more (this is exactly
    test_soft_requirements_render_constraints_and_installs's graph)."""
    g = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _pkg("pkg:requests", "requests", "2.31.0"),
        _project_with_soft(soft=("worlds/aquaria/requirements.txt",
                                  "WebHostLib/requirements.txt")),
    ))
    out = render_build_script(g)
    assert "excluded" not in out
    open_line = f"cat > {_SOFT_CONSTRAINTS_PATH} <<'V3_EOF'"
    start = out.index(open_line)
    end = out.index("V3_EOF", start + len(open_line)) + len("V3_EOF")
    heredoc = out[start:end]
    assert heredoc == (
        f"cat > {_SOFT_CONSTRAINTS_PATH} <<'V3_EOF'\n"
        "psycopg2==2.9.9\n"
        "requests==2.31.0\n"
        "V3_EOF"
    )


def test_no_soft_files_no_constraints_even_with_exclusions():
    """No soft requirements files at all -> no constraints heredoc, full stop
    -- an excluded package with nothing to launder it must not conjure a
    constraints file into existence."""
    g = DepGraph(nodes=(
        _excluded_pkg("pkg:kivymd", "kivymd"),
        _pkg("pkg:requests", "requests", "2.31.0"),
        _project(),  # no soft_requirements_files
    ))
    out = render_build_script(g)
    assert _SOFT_HEADER not in out
    assert "v3_closure_constraints" not in out
    assert "kivymd" not in out


def test_unsatisfiable_specifier_is_valid_pep440():
    """The chosen specifier must be a VALID PEP 440 version -- pip should
    fail with "no matching distribution" (a resolution failure), never with
    "invalid requirement" (a parse failure), which could behave differently
    (e.g. abort the whole pip invocation before even reading the constraints
    file, defeating the other, real packages' protection in the same file)."""
    from packaging.version import Version
    from packaging.specifiers import SpecifierSet

    parsed = Version(_EXCLUDED_CONSTRAINT_VERSION)
    assert str(parsed) == _EXCLUDED_CONSTRAINT_VERSION
    # a local version segment is present -- PEP 440 forbids local versions on
    # public index uploads, so no genuine PyPI release can ever carry one.
    assert parsed.local is not None
    # and pip's own specifier parser accepts `name==<version>` built from it
    spec = SpecifierSet(f"=={_EXCLUDED_CONSTRAINT_VERSION}")
    assert parsed in spec


def test_excluded_constraint_lines_deterministic_and_sorted():
    g1 = DepGraph(nodes=(
        _excluded_pkg("pkg:zeta", "zeta"),
        _excluded_pkg("pkg:alpha", "alpha"),
        _project_with_soft(soft=("worlds/x/requirements.txt",)),
    ))
    out1 = render_build_script(g1)
    out2 = render_build_script(g1)
    assert out1 == out2
    assert out1.index(f"alpha=={_EXCLUDED_CONSTRAINT_VERSION}") < \
        out1.index(f"zeta=={_EXCLUDED_CONSTRAINT_VERSION}")
