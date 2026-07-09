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
from python_deps.depgraph.schema import DepGraph

import src.envstate.classify_services_clean as csc
from src.envstate.classify_services_clean import (
    classify_services_clean, make_construction_classifier)


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
