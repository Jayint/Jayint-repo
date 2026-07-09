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

from python_deps.depgraph.emit import _is_service_reciped
from python_deps.depgraph.schema import DepGraph, NodeType, State
from python_deps.depgraph.service_recipes import render_probe_poll

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
