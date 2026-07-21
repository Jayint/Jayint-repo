"""Gated SERVICE provisioning (V3_INCLUDE_SERVICES) — build-time install lines in
setup.sh + the separate runtime ENTRYPOINT start script.

Task 4 — services are the construction artifact (:class:`RuntimePlan`) now:
``render_service_start_script`` reads the plan, and ``render_build_script`` locally
admits the plan's services (the SAME ``with_node`` idempotency the v3 loop uses) so the
existing graph-based service-render machinery still runs. A graph that already carries a
SERVICE node (e.g. the loop-final graph after admission) renders it too. The Config/Service
``#@need`` stub tier is DELETED.

Default OFF: services never render into the build-time script.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from graph.model import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from graph.compile.build_script import render_build_script, render_service_start_script
from graph.runtime_plan import RuntimePlan
from graph.python.services.service_recipes import render_probe_poll

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


def _plan(*services):
    return RuntimePlan(service_obligations=tuple(services))


def _pkg(id_, name, version):
    return Node(id=id_, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=version)


# ---------------------------------------------------------------------------
# render_service_start_script — now reads the RuntimePlan
# ---------------------------------------------------------------------------

def test_start_script_empty_plan_is_noop():
    assert render_service_start_script(None) == ""
    assert render_service_start_script(RuntimePlan()) == ""


def test_start_script_no_service_obligations_is_noop():
    assert render_service_start_script(RuntimePlan(service_obligations=())) == ""


def test_start_script_service_with_no_setup_data_is_skipped():
    # A SERVICE obligation with no data["setup"] should never happen post patch_gate
    # (§1), but the predicate must not crash or emit a bare id.
    plan = _plan(Node(id="service:mystery", type=NodeType.SERVICE, name="mystery",
                      layer=Layer.SERVICES, discovered_by=DiscoveredBy.CLASSIFIER,
                      state=State.MISSING))
    assert render_service_start_script(plan) == ""


def test_start_script_postgres_emits_start_probe_createdb_post_and_execs():
    out = render_service_start_script(_plan(_service()))
    assert "service postgresql start" in out
    # Bounded wait loop that does NOT exit the script — deliberately NOT
    # service_recipes.render_probe_poll's `exit 0`/`exit 1` wrapper.
    assert "for _i in $(seq 1 30); do pg_isready && break; sleep 1; done" in out
    assert "createdb -O app app" in out
    assert "CREATE USER app" in out
    assert out.rstrip("\n").endswith('exec "$@"')
    assert "exit 0" not in out
    assert "exit 1" not in out
    # ordering: start -> probe -> post (createuser) -> createdb -> exec
    start_i = out.index("service postgresql start")
    probe_i = out.index("for _i in $(seq 1 30);")
    post_i = out.index("CREATE USER app")
    createdb_i = out.index("createdb -O app app")
    exec_i = out.index('exec "$@"')
    assert start_i < probe_i < post_i < createdb_i < exec_i


def test_start_script_multiple_services_all_present():
    redis_setup = {"install": ["apt-get update",
                                "DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server"],
                   "start": "redis-server --daemonize yes", "probe": "redis-cli ping",
                   "createdb": None, "post": []}
    out = render_service_start_script(
        _plan(_service(), _service("service:redis", "redis", setup=redis_setup)))
    assert "service postgresql start" in out
    assert "redis-server --daemonize yes" in out
    assert out.count('exec "$@"') == 1          # exec is the SCRIPT's tail, not per-node
    assert out.rstrip("\n").endswith('exec "$@"')


def test_start_script_is_deterministic():
    p1 = _plan(_service("service:postgres", "postgres"), _service("service:redis", "redis"))
    p2 = _plan(_service("service:redis", "redis"), _service("service:postgres", "postgres"))
    assert render_service_start_script(p1) == render_service_start_script(p1)  # pure
    # topo_order breaks ties by (layer rank, name) — insertion order shouldn't matter
    assert render_service_start_script(p1) == render_service_start_script(p2)


# ---------------------------------------------------------------------------
# Shell-execution — LOAD-BEARING: proves the rendered script reaches `exec "$@"`
# past a succeeding probe (regression for the render_probe_poll `exit 0` bug).
# Requires a real `bash` on PATH.
# ---------------------------------------------------------------------------

def _true_probe_service(id_="service:fake", name="fake"):
    setup = {"install": [], "start": ":", "probe": "true", "createdb": None, "post": []}
    return Node(id=id_, type=NodeType.SERVICE, name=name, layer=Layer.SERVICES,
                discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING,
                data={"setup": setup})


def _run_start_script(script_text: str, *args: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "v3_start_services.sh"
        script_path.write_text(script_text)
        return subprocess.run(
            ["bash", str(script_path), *args],
            capture_output=True, text=True, timeout=30,
        )


def test_start_script_reaches_exec_past_a_succeeding_probe():
    script = render_service_start_script(_plan(_true_probe_service()))
    proc = _run_start_script(script, "echo", "__REACHED_EXEC__")
    assert "__REACHED_EXEC__" in proc.stdout, (
        f'start script did not reach exec "$@": rc={proc.returncode} '
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}\nscript:\n{script}"
    )


def test_start_script_two_services_first_probe_success_does_not_short_circuit_second(tmp_path):
    marker_post = tmp_path / "second_post_ran"
    marker_createdb = tmp_path / "second_createdb_ran"
    second_setup = {
        "install": [], "start": ":", "probe": "true",
        "createdb": f"touch {marker_createdb}",
        "post": [f"touch {marker_post}"],
    }
    plan = _plan(
        _true_probe_service("service:first", "first"),
        Node(id="service:second", type=NodeType.SERVICE, name="second",
             layer=Layer.SERVICES, discovered_by=DiscoveredBy.CLASSIFIER,
             state=State.MISSING, data={"setup": second_setup}),
    )
    script = render_service_start_script(plan)
    proc = _run_start_script(
        script, "bash", "-c",
        f"test -f {marker_post} && echo POST_RAN; "
        f"test -f {marker_createdb} && echo CREATEDB_RAN; "
        "echo __REACHED_EXEC__",
    )
    assert "POST_RAN" in proc.stdout, f"second service's post never ran: {proc}"
    assert "CREATEDB_RAN" in proc.stdout, f"second service's createdb never ran: {proc}"
    assert "__REACHED_EXEC__" in proc.stdout, f'exec "$@" never reached: {proc}'


def test_start_script_coerces_string_post_into_one_command_line():
    setup = {"install": [], "start": ":", "probe": "true",
             "createdb": None, "post": "createbucket foo"}
    plan = _plan(Node(id="service:strpost", type=NodeType.SERVICE, name="strpost",
                      layer=Layer.SERVICES, discovered_by=DiscoveredBy.CLASSIFIER,
                      state=State.MISSING, data={"setup": setup}))
    out = render_service_start_script(plan)
    lines = out.splitlines()
    assert any(ln.strip() == "createbucket foo" for ln in lines), (
        f"expected 'createbucket foo' as one literal command line, got:\n{out}"
    )
    exploded_chars = set("createbucto fg")
    assert not any(ln.strip() and ln.strip() in exploded_chars for ln in lines)


# ---------------------------------------------------------------------------
# render_build_script(include_services=...) — the setup.sh half. A SERVICE node
# already in the graph (loop-admitted) still renders; the plan path is exercised
# separately below.
# ---------------------------------------------------------------------------

def test_flag_off_default_is_byte_identical_to_explicit_false():
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"), _service()))
    assert render_build_script(g) == render_build_script(g, include_services=False)


def test_flag_off_default_contains_none_of_the_service_commands_and_matches_explicit_false():
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"), _service()))
    default_out = render_build_script(g)
    assert default_out == render_build_script(g, include_services=False)
    for cmd in _POSTGRES_SETUP["install"]:
        assert cmd not in default_out
    assert _POSTGRES_SETUP["start"] not in default_out


def test_flag_off_no_active_service_commands_and_no_stub():
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"), _service()))
    out = render_build_script(g)
    # none of the service's install/start/createdb/post commands are active
    assert "DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql" not in out
    assert "service postgresql start" not in out
    assert "createdb -O app app" not in out
    assert "CREATE USER app" not in out
    # Task 4: NO #@need stub either (deleted); no #@node for an inactive service
    assert "#@need service:postgres" not in out
    assert "#@node service:postgres" not in out
    assert "# ==================== SERVICES ====================" not in out


def test_flag_off_service_only_graph_renders_no_services_section():
    # A graph with ONLY a service node renders no SERVICES section when the flag is off.
    g = DepGraph(nodes=(_service(),))
    out = render_build_script(g)
    assert "# ==================== SERVICES ====================" not in out
    assert "#@node service:postgres" not in out


def test_flag_on_install_lines_active_and_no_stub_from_graph_service():
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
    # the probe-poll check_command still renders (now under #@node)
    assert "for i in $(seq 1 15); do pg_isready" in out


def test_flag_on_install_lines_active_from_plan_admission():
    # Task 4: the plan path — services come from the RuntimePlan, locally admitted.
    out = render_build_script(DepGraph(), plan=_plan(_service()), include_services=True)
    assert "DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql" in out
    assert "#@node service:postgres" in out
    assert "service postgresql start" not in out         # runtime-only, never here


def test_flag_on_only_affects_services_with_setup_data():
    # A SERVICE node with no data["setup"] renders nothing (no stub, no #@node).
    g = DepGraph(nodes=(
        Node(id="service:mystery", type=NodeType.SERVICE, name="mystery",
             layer=Layer.SERVICES, discovered_by=DiscoveredBy.CLASSIFIER,
             state=State.MISSING),
    ))
    out = render_build_script(g, include_services=True)
    assert "#@need service:mystery" not in out
    assert "#@node service:mystery" not in out


def test_flag_on_config_graph_node_renders_nothing():
    # Task 4: a CONFIG graph node is not part of the plan/marker path and renders
    # nothing (Config markers come from plan.config_obligations, not graph nodes).
    g = DepGraph(nodes=(
        Node(id="config:DATABASE_URL", type=NodeType.CONFIG, name="DATABASE_URL",
             layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN,
             state=State.MISSING),
        _service(),
    ))
    out = render_build_script(g, include_services=True)
    assert "#@need config:DATABASE_URL" not in out
    assert "config:DATABASE_URL" not in out


def test_flag_on_manifest_counts_service_as_reciped():
    g = DepGraph(nodes=(_service(),))
    out = render_build_script(g, include_services=True)
    preamble = out[:out.index("set -Eeuo pipefail")]
    assert "1 reciped (1 service)" in preamble
    assert "needs" not in preamble                        # Task 4: no needs tally
