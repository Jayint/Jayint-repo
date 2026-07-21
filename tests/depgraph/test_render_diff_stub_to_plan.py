"""Task 4 render-diff PROOF (brief item 6, review I3).

The migration deletes the CONFIG/SERVICE ``#@need`` stub tier and relocates the
``#@config-env`` markers into a dedicated block after the PIP section. Deleting the
stubs also removes the now-orphaned ``# ==================== CONFIG/SERVICES ====``
section headers and their blank lines, and drops the ``+ N needs`` manifest tally.

This test renders PRE (a faithful in-test reproduction of the OLD stub rendering, over
a graph carrying Config/Service nodes) and POST (the SAME content via the RuntimePlan)
in-process, then compares them against a COMPUTED expected diff:

    removed  == the full stub blocks + orphaned section headers/blank lines + the old
                manifest needs tally,
    added    == only the config-env block header + the new manifest line,
    markers  == byte-IDENTICAL in both (relocated, never rewritten), at the new
                position immediately after the PIP section.

The corpus INCLUDES a rung-1 fixture with a real marker (the existing 10-repo
render-fidelity corpus has zero markers and cannot exercise the relocation).
"""
from __future__ import annotations

from collections import Counter

from graph.model import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from graph.compile.build_script import (
    render_build_script, _CONFIG_ENV_HEADER, _section_header,
)
from graph.runtime_plan import ConfigObligation, RuntimePlan


# ── fixtures ─────────────────────────────────────────────────────────────────

def _pkg(id_, name, version):
    return Node(id=id_, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=version)


def _service(id_, name, probe="pg_isready"):
    # An advisory service (no setup) — under the DEFAULT (include_services=False) it
    # rendered ONLY a #@need stub in the old world and renders NOTHING now.
    return Node(id=id_, type=NodeType.SERVICE, name=name, layer=Layer.SERVICES,
                discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING,
                check_command=(f"for i in $(seq 1 15); do {probe} && exit 0; sleep 2; done; exit 1"
                               if probe else None))


# Each fixture: (deps_graph, config_obligations, service_nodes).
def _corpus():
    return {
        # rung-1 marker (REQUIRED by the brief — exercises the relocation)
        "rung1_marker": (
            DepGraph(nodes=(_pkg("pkg:django", "django", "5.0"),)),
            (ConfigObligation.create("DJANGO_SETTINGS_MODULE", "myproj.settings",
                                     {"rung": 1, "source": "authoritative_config"}),
             ConfigObligation.create("DATABASE_URL", None, None)),   # valueless hint
            (_service("service:postgres", "postgres"),),
        ),
        # rung-2 marker + a service with no probe (no #@check line)
        "rung2_marker": (
            DepGraph(nodes=(_pkg("pkg:flask", "flask", "3.0"),
                            _pkg("pkg:redis", "redis", "5.0"))),
            (ConfigObligation.create("FLASK_APP", "app.wsgi",
                                     {"rung": 2, "source": ".env.example"}),),
            (_service("service:redis", "redis", probe=None),),
        ),
        # no marker at all (bakes nothing) — pure stub removal
        "no_marker": (
            DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"),)),
            (ConfigObligation.create("SOME_VAR", None, None),),
            (_service("service:mysql", "mysql"),),
        ),
    }


# ── faithful reproduction of the DELETED old stub rendering ──────────────────

def _old_need_block(node: Node) -> list[str]:
    """Reproduces the deleted ``build_script._need_block`` for a bare CONFIG/SERVICE
    node (no requires/evidence in these fixtures)."""
    out = ["#", f"#@need {node.id}  state={node.state.value}"]
    if node.check_command:
        out.append(f"#@check {node.check_command}")
    out.append("#     (no command — propose a governed block to satisfy this)")
    return out


def _old_config_marker(ob: ConfigObligation) -> str | None:
    from graph.compile.build_script import _config_env_marker
    return _config_env_marker(ob)


def _render_old(deps_graph, configs, services) -> str:
    """The OLD output: the NEW deps render, then re-inserted with (a) the old manifest
    needs tally, and (b) the CONFIG section (stubs, markers inside) + SERVICES section
    (stubs) that the deleted renderer appended after the dep layers."""
    new = render_build_script(deps_graph)          # deps sections + NEW manifest
    lines = new.rstrip("\n").split("\n")

    # (a) old manifest: re-add "+ N needs (need_str)".
    n_needs = len(configs) + len(services)
    parts = []
    if services:
        parts.append(f"{len(services)} service")
    if configs:
        parts.append(f"{len(configs)} config")
    need_str = ", ".join(parts)
    for i, ln in enumerate(lines):
        if ln.startswith("#   nodes: "):
            lines[i] = f"{ln} + {n_needs} needs ({need_str})"
            break

    # (b) CONFIG section then SERVICES section (old layer order: config before services).
    if configs:
        lines.append("")
        lines.append(_section_header(Layer.CONFIG))
        for ob in sorted(configs, key=lambda c: f"config:{c.var}"):
            block = _old_need_block(_cfg_node(ob))
            marker = _old_config_marker(ob)
            if marker:
                block.append(marker)
            lines.extend(block)
    if services:
        lines.append("")
        lines.append(_section_header(Layer.SERVICES))
        for svc in sorted(services, key=lambda n: n.id):
            lines.extend(_old_need_block(svc))
    return "\n".join(lines) + "\n"


def _cfg_node(ob: ConfigObligation) -> Node:
    return Node(id=f"config:{ob.var}", type=NodeType.CONFIG, name=ob.var, layer=Layer.CONFIG,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING)


# ── the proof ────────────────────────────────────────────────────────────────

def _config_env_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("#@config-env")]


def test_stub_to_plan_render_diff_is_exactly_the_computed_diff():
    for name, (deps_graph, configs, services) in _corpus().items():
        plan = RuntimePlan(config_obligations=configs, service_obligations=services)
        pre = _render_old(deps_graph, configs, services)
        post = render_build_script(deps_graph, plan=plan)   # include_services=False

        pre_c, post_c = Counter(pre.splitlines()), Counter(post.splitlines())
        removed = pre_c - post_c                              # in old, gone in new
        added = post_c - pre_c                                # new-only

        # 1. Every REMOVED line is a stub/header/blank/manifest comment — NEVER a dep
        #    install line. (No real dependency was dropped by the migration.)
        for ln in removed:
            assert ln == "" or ln.startswith("#"), f"[{name}] non-comment removed: {ln!r}"
        for dep in deps_graph.nodes:
            spec = f"{dep.name}=={dep.version}"
            assert spec in post and spec in pre, f"[{name}] dep {spec} must survive"

        # 2. The removed set is EXACTLY the old stub blocks + orphaned headers + the
        #    old manifest line (computed independently here).
        expected_removed: Counter = Counter()
        # config stubs (markers are NOT removed — they relocate, see #4)
        for ob in configs:
            expected_removed.update(_old_need_block(_cfg_node(ob)))
        # service stubs
        for svc in services:
            expected_removed.update(_old_need_block(svc))
        # orphaned section headers
        expected_removed.update([_section_header(Layer.CONFIG),
                                 _section_header(Layer.SERVICES)])
        # the two section-separator blank lines that headed CONFIG + SERVICES; the
        # NEW render still emits ONE leading blank (for the config-env block) whenever a
        # marker bakes, so the NET removed blanks = 2 - (1 if a marker bakes else 0).
        bakes = any(_old_config_marker(ob) for ob in configs)
        expected_removed[""] += 2 - (1 if bakes else 0)
        # the old manifest tally line
        old_manifest = next(ln for ln in pre.splitlines() if ln.startswith("#   nodes: "))
        expected_removed[old_manifest] += 1
        assert removed == expected_removed, (
            f"[{name}] removed diff mismatch:\n  got={dict(removed)}\n"
            f"  expected={dict(expected_removed)}")

        # 3. The ADDED set is ONLY the config-env block header + the new manifest line
        #    (markers are byte-preserved, so they are in NEITHER added nor removed).
        expected_added: Counter = Counter()
        new_manifest = next(ln for ln in post.splitlines() if ln.startswith("#   nodes: "))
        expected_added[new_manifest] += 1
        if bakes:
            expected_added[_CONFIG_ENV_HEADER] += 1
        assert added == expected_added, (
            f"[{name}] added diff mismatch:\n  got={dict(added)}\n"
            f"  expected={dict(expected_added)}")

        # 4. #@config-env markers are byte-IDENTICAL across pre/post (relocated, never
        #    rewritten) and sit immediately after the PIP section in POST.
        assert _config_env_lines(pre) == _config_env_lines(post), f"[{name}] markers changed"
        if bakes:
            lines = post.splitlines()
            pip_i = lines.index("# ==================== PIP ====================")
            hdr_i = lines.index(_CONFIG_ENV_HEADER)
            marker_i = next(i for i, ln in enumerate(lines) if ln.startswith("#@config-env"))
            assert pip_i < hdr_i < marker_i, f"[{name}] marker block not right after PIP"
            # nothing but the pip section body sits between PIP header and the block
            assert not any(ln.startswith("# ====") for ln in lines[pip_i + 1:hdr_i]), name


def test_corpus_has_a_rung1_marker_fixture():
    # Guard: the relocation is only exercised when at least one fixture actually bakes
    # a marker (the brief's requirement — the old corpus had none).
    _, configs, _ = _corpus()["rung1_marker"]
    assert any(_old_config_marker(ob) for ob in configs)
