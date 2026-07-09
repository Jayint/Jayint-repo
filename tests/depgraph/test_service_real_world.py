"""Regression fixtures from the 50-repo corpus (see
.superpowers/sdd/service-schema-poc-findings.md)."""
import shutil
import textwrap
from pathlib import Path

from python_deps.depgraph.service_construct import build_service_nodes

FIXTURES = Path(__file__).parent / "fixtures" / "services"


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_rq_valkey_is_detected_with_zero_service_knowledge(tmp_path):
    """rq/rq has NO compose file. Its only signal is a CI workflow whose image
    tag is a matrix expression. No `kind`, no valkey->redis alias."""
    dest = tmp_path / ".github" / "workflows" / "valkey.yml"
    dest.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "rq_valkey.yml", dest)

    (node,) = build_service_nodes(str(tmp_path), owner="rq")
    assert node.id == "service:valkey"
    assert node.image_repo == "valkey/valkey"
    assert node.image_tag is None and "image_tag" in node.unresolved
    assert node.endpoint == "localhost:6379" and node.port_source == "ports"
    assert node.check.command == "valkey-cli ping"
    assert node.check.source == "declared_healthcheck"
    assert node.check.retries == "5" and node.check.interval_s == "10s"
    assert node.relevance == "ci_service"
    assert node.state == "certifiable_obligation"
    assert node.provenance[0].locator == "jobs.valkey-test.services.valkey"


def test_mlflow_style_tests_dir_compose_named_by_ci_is_kept(tmp_path):
    """A path heuristic would drop tests/db/compose.yml. CI names it, so it stays."""
    _write(tmp_path, "tests/db/compose.yml",
           "services:\n  postgres:\n    image: postgres:16\n    ports: ['5432:5432']\n")
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f tests/db/compose.yml up -d
              - run: pytest
    """)
    (node,) = build_service_nodes(str(tmp_path), owner="mlflow")
    assert node.name == "postgres"
    assert node.relevance == "ci_referenced_compose"


def test_testcontainers_style_fixture_compose_is_lowest_confidence(tmp_path):
    """The library's own compose fixtures are NOT its test environment."""
    _write(tmp_path, "tests/core/compose_fixtures/basic/docker-compose.yaml",
           "services:\n  alpine:\n    image: alpine:latest\n    ports: ['8080:80']\n")
    (node,) = build_service_nodes(str(tmp_path), owner="testcontainers")
    assert node.relevance == "unreferenced_compose"     # surfaced, but lowest confidence
