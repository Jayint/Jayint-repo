"""Gated SERVICE-node provisioning (V3_INCLUDE_SERVICES) — build-time install
lines in setup.sh + the separate runtime ENTRYPOINT start script.

Default OFF must be byte-identical to the pre-existing #@need-stub-only
rendering (SERVICE nodes are advisory-only, same as CONFIG). ON activates each
reciped SERVICE node's ``data['setup']['install']`` commands in setup.sh (build-
time-safe) and makes ``render_service_start_script`` available to bake the
runtime start/probe/createdb/post sequence into a Dockerfile ENTRYPOINT.

See .superpowers/sdd/service-inclusion-findings.md for the full design trace
and .superpowers/sdd/service-mechanism-report.md for this mechanism's report.
"""
from __future__ import annotations

from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from python_deps.depgraph.build_script import render_build_script, render_service_start_script
from python_deps.depgraph.service_recipes import render_probe_poll

# A realistic known-kind recipe dict, shaped exactly like
# service_recipes.render_setup("postgres", {"user": "app", "db": "app"}) /
# patch_gate.apply_proposal's data["setup"] (see findings §1).
_POSTGRES_SETUP = {
    "install": ["apt-get update",
                "DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql"],
    "start": "service postgresql start",
    "probe": "pg_isready",
    "createdb": "su postgres -c 'createdb -O app app'",
    "post": ["su postgres -c \"psql -c \\\"CREATE USER app PASSWORD 'app'\\\"\""],
    "bind": ["export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/app"],
}


def _service(id_="service:postgres", name="postgres", setup=None, **kw):
    setup = _POSTGRES_SETUP if setup is None else setup
    check_command = render_probe_poll(setup["probe"]) if setup.get("probe") else None
    return Node(id=id_, type=NodeType.SERVICE, name=name, layer=Layer.SERVICES,
                discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING,
                check_command=check_command, data={"setup": setup}, **kw)


def _pkg(id_, name, version):
    return Node(id=id_, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=version)


# ---------------------------------------------------------------------------
# render_service_start_script
# ---------------------------------------------------------------------------

def test_start_script_empty_graph_is_noop():
    assert render_service_start_script(None) == ""
    assert render_service_start_script(DepGraph()) == ""


def test_start_script_no_service_nodes_is_noop():
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"),))
    assert render_service_start_script(g) == ""


def test_start_script_service_with_no_setup_data_is_skipped():
    # A SERVICE node admitted with no data["setup"] should never happen post
    # patch_gate (§1), but the predicate must not crash or emit a bare id.
    g = DepGraph(nodes=(
        Node(id="service:mystery", type=NodeType.SERVICE, name="mystery",
             layer=Layer.SERVICES, discovered_by=DiscoveredBy.CLASSIFIER,
             state=State.MISSING),
    ))
    assert render_service_start_script(g) == ""


def test_start_script_postgres_emits_start_probe_createdb_post_and_execs():
    g = DepGraph(nodes=(_service(),))
    out = render_service_start_script(g)
    assert "service postgresql start" in out
    assert "for i in $(seq 1 15); do pg_isready && exit 0; sleep 2; done; exit 1" in out
    assert "createdb -O app app" in out
    assert "CREATE USER app" in out
    assert out.rstrip("\n").endswith('exec "$@"')
    # ordering: start -> probe -> post (createuser) -> createdb -> exec
    start_i = out.index("service postgresql start")
    probe_i = out.index("for i in $(seq 1 15);")
    post_i = out.index("CREATE USER app")
    createdb_i = out.index("createdb -O app app")
    exec_i = out.index('exec "$@"')
    assert start_i < probe_i < post_i < createdb_i < exec_i


def test_start_script_multiple_services_all_present():
    redis_setup = {"install": ["apt-get update",
                                "DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server"],
                   "start": "redis-server --daemonize yes", "probe": "redis-cli ping",
                   "createdb": None, "post": []}
    g = DepGraph(nodes=(_service(), _service("service:redis", "redis", setup=redis_setup)))
    out = render_service_start_script(g)
    assert "service postgresql start" in out
    assert "redis-server --daemonize yes" in out
    assert out.count('exec "$@"') == 1          # exec is the SCRIPT's tail, not per-node
    assert out.rstrip("\n").endswith('exec "$@"')


def test_start_script_is_deterministic():
    g1 = DepGraph(nodes=(_service("service:postgres", "postgres"),
                         _service("service:redis", "redis")))
    g2 = DepGraph(nodes=(_service("service:redis", "redis"),
                         _service("service:postgres", "postgres")))
    assert render_service_start_script(g1) == render_service_start_script(g1)  # pure
    # topo_order breaks ties by (layer rank, name) — insertion order shouldn't matter
    assert render_service_start_script(g1) == render_service_start_script(g2)


# ---------------------------------------------------------------------------
# render_build_script(include_services=...) — the setup.sh half
# ---------------------------------------------------------------------------

def test_flag_off_default_is_byte_identical_to_explicit_false():
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"), _service()))
    assert render_build_script(g) == render_build_script(g, include_services=False)


def test_flag_off_no_active_service_commands_in_setup_sh():
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"), _service()))
    out = render_build_script(g)
    # none of the service's install/start/createdb/post commands are active
    assert "DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql" not in out
    assert "service postgresql start" not in out
    assert "createdb -O app app" not in out
    assert "CREATE USER app" not in out
    # today's inert stub is unchanged
    assert "#@need service:postgres  state=missing" in out
    assert "#     (no command — propose a governed block to satisfy this)" in out
    assert "#@node service:postgres" not in out


def test_flag_off_matches_pre_service_work_golden_shape():
    # Regression pin: a graph with ONLY a service node renders identically to
    # the pre-existing test_need_stubs_are_comment_only expectations (no
    # non-comment line anywhere in the SERVICES section) when the flag is off.
    g = DepGraph(nodes=(_service(),))
    out = render_build_script(g)
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("#@need service:postgres"))
    for ln in lines[start:]:
        if ln.strip():
            assert ln.startswith("#"), f"non-comment line in #@need stub: {ln!r}"


def test_flag_on_install_lines_active_and_need_stub_suppressed():
    g = DepGraph(nodes=(_service(),))
    out = render_build_script(g, include_services=True)
    assert "apt-get update" in out
    assert "DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql" in out
    assert "#@node service:postgres" in out
    assert "#@need service:postgres" not in out
    # runtime-only commands must NEVER be baked into the build-time script
    assert "service postgresql start" not in out
    assert "createdb -O app app" not in out
    assert "CREATE USER app" not in out
    # the probe-poll check_command still renders (it always did, under #@need;
    # now it renders under #@node — same host-verifiable contract)
    assert "for i in $(seq 1 15); do pg_isready" in out


def test_flag_on_only_affects_services_with_setup_data():
    # A SERVICE node with no data["setup"] must still fall back to the #@need
    # stub even when the flag is on (never emitted as an install-active node).
    g = DepGraph(nodes=(
        Node(id="service:mystery", type=NodeType.SERVICE, name="mystery",
             layer=Layer.SERVICES, discovered_by=DiscoveredBy.CLASSIFIER,
             state=State.MISSING),
    ))
    out = render_build_script(g, include_services=True)
    assert "#@need service:mystery  state=missing" in out
    assert "#@node service:mystery" not in out


def test_flag_on_config_nodes_still_advisory_only():
    # CONFIG never carries a setup payload (findings §1) — unaffected either way.
    g = DepGraph(nodes=(
        Node(id="config:DATABASE_URL", type=NodeType.CONFIG, name="DATABASE_URL",
             layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN,
             state=State.MISSING),
        _service(),
    ))
    out = render_build_script(g, include_services=True)
    assert "#@need config:DATABASE_URL  state=missing" in out


def test_flag_on_manifest_counts_service_as_reciped_not_needs():
    g = DepGraph(nodes=(_service(),))
    out = render_build_script(g, include_services=True)
    preamble = out[:out.index("set -Eeuo pipefail")]
    assert "1 reciped (1 service) + 0 needs" in preamble
