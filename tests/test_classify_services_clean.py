"""Task 9: evidence-only Service + Config classifier (NO live LLM / NO network).

``classify_services_clean`` reads the repo, builds one evidence-only ServiceNode per
declared backing service (via :func:`service_construct.build_service_nodes` -- no LLM,
no kind table), attaches a derived ``data['setup']`` compat view ONLY to certifiable
services, and admits the batch through the pure ``patch_gate``. A ``declared_unverifiable``
service is admitted and surfaced, but carries no ``setup`` (nothing for the host to run).

These tests never touch a model: construction is deterministic. ``test_construction_makes_no_llm_call``
passes a client that raises on any attribute access to prove the LLM is gone.
"""
import sys
import textwrap
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for _p in (str(_ROOT), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from graph.emit.build_script import render_build_script
from graph.emit.emit import _is_service_reciped
from graph.schema import DepGraph, NodeType, State
from graph.service_recipes import render_probe_poll

import src.envstate.classify_services_clean as csc
from src.envstate.classify_services_clean import (
    classify_services_clean, make_construction_classifier)


def _ci_only_repo(tmp_path, service_name="redis", image="redis:7"):
    """A repo whose ONLY service declaration is a GH Actions `jobs.<job>.services` block."""
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  test:\n    services:\n"
           f"      {service_name}:\n        image: {image}\n"
           "        ports:\n          - '6379:6379'\n")
    _write(tmp_path, "app.py", "import redis\nr = redis.Redis()\n")
    return str(tmp_path)


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_construction_makes_no_llm_call(tmp_path):
    """A client that raises proves construction never calls the model."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  clickhouse:\n    image: clickhouse/clickhouse-server:24\n"
           "    ports: ['8123:8123']\n")

    class Boom:
        def __getattr__(self, _n):
            raise AssertionError("construction must not call the LLM")

    graph = classify_services_clean(DepGraph(), str(tmp_path), client=Boom(), model="x")
    node = next(n for n in graph.nodes if n.id == "service:clickhouse")
    assert node.data["service"]["check"]["source"] == "tcp_port"


def test_certifiable_node_gets_a_compat_setup_view(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:16\n    ports: ['5432:5432']\n"
           "    healthcheck:\n      test: ['CMD', 'pg_isready']\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    node = next(n for n in graph.nodes if n.id == "service:db")
    assert node.data["setup"]["probe"] == "pg_isready"
    assert node.data["setup"]["install"] == [] and node.data["setup"]["start"] == ""
    assert _is_service_reciped(node)                    # certifiable -> reciped


def test_unverifiable_node_is_admitted_but_not_reciped(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:11-alpine\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    node = next(n for n in graph.nodes if n.id == "service:db")
    assert node.data["service"]["state"] == "declared_unverifiable"
    assert "setup" not in node.data                     # nothing for the host to run
    assert not _is_service_reciped(node)                # surfaced, never enforced


def test_config_dsn_repointed_into_setup_bind(tmp_path):
    """A DSN whose host is the declared service is repointed to loopback in setup['bind']."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    _write(tmp_path, ".env.example", "CACHE_URL=redis://cache:6379/0\n")
    _write(tmp_path, "app.py", "import os\nCACHE_URL = os.environ['CACHE_URL']\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    node = next(n for n in graph.nodes if n.id == "service:cache")
    assert "export CACHE_URL=redis://127.0.0.1:6379/0" in node.data["setup"]["bind"]


def test_never_crashes(tmp_path, monkeypatch):
    """Best-effort wrapper: a repo-read/collect error returns the input graph unchanged."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    monkeypatch.setattr(csc, "collect_static_evidence",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    g = DepGraph()
    out = classify_services_clean(g, str(tmp_path), client=None, model="m")
    assert out is g                                     # best-effort: error -> input graph


def test_make_construction_classifier_returns_callable(tmp_path):
    """The construction entrypoint returns a classify(graph, repo_path) closure that
    runs the deterministic (LLM-free) classifier."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    classify = make_construction_classifier(client=None, model="m")
    assert callable(classify)
    out = classify(DepGraph(), str(tmp_path))
    assert isinstance(out, DepGraph)
    assert out.get("service:cache") is not None         # wired through to the classifier


# ---------------------------------------------------------------------------
# C1 regression: scheme-agnostic DSN detection (was gated by _SCHEME_TO_KIND).
# ---------------------------------------------------------------------------
def test_exotic_scheme_dsn_repointed_into_bind(tmp_path):
    """A `clickhouse://` DSN — unknown to `service_from_url` — must still repoint,
    because `_dsn_configs` keeps any URL-with-a-host, not just known kinds."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  ch:\n    image: clickhouse/clickhouse-server:24\n"
           "    ports: ['9000:9000']\n")
    _write(tmp_path, ".env.example", "CLICKHOUSE_URL=clickhouse://user@ch:9000/db\n")
    _write(tmp_path, "app.py", "import os\nX = os.environ['CLICKHOUSE_URL']\n")
    out = classify_services_clean(DepGraph(), str(tmp_path))
    node = out.get("service:ch")
    assert node is not None
    assert ("export CLICKHOUSE_URL=clickhouse://user@127.0.0.1:9000/db"
            in node.data["setup"]["bind"])             # empty under the old scheme gate


# ---------------------------------------------------------------------------
# C2 regression: one malformed DSN must not delete the whole service tier.
# ---------------------------------------------------------------------------
def test_malformed_dsn_is_skipped_not_fatal(tmp_path):
    """A bad port in one `.env` value must NOT crash classify (a broad `except` would
    otherwise return the input graph and every service node would vanish). The service
    node is still built; the unparseable config is silently skipped, never repointed."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:16\n    ports: ['5432:5432']\n")
    _write(tmp_path, ".env.example", "BAD_URL=postgres://db:bad/app\n")
    _write(tmp_path, "app.py", "import os\nX = os.environ['BAD_URL']\n")
    out = classify_services_clean(DepGraph(), str(tmp_path))
    node = out.get("service:db")
    assert node is not None                              # the service tier survives
    assert node.data["setup"]["bind"] == []             # bad config skipped, not repointed


# ---------------------------------------------------------------------------
# C3 restored coverage (behaviour that outlived the translate_service path).
# ---------------------------------------------------------------------------
def test_admitted_service_contract(tmp_path):
    """The admitted-Service contract: SERVICE / MISSING, check_command derived from the
    probe, and NONE of the legacy kind keys on `data`."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    out = classify_services_clean(DepGraph(), str(tmp_path))
    node = out.get("service:cache")
    assert node is not None
    assert node.type is NodeType.SERVICE
    assert node.state is State.MISSING                  # host certify owns SATISFIED
    assert node.check_command == render_probe_poll(node.data["setup"]["probe"])
    assert "service_kind" not in node.data              # no kind, by design
    assert "service_params" not in node.data


def test_config_node_emitted(tmp_path):
    """CONFIG hint nodes are still emitted (one per env var the code reads)."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    _write(tmp_path, "app.py", "import os\nCACHE_URL = os.environ['CACHE_URL']\n")
    out = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = out.get("config:CACHE_URL")
    assert cfg is not None
    assert cfg.type is NodeType.CONFIG
    assert cfg.data.get("promotion") == "hint"          # advisory hint, never scheduled
    assert cfg.check_command is None


def test_ci_only_service_produces_reciped_service_node(tmp_path):
    """A service declared ONLY in a CI `services:` block still becomes a reciped node."""
    out = classify_services_clean(DepGraph(), _ci_only_repo(tmp_path))
    node = out.get("service:redis")
    assert node is not None
    assert node.type is NodeType.SERVICE
    setup = node.data.get("setup")
    assert setup is not None
    assert setup.get("probe")                            # non-empty probe (start is "" by design)
    assert _is_service_reciped(node)


def test_ci_and_compose_dedupe_produces_one_node(tmp_path):
    """compose + CI declarations of the same name fuse into ONE service node."""
    _write(tmp_path, "docker-compose.yml", "services:\n  redis:\n    image: redis:7\n")
    repo = _ci_only_repo(tmp_path)                       # adds the CI redis block too
    out = classify_services_clean(DepGraph(), repo)
    svc = [n for n in out.nodes if n.type is NodeType.SERVICE and n.name == "redis"]
    assert len(svc) == 1


def test_malformed_workflow_does_not_crash_classify(tmp_path):
    """A malformed workflow file must not sink the valid compose service."""
    _write(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres:16\n")
    _write(tmp_path, ".github/workflows/broken.yml",
           "jobs:\n  test:\n    services: [redis: image: redis:7\n")
    _write(tmp_path, "app.py", "import psycopg2\n")
    out = classify_services_clean(DepGraph(), str(tmp_path))
    assert isinstance(out, DepGraph)                     # never raises
    assert out.get("service:db") is not None             # valid compose service still admitted


# ---------------------------------------------------------------------------
# FIX B1 — the CONFIG value is now carried all the way to the rendered
# setup.sh as a `#@config-env VAR=value` marker. These go through the REAL
# production wiring end to end (classify_services_clean's own internal
# patch_gate.admit_proposal -> render_build_script), never a hand-built Node
# with a pre-set field — that bypass is exactly how this bug went undetected:
# every prior test built the Node directly instead of exercising construction.
# ---------------------------------------------------------------------------

def test_config_value_from_setdefault_reaches_rendered_config_env_marker(tmp_path):
    """First rung: a `os.environ.setdefault(VAR, 'literal')` default (the
    canonical Django ``manage.py`` idiom) is a real static value source.
    Before FIX B1 this NEVER rendered a marker -- `_config_nodes` minted the
    node from the var name only, and `_known_config_value` read a
    `chosen_fix` field nothing in production ever set."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE=myproj.settings" in out


def test_config_value_from_env_example_reaches_rendered_config_env_marker(tmp_path):
    """Second rung: `.env.example` is the value source when the var has no
    static Python default of its own. (FIX 3: `ENVIRONMENT` used to stand in
    here, but it is not settings-module-shaped -- swapped for an allowlisted
    var so this rung stays covered without regressing the FIX 3 allowlist.)"""
    _write(tmp_path, ".env.example", "FLASK_APP=myapp.wsgi\n")
    _write(tmp_path, "app.py", "import os\nos.environ['FLASK_APP']\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env FLASK_APP=myapp.wsgi" in out


def test_dsn_shaped_config_value_is_not_baked_as_config_env(tmp_path):
    """False-green guard: a DSN-shaped value already has a CERTIFIED bind path
    (`_dsn_configs` -> `render_bind_steps` -> `setup['bind']`). It must never
    ALSO be baked as an uncertified `#@config-env` ENV line -- a stale
    `.env.example` DSN would otherwise let the app import cleanly while
    silently pointing at the wrong host."""
    _write(tmp_path, ".env.example", "DATABASE_URL=postgres://localhost/db\n")
    _write(tmp_path, "app.py", "import os\nos.environ['DATABASE_URL']\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = graph.get("config:DATABASE_URL")
    assert cfg is not None and "config_value" not in cfg.data  # hint stays valueless
    out = render_build_script(graph)
    assert "#@config-env DATABASE_URL" not in out


def test_secret_named_config_value_still_not_baked_end_to_end(tmp_path):
    """The build_script secret-name denylist still holds through the real
    classify -> render pipeline, not just in an isolated unit test. (FIX 3:
    DJANGO_SECRET_KEY is now ALSO excluded by the allowlist -- the denylist is
    belt-and-braces, unreachable in normal operation, which is the point.)"""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SECRET_KEY', 'insecure-dev-key')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SECRET_KEY" not in out


def test_config_var_with_no_discoverable_value_stays_an_inert_hint(tmp_path):
    """A var read but never given a static default/`.env.example` value stays
    exactly as before construction: a nameless advisory `#@need` hint, never
    an `ENV VAR=` marker."""
    _write(tmp_path, "app.py", "import os\nos.environ['SOME_RUNTIME_ONLY_VAR']\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = graph.get("config:SOME_RUNTIME_ONLY_VAR")
    assert cfg is not None and "config_value" not in cfg.data
    out = render_build_script(graph)
    assert "#@config-env SOME_RUNTIME_ONLY_VAR" not in out


# ---------------------------------------------------------------------------
# FIX 3 — the DSN denylist over-fired (django-oauth-toolkit's real construction
# run baked POSTGRES_HOST/PORT/MYSQL_HOST/PORT alongside DJANGO_SETTINGS_MODULE;
# PostHog baked ~190 vars incl. PYTEST_CURRENT_TEST/HOSTNAME/TERM/SALT_KEY).
# Replaced with an allowlist: bake ONLY framework settings-module-shaped vars.
# These go through the same real classify -> render wiring as the B1 tests above.
# ---------------------------------------------------------------------------

def test_django_settings_module_still_baked_the_payoff(tmp_path):
    """The evidenced payoff must not regress under the new allowlist."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE=idp.settings" in out


def test_host_and_port_vars_are_never_baked_even_with_a_known_value(tmp_path):
    """FALSE-GREEN GUARD (the point of FIX 3): a bare host/port default is not
    DSN-shaped (no `://`), so it slipped past the old `_looks_like_dsn` filter
    and got baked as a Dockerfile ENV -- silently overriding the certified
    Service-tier binding (django-oauth-toolkit's real run baked
    POSTGRES_PORT=55432, a dev docker-compose port, which would have shadowed
    the provisioned Postgres on 5432). The allowlist must block ALL of these,
    asserted directly, regardless of whether a value is known."""
    _write(tmp_path, "settings.py", """
        import os
        POSTGRES_HOST = os.environ.get('POSTGRES_HOST', 'host.docker.internal')
        POSTGRES_PORT = os.environ.get('POSTGRES_PORT', '55432')
        MYSQL_HOST = os.environ.get('MYSQL_HOST', '127.0.0.1')
        MYSQL_PORT = os.environ.get('MYSQL_PORT', '53306')
    """)
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    for var in ("POSTGRES_HOST", "POSTGRES_PORT", "MYSQL_HOST", "MYSQL_PORT"):
        assert f"#@config-env {var}" not in out
        assert graph.get(f"config:{var}") is not None      # still an inert hint


def test_incidental_and_secret_shaped_vars_are_never_baked(tmp_path):
    """More real-run findings (PostHog): common shell incidentals and a
    secret-shaped name the OLD regex missed (`SALT_KEY` -- generic `*_KEY` was
    never in the denylist) must never bake even when a static default IS known."""
    _write(tmp_path, "settings.py", """
        import os
        PYTEST_CURRENT_TEST = os.environ.get('PYTEST_CURRENT_TEST', 'test_x (call)')
        HOSTNAME = os.environ.get('HOSTNAME', 'worker-1')
        TERM = os.environ.get('TERM', 'xterm')
        SALT_KEY = os.environ.get('SALT_KEY', 'dev-salt')
    """)
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    for var in ("PYTEST_CURRENT_TEST", "HOSTNAME", "TERM", "SALT_KEY"):
        assert f"#@config-env {var}" not in out


def test_conflicting_defaults_for_an_allowlisted_var_bakes_nothing(tmp_path):
    """AMBIGUOUS -> never pick a variant (same discipline as
    `depgraph.python.lanes.install.repair.choose_provider`'s AMBIGUOUS branch). Two files disagreeing
    on DJANGO_SETTINGS_MODULE's default must bake NEITHER value -- not whichever
    file the walk happened to visit first -- through the real wiring end to end."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'prod.settings')\n")
    _write(tmp_path, "tests/conftest.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'test.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE" not in out
    assert "#@need config:DJANGO_SETTINGS_MODULE" in out    # still surfaced as a hint


# ---------------------------------------------------------------------------
# REGRESSION (measured, django-oauth-toolkit): scan_env_defaults' code-scan
# picked up a VENDORED EXAMPLE APP bundled inside the test suite
# (tests/app/idp/manage.py) over the repo's real, AUTHORITATIVE test config
# (tox.ini's [testenv] setenv). The baked ENV then OVERRODE pytest-django's own
# correct settings resolution (env wins over ini), turning a working repo into
# `ImportError: No module named 'idp'`. Fix: authoritative config-file sources
# (tox.ini/pytest.ini/setup.cfg/pyproject.toml) are read FIRST and rank above
# both `.env.example` and the `.py` code scan; the code scan is a last-resort
# fallback that must never source a value from a vendored/example/fixture path.
# All exercised through the REAL wiring (classify_services_clean -> patch_gate
# -> render_build_script), never a hand-built Node.
# ---------------------------------------------------------------------------

def test_tox_ini_setenv_wins_over_vendored_example_app_the_exact_regression(tmp_path):
    """The django-oauth-toolkit layout, reproduced exactly: tox.ini's
    `[testenv] setenv` names the REAL settings module (`tests.settings`); a
    vendored example Django app bundled INSIDE the test suite
    (`tests/app/idp/manage.py`) sets a DIFFERENT value via the canonical
    `os.environ.setdefault(...)` idiom. The rendered script must bake
    `tests.settings` -- never `idp.settings`, the vendored fixture's value."""
    _write(tmp_path, "tox.ini", """
        [tox]
        envlist = py311

        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings
        commands = pytest
    """)
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE=tests.settings" in out
    assert "idp.settings" not in out


def test_pytest_ini_section_is_an_authoritative_source(tmp_path):
    """`[pytest]` in pytest.ini (pytest-django's own ini-option spelling) is
    read as an authoritative source, ranked above the `.py` code scan."""
    _write(tmp_path, "pytest.ini", """
        [pytest]
        DJANGO_SETTINGS_MODULE = tests.settings
    """)
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE=tests.settings" in out


def test_setup_cfg_tool_pytest_section_is_an_authoritative_source(tmp_path):
    """`[tool:pytest]` in setup.cfg is the setup.cfg spelling of the same
    pytest-django ini option and must be read as authoritative too."""
    _write(tmp_path, "setup.cfg", """
        [tool:pytest]
        DJANGO_SETTINGS_MODULE = tests.settings
    """)
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE=tests.settings" in out


def test_pyproject_ini_options_section_is_an_authoritative_source(tmp_path):
    """`[tool.pytest.ini_options]` in pyproject.toml is the modern pyproject
    spelling of the same pytest-django ini option; also authoritative."""
    _write(tmp_path, "pyproject.toml", """
        [tool.pytest.ini_options]
        DJANGO_SETTINGS_MODULE = "tests.settings"
    """)
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE=tests.settings" in out


def test_code_scan_fallback_still_works_with_no_authoritative_source(tmp_path):
    """No tox.ini/pytest.ini/setup.cfg/pyproject.toml at all -> the `.py` code
    scan is consulted as the last-resort fallback, exactly as before, for a
    FIRST-PARTY (non-vendored) file."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE=myproj.settings" in out


def test_code_scan_fallback_never_bakes_a_value_from_a_vendored_fixture_path(tmp_path):
    """No authoritative source names the var, so the code-scan fallback runs --
    but the ONLY value it finds lives under `tests/app/`, a vendored example
    app. That value must NEVER be baked; the var stays an inert hint."""
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = graph.get("config:DJANGO_SETTINGS_MODULE")
    assert cfg is not None and "config_value" not in cfg.data
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE" not in out
    assert "#@need config:DJANGO_SETTINGS_MODULE" in out


def test_code_scan_fallback_never_bakes_a_value_from_an_examples_directory(tmp_path):
    """Same guard, `examples/` layout: a value found only under an examples
    directory must not be baked by the last-resort code scan. (`examples/` is
    ALSO excluded from read-detection itself -- pre-existing, unrelated to this
    fix -- so unlike the `tests/app/` case the var gets no hint node at all;
    the invariant under test is simply "never baked".)"""
    _write(tmp_path, "examples/demo_app/settings.py",
           "import os\n"
           "os.environ.setdefault('FLASK_APP', 'demo_app.wsgi')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env FLASK_APP" not in out
    assert "demo_app.wsgi" not in out


def test_two_authoritative_sources_disagreeing_bakes_nothing(tmp_path):
    """tox.ini and pytest.ini both authoritatively name DJANGO_SETTINGS_MODULE
    but with DIFFERENT values -- real disagreement about the project's OWN
    declared config, not a vendored-fixture problem. That is ambiguity: bake
    NEITHER value, and do not silently fall through to a lower-ranked source
    either (there is none here, but the point is nothing is baked)."""
    _write(tmp_path, "tox.ini", """
        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tox.settings
    """)
    _write(tmp_path, "pytest.ini", """
        [pytest]
        DJANGO_SETTINGS_MODULE = pytest.settings
    """)
    _write(tmp_path, "app.py", "import os\nos.environ.get('DJANGO_SETTINGS_MODULE')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE" not in out
    assert "#@need config:DJANGO_SETTINGS_MODULE" in out


def test_authoritative_ambiguity_is_not_rescued_by_a_lower_ranked_source(tmp_path):
    """Belt-and-braces on the ambiguity rule: even when a THIRD, lower-ranked
    source (`.env.example`) agrees with one of the two disagreeing authoritative
    sources, the var must still not be baked -- authoritative disagreement is
    disqualifying on its own, never resolved by falling through."""
    _write(tmp_path, "tox.ini", """
        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tox.settings
    """)
    _write(tmp_path, "pytest.ini", """
        [pytest]
        DJANGO_SETTINGS_MODULE = pytest.settings
    """)
    _write(tmp_path, ".env.example", "DJANGO_SETTINGS_MODULE=tox.settings\n")
    _write(tmp_path, "app.py", "import os\nos.environ.get('DJANGO_SETTINGS_MODULE')\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    out = render_build_script(graph)
    assert "#@config-env DJANGO_SETTINGS_MODULE" not in out
