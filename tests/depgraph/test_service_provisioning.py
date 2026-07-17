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

import subprocess
import tempfile
from pathlib import Path

from graph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from graph.emit.build_script import render_build_script, render_service_start_script
from graph.service_recipes import render_probe_poll

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
    # Bounded wait loop that does NOT exit the script (see the shell-execution
    # tests below) — deliberately NOT service_recipes.render_probe_poll's
    # `exit 0`/`exit 1` wrapper, which would kill v3_start_services.sh dead
    # the instant the first service's probe succeeds.
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
# Shell-execution — LOAD-BEARING: proves the rendered script actually reaches
# `exec "$@"` past a succeeding probe. Regression test for the bug where
# service_recipes.render_probe_poll's `exit 0` (safe as the tail of a
# throwaway check script) terminated the WHOLE v3_start_services.sh the
# instant the first probe succeeded — later services' post/createdb and the
# final `exec "$@"` never ran, so `docker run -d <image> tail -f /dev/null`
# exited immediately and the container was dead before any `docker exec`.
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
    g = DepGraph(nodes=(_true_probe_service(),))
    script = render_service_start_script(g)
    proc = _run_start_script(script, "echo", "__REACHED_EXEC__")
    assert "__REACHED_EXEC__" in proc.stdout, (
        f'start script did not reach exec "$@": rc={proc.returncode} '
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}\nscript:\n{script}"
    )


def test_start_script_two_services_first_probe_success_does_not_short_circuit_second(tmp_path):
    """The FIRST service's probe succeeding must not skip the SECOND service's
    setup — each service's block (start/probe/post/createdb) must run to
    completion before the next one starts, and exec "$@" must still be the
    final thing reached."""
    marker_post = tmp_path / "second_post_ran"
    marker_createdb = tmp_path / "second_createdb_ran"
    second_setup = {
        "install": [], "start": ":", "probe": "true",
        "createdb": f"touch {marker_createdb}",
        "post": [f"touch {marker_post}"],
    }
    g = DepGraph(nodes=(
        _true_probe_service("service:first", "first"),
        Node(id="service:second", type=NodeType.SERVICE, name="second",
             layer=Layer.SERVICES, discovered_by=DiscoveredBy.CLASSIFIER,
             state=State.MISSING, data={"setup": second_setup}),
    ))
    script = render_service_start_script(g)
    proc = _run_start_script(
        script, "bash", "-c",
        f"test -f {marker_post} && echo POST_RAN; "
        f"test -f {marker_createdb} && echo CREATEDB_RAN; "
        "echo __REACHED_EXEC__",
    )
    assert "POST_RAN" in proc.stdout, f"second service's post never ran: {proc}"
    assert "CREATEDB_RAN" in proc.stdout, f"second service's createdb never ran: {proc}"
    assert "__REACHED_EXEC__" in proc.stdout, f'exec "$@" never reached: {proc}'


# ---------------------------------------------------------------------------
# Coercion — `post` (and the other list-valued setup fields) may arrive as a
# bare string instead of a list; naive `for cmd in post` iteration over a
# string explodes it character-by-character (list("mc mb x") == ['m', 'c',
# ' ', 'm', 'b', ...]).
# ---------------------------------------------------------------------------

def test_start_script_coerces_string_post_into_one_command_line():
    setup = {"install": [], "start": ":", "probe": "true",
             "createdb": None, "post": "createbucket foo"}
    g = DepGraph(nodes=(
        Node(id="service:strpost", type=NodeType.SERVICE, name="strpost",
             layer=Layer.SERVICES, discovered_by=DiscoveredBy.CLASSIFIER,
             state=State.MISSING, data={"setup": setup}),
    ))
    out = render_service_start_script(g)
    lines = out.splitlines()
    assert any(ln.strip() == "createbucket foo" for ln in lines), (
        f"expected 'createbucket foo' as one literal command line, got:\n{out}"
    )
    # no one-char-per-line artifact from character-splitting the string
    exploded_chars = set("createbucto fg")
    assert not any(ln.strip() and ln.strip() in exploded_chars for ln in lines)


# ---------------------------------------------------------------------------
# render_build_script(include_services=...) — the setup.sh half
# ---------------------------------------------------------------------------

def test_flag_off_default_is_byte_identical_to_explicit_false():
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"), _service()))
    assert render_build_script(g) == render_build_script(g, include_services=False)


def test_flag_off_default_contains_none_of_the_service_commands_and_matches_explicit_false():
    # Strengthened byte-identity-off check, combined into one assertion: the
    # default call (no `include_services` kwarg at all) must both equal the
    # explicit-False call AND contain none of the service's install/start
    # command strings, for a graph that DOES carry a service node.
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"), _service()))
    default_out = render_build_script(g)
    assert default_out == render_build_script(g, include_services=False)
    for cmd in _POSTGRES_SETUP["install"]:
        assert cmd not in default_out
    assert _POSTGRES_SETUP["start"] not in default_out


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
