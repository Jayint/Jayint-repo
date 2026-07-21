"""Task 9 / Task 4: evidence-only Service + Config classifier (NO live LLM / NO network).

``classify_services_clean`` reads the repo, builds one evidence-only ServiceNode per
declared backing service (via :func:`service_construct.build_service_nodes` -- no LLM,
no kind table), attaches a derived ``data['setup']`` compat view ONLY to certifiable
services, and (Task 4) returns a :class:`RuntimePlan` instead of admitting Service/Config
nodes into the graph. SERVICE ``Node`` objects go in ``plan.service_obligations`` (still
built through the pure ``patch_gate.admit_proposal`` — the setup-shape / probe / evidence
validation is unchanged); the advisory Config tier goes in ``plan.config_obligations`` as
``(var, value, provenance, bake_eligible)`` records, consumed only by the render's
``#@config-env`` marker block.

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

from graph.compile.build_script import render_build_script
from graph.compile.emit import _is_service_reciped
from graph.model import DepGraph, NodeType, State
from graph.runtime_plan import RuntimePlan
from graph.python.services.service_recipes import render_probe_poll

import graph.python.classify_services_clean as csc
from graph.python.classify_services_clean import (
    classify_services_clean, make_construction_classifier)


def _render_markers(plan) -> str:
    """Render setup.sh from an EMPTY graph + the plan — the production wiring for the
    ``#@config-env`` marker block (classify -> RuntimePlan -> render_build_script)."""
    return render_build_script(DepGraph(), plan=plan)


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

    plan = classify_services_clean(DepGraph(), str(tmp_path), client=Boom(), model="x")
    node = plan.get_service("service:clickhouse")
    assert node is not None
    assert node.data["service"]["check"]["source"] == "tcp_port"


def test_certifiable_node_gets_a_compat_setup_view(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:16\n    ports: ['5432:5432']\n"
           "    healthcheck:\n      test: ['CMD', 'pg_isready']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    node = plan.get_service("service:db")
    assert node.data["setup"]["probe"] == "pg_isready"
    assert node.data["setup"]["install"] == [] and node.data["setup"]["start"] == ""
    assert _is_service_reciped(node)                    # certifiable -> reciped


def test_unverifiable_node_is_admitted_but_not_reciped(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:11-alpine\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    node = plan.get_service("service:db")
    assert node.data["service"]["state"] == "declared_unverifiable"
    assert "setup" not in node.data                     # nothing for the host to run
    assert not _is_service_reciped(node)                # surfaced, never enforced


def test_config_dsn_repointed_into_setup_bind(tmp_path):
    """A DSN whose host is the declared service is repointed to loopback in setup['bind']."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    _write(tmp_path, ".env.example", "CACHE_URL=redis://cache:6379/0\n")
    _write(tmp_path, "app.py", "import os\nCACHE_URL = os.environ['CACHE_URL']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    node = plan.get_service("service:cache")
    assert "export CACHE_URL=redis://127.0.0.1:6379/0" in node.data["setup"]["bind"]


def test_never_crashes(tmp_path, monkeypatch):
    """Best-effort wrapper: a repo-read/collect error returns an EMPTY plan."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    monkeypatch.setattr(csc, "collect_static_evidence",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = classify_services_clean(DepGraph(), str(tmp_path), client=None, model="m")
    assert isinstance(out, RuntimePlan) and out.is_empty()   # best-effort: error -> empty plan


def test_make_construction_classifier_returns_callable(tmp_path):
    """The construction entrypoint returns a classify(graph, repo_path) closure that
    runs the deterministic (LLM-free) classifier and yields a RuntimePlan."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    classify = make_construction_classifier(client=None, model="m")
    assert callable(classify)
    out = classify(DepGraph(), str(tmp_path))
    assert isinstance(out, RuntimePlan)
    assert out.get_service("service:cache") is not None    # wired through to the classifier


def test_classify_leaves_input_graph_unmodified(tmp_path):
    """Task 4 invariant: Service/Config no longer enter the constructed graph."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    g = DepGraph()
    classify_services_clean(g, str(tmp_path))
    assert g.nodes == ()                                   # untouched


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
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    node = plan.get_service("service:ch")
    assert node is not None
    assert ("export CLICKHOUSE_URL=clickhouse://user@127.0.0.1:9000/db"
            in node.data["setup"]["bind"])             # empty under the old scheme gate


# ---------------------------------------------------------------------------
# C2 regression: one malformed DSN must not delete the whole service tier.
# ---------------------------------------------------------------------------
def test_malformed_dsn_is_skipped_not_fatal(tmp_path):
    """A bad port in one `.env` value must NOT crash classify (a broad `except` would
    otherwise return an EMPTY plan and every service obligation would vanish). The
    service node is still built; the unparseable config is silently skipped."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:16\n    ports: ['5432:5432']\n")
    _write(tmp_path, ".env.example", "BAD_URL=postgres://db:bad/app\n")
    _write(tmp_path, "app.py", "import os\nX = os.environ['BAD_URL']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    node = plan.get_service("service:db")
    assert node is not None                              # the service tier survives
    assert node.data["setup"]["bind"] == []             # bad config skipped, not repointed


# ---------------------------------------------------------------------------
# C3 restored coverage (behaviour that outlived the translate_service path).
# ---------------------------------------------------------------------------
def test_admitted_service_contract(tmp_path):
    """The service-obligation contract: SERVICE / MISSING, check_command derived from
    the probe, and NONE of the legacy kind keys on `data`."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    node = plan.get_service("service:cache")
    assert node is not None
    assert node.type is NodeType.SERVICE
    assert node.state is State.MISSING                  # host certify owns SATISFIED
    assert node.check_command == render_probe_poll(node.data["setup"]["probe"])
    assert "service_kind" not in node.data              # no kind, by design
    assert "service_params" not in node.data


def test_config_obligation_emitted(tmp_path):
    """A Config obligation is emitted per env var the code reads (never scheduled)."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    _write(tmp_path, "app.py", "import os\nCACHE_URL = os.environ['CACHE_URL']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("CACHE_URL")
    assert cfg is not None
    assert cfg.var == "CACHE_URL"
    assert cfg.value is None                            # no discoverable value -> inert hint


def test_ci_only_service_produces_reciped_service_node(tmp_path):
    """A service declared ONLY in a CI `services:` block still becomes a reciped node."""
    plan = classify_services_clean(DepGraph(), _ci_only_repo(tmp_path))
    node = plan.get_service("service:redis")
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
    plan = classify_services_clean(DepGraph(), repo)
    svc = [n for n in plan.service_obligations if n.name == "redis"]
    assert len(svc) == 1


def test_malformed_workflow_does_not_crash_classify(tmp_path):
    """A malformed workflow file must not sink the valid compose service."""
    _write(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres:16\n")
    _write(tmp_path, ".github/workflows/broken.yml",
           "jobs:\n  test:\n    services: [redis: image: redis:7\n")
    _write(tmp_path, "app.py", "import psycopg2\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert isinstance(plan, RuntimePlan)                 # never raises
    assert plan.get_service("service:db") is not None    # valid compose service still built


# ---------------------------------------------------------------------------
# FIX B1 — the CONFIG value is carried to the rendered setup.sh as a
# `#@config-env VAR=value` marker (multi_docker_eval_adapter turns it into a
# Dockerfile ENV). These go through the REAL production wiring end to end
# (classify_services_clean -> RuntimePlan -> render_build_script(plan=...)).
# ---------------------------------------------------------------------------

def test_config_value_from_setdefault_reaches_rendered_config_env_marker(tmp_path):
    """First rung: a `os.environ.setdefault(VAR, 'literal')` default (the
    canonical Django ``manage.py`` idiom) is a real static value source."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE=myproj.settings" in _render_markers(plan)


def test_config_value_from_env_example_reaches_rendered_config_env_marker(tmp_path):
    """Second rung: `.env.example` is the value source when the var has no
    static Python default of its own."""
    _write(tmp_path, ".env.example", "FLASK_APP=myapp.wsgi\n")
    _write(tmp_path, "app.py", "import os\nos.environ['FLASK_APP']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env FLASK_APP=myapp.wsgi" in _render_markers(plan)


def test_dsn_shaped_config_value_is_not_baked_as_config_env(tmp_path):
    """False-green guard: a DSN-shaped value already has a CERTIFIED bind path
    (`_dsn_configs` -> `render_bind_steps` -> `setup['bind']`). It must never
    ALSO be baked as an uncertified `#@config-env` ENV line."""
    _write(tmp_path, ".env.example", "DATABASE_URL=postgres://localhost/db\n")
    _write(tmp_path, "app.py", "import os\nos.environ['DATABASE_URL']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("DATABASE_URL")
    assert cfg is not None and cfg.value is None          # DSN value withheld from bake
    assert "#@config-env DATABASE_URL" not in _render_markers(plan)


def test_secret_named_config_value_still_not_baked_end_to_end(tmp_path):
    """The secret-name denylist still holds through the real classify -> render
    pipeline (belt-and-braces behind the allowlist)."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SECRET_KEY', 'insecure-dev-key')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SECRET_KEY" not in _render_markers(plan)


def test_config_var_with_no_discoverable_value_stays_an_inert_hint(tmp_path):
    """A var read but never given a static default/`.env.example` value stays an
    inert obligation (value None), never an `ENV VAR=` marker."""
    _write(tmp_path, "app.py", "import os\nos.environ['SOME_RUNTIME_ONLY_VAR']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("SOME_RUNTIME_ONLY_VAR")
    assert cfg is not None and cfg.value is None
    assert "#@config-env SOME_RUNTIME_ONLY_VAR" not in _render_markers(plan)


# ---------------------------------------------------------------------------
# FIX 3 — bake ONLY framework settings-module-shaped vars (allowlist).
# ---------------------------------------------------------------------------

def test_django_settings_module_still_baked_the_payoff(tmp_path):
    """The evidenced payoff must not regress under the allowlist."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE=idp.settings" in _render_markers(plan)


def test_host_and_port_vars_are_never_baked_even_with_a_known_value(tmp_path):
    """FALSE-GREEN GUARD (the point of FIX 3): a bare host/port default is not
    DSN-shaped, so the allowlist (not `_looks_like_dsn`) must block it. The var
    stays an inert obligation."""
    _write(tmp_path, "settings.py", """
        import os
        POSTGRES_HOST = os.environ.get('POSTGRES_HOST', 'host.docker.internal')
        POSTGRES_PORT = os.environ.get('POSTGRES_PORT', '55432')
        MYSQL_HOST = os.environ.get('MYSQL_HOST', '127.0.0.1')
        MYSQL_PORT = os.environ.get('MYSQL_PORT', '53306')
    """)
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    out = _render_markers(plan)
    for var in ("POSTGRES_HOST", "POSTGRES_PORT", "MYSQL_HOST", "MYSQL_PORT"):
        assert f"#@config-env {var}" not in out
        assert plan.get_config(var) is not None            # still an inert obligation


def test_incidental_and_secret_shaped_vars_are_never_baked(tmp_path):
    """Common shell incidentals and a secret-shaped name the OLD regex missed
    (`SALT_KEY`) must never bake even when a static default IS known."""
    _write(tmp_path, "settings.py", """
        import os
        PYTEST_CURRENT_TEST = os.environ.get('PYTEST_CURRENT_TEST', 'test_x (call)')
        HOSTNAME = os.environ.get('HOSTNAME', 'worker-1')
        TERM = os.environ.get('TERM', 'xterm')
        SALT_KEY = os.environ.get('SALT_KEY', 'dev-salt')
    """)
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    out = _render_markers(plan)
    for var in ("PYTEST_CURRENT_TEST", "HOSTNAME", "TERM", "SALT_KEY"):
        assert f"#@config-env {var}" not in out


def test_conflicting_defaults_for_an_allowlisted_var_bakes_nothing(tmp_path):
    """AMBIGUOUS -> never pick a variant. Two files disagreeing on
    DJANGO_SETTINGS_MODULE's default must bake NEITHER value; the var is still
    surfaced as an inert obligation (value None)."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'prod.settings')\n")
    _write(tmp_path, "tests/conftest.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'test.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE" not in _render_markers(plan)
    cfg = plan.get_config("DJANGO_SETTINGS_MODULE")
    assert cfg is not None and cfg.value is None            # still surfaced, valueless


# ---------------------------------------------------------------------------
# REGRESSION (django-oauth-toolkit): authoritative config-file sources
# (tox.ini/pytest.ini/setup.cfg/pyproject.toml) rank above `.env.example` and
# the `.py` code scan; the code scan never sources a vendored/example path.
# All exercised through the REAL wiring (classify -> RuntimePlan -> render).
# ---------------------------------------------------------------------------

def test_tox_ini_setenv_wins_over_vendored_example_app_the_exact_regression(tmp_path):
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
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    out = _render_markers(plan)
    assert "#@config-env DJANGO_SETTINGS_MODULE=tests.settings" in out
    assert "idp.settings" not in out


def test_pytest_ini_section_is_an_authoritative_source(tmp_path):
    _write(tmp_path, "pytest.ini", """
        [pytest]
        DJANGO_SETTINGS_MODULE = tests.settings
    """)
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE=tests.settings" in _render_markers(plan)


def test_setup_cfg_tool_pytest_section_is_an_authoritative_source(tmp_path):
    _write(tmp_path, "setup.cfg", """
        [tool:pytest]
        DJANGO_SETTINGS_MODULE = tests.settings
    """)
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE=tests.settings" in _render_markers(plan)


def test_pyproject_ini_options_section_is_an_authoritative_source(tmp_path):
    _write(tmp_path, "pyproject.toml", """
        [tool.pytest.ini_options]
        DJANGO_SETTINGS_MODULE = "tests.settings"
    """)
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE=tests.settings" in _render_markers(plan)


def test_code_scan_fallback_still_works_with_no_authoritative_source(tmp_path):
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE=myproj.settings" in _render_markers(plan)


def test_code_scan_fallback_never_bakes_a_value_from_a_vendored_fixture_path(tmp_path):
    """A value found only under `tests/app/` (a vendored example app) must NEVER be
    baked; the var stays an inert obligation."""
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("DJANGO_SETTINGS_MODULE")
    assert cfg is not None and cfg.value is None
    assert "#@config-env DJANGO_SETTINGS_MODULE" not in _render_markers(plan)


def test_code_scan_fallback_never_bakes_a_value_from_an_examples_directory(tmp_path):
    """A value found only under an `examples/` directory must not be baked."""
    _write(tmp_path, "examples/demo_app/settings.py",
           "import os\n"
           "os.environ.setdefault('FLASK_APP', 'demo_app.wsgi')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    out = _render_markers(plan)
    assert "#@config-env FLASK_APP" not in out
    assert "demo_app.wsgi" not in out


def test_two_authoritative_sources_disagreeing_bakes_nothing(tmp_path):
    """tox.ini and pytest.ini both authoritatively name DJANGO_SETTINGS_MODULE with
    DIFFERENT values -- that is ambiguity: bake NEITHER value."""
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
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE" not in _render_markers(plan)
    cfg = plan.get_config("DJANGO_SETTINGS_MODULE")
    assert cfg is not None and cfg.value is None            # surfaced, valueless


def test_authoritative_ambiguity_is_not_rescued_by_a_lower_ranked_source(tmp_path):
    """Even when a THIRD, lower-ranked source (`.env.example`) agrees with one of the
    two disagreeing authoritative sources, the var must still not be baked."""
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
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE" not in _render_markers(plan)


# ---------------------------------------------------------------------------
# Task 3 — every discovered Config value carries structured provenance
# `{"rung": int, "source": str}`, threaded onto the obligation, and
# bake-eligibility is keyed on it (rung-3 SPLIT: 3a setdefault bakes, 3b
# get/getenv fallback is advisory-only).
# ---------------------------------------------------------------------------

def test_provenance_rung1_authoritative_config(tmp_path):
    _write(tmp_path, "tox.ini", """
        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings
    """)
    _write(tmp_path, "app.py", "import os\nos.environ.get('DJANGO_SETTINGS_MODULE')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("DJANGO_SETTINGS_MODULE")
    assert cfg.provenance == {"rung": 1, "source": "authoritative_config"}
    assert cfg.bake_eligible is True
    # Task 3 binding deliverable: the obligation anchors to the WINNING config file
    # (provenance.source only records the CATEGORY, not which of four files won).
    assert cfg.evidence == {"file": "tox.ini", "kind": "config_file"}


def test_provenance_rung2_env_example(tmp_path):
    _write(tmp_path, ".env.example", "FLASK_APP=myapp.wsgi\n")
    _write(tmp_path, "app.py", "import os\nos.environ['FLASK_APP']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("FLASK_APP")
    # rung-2 source is the ACTUAL .env.* file the value won from (B1 review #2).
    assert cfg.provenance == {"rung": 2, "source": ".env.example"}


def test_provenance_rung2_threads_actual_env_sample_file(tmp_path):
    """B1 review #2: a value won from `.env.sample` (not `.env.example`) records
    `.env.sample` as its provenance source AND anchors its evidence to the
    `.env.sample` row, not a mislabeled `.env.example`."""
    _write(tmp_path, ".env.sample", "FLASK_APP=sample.wsgi\n")
    _write(tmp_path, "app.py", "import os\nos.environ['FLASK_APP']\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("FLASK_APP")
    assert cfg.provenance == {"rung": 2, "source": ".env.sample"}
    assert cfg.evidence == {"file": ".env.sample", "kind": "env_var"}


def test_provenance_rung3a_setdefault(tmp_path):
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("DJANGO_SETTINGS_MODULE")
    assert cfg.provenance == {"rung": 3, "source": "code_scan_setdefault"}


def test_provenance_rung3b_fallback(tmp_path):
    _write(tmp_path, "settings.py",
           "import os\nMODE = os.environ.get('APP_SETTINGS', 'app.dev')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("APP_SETTINGS")
    assert cfg.provenance == {"rung": 3, "source": "code_scan_fallback"}


def test_rung3b_fallback_allowlisted_var_is_not_baked(tmp_path):
    """The rung-3 SPLIT: an ALLOWLISTED var whose ONLY value source is a rung-3b
    `os.environ.get` fallback must not emit a `#@config-env` marker."""
    _write(tmp_path, "settings.py",
           "import os\nMODE = os.environ.get('APP_SETTINGS', 'app.dev')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env APP_SETTINGS" not in _render_markers(plan)
    cfg = plan.get_config("APP_SETTINGS")                   # still surfaced as an obligation
    assert cfg is not None and cfg.bake_eligible is False


def test_rung3a_setdefault_allowlisted_var_still_bakes(tmp_path):
    """The rung-3 SPLIT, other side: a rung-3a `os.environ.setdefault` for an
    allowlisted var stays bake-eligible (the canonical Django payoff)."""
    _write(tmp_path, "manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    assert "#@config-env DJANGO_SETTINGS_MODULE=myproj.settings" in _render_markers(plan)


def test_tox_won_value_anchors_evidence_to_the_tox_file_not_the_code_read(tmp_path):
    """B1 residual (b): when tox.ini WINS the value over a vendored code-read site, the
    obligation's evidence anchors to the tox.ini config-file source, NOT the (vendored)
    code-read site that also mentions the var — the concrete WINNING file the plain
    provenance CATEGORY (`authoritative_config`) cannot carry."""
    _write(tmp_path, "tox.ini", """
        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings
    """)
    _write(tmp_path, "tests/app/idp/manage.py",
           "import os\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')\n")
    plan = classify_services_clean(DepGraph(), str(tmp_path))
    cfg = plan.get_config("DJANGO_SETTINGS_MODULE")
    assert cfg.value == "tests.settings"
    assert cfg.provenance == {"rung": 1, "source": "authoritative_config"}
    assert cfg.evidence == {"file": "tox.ini", "kind": "config_file"}
