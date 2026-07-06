"""Hand ground-truth labels for Level-3 real-repo scoring.

Labeled from direct inspection of each repo's docker-compose.yml / CI / test files
(provenance in `source`), independent of the module's output. Extend by adding entries
keyed on the checkout directory name.

Honesty note: the three seeded repos are the research's *service-tagged-but-mocked*
cases, so `services_truly_required` is empty for all three. This set therefore probes
PRECISION and the guard #2 mock-downgrade CALIBRATION — not recall. A recall probe needs
repos whose tests genuinely dial a live service (add them here as you label them).

Label schema (all lists are service kinds: postgres/mysql/redis/mongo/rabbitmq/broker/elasticsearch):
  services_declared        — kinds a compose/CI file declares
  declared_but_mocked      — declared kinds the CI-executed tests mock (so not truly needed)
  services_truly_required  — kinds the test suite actually dials (the dynamic truth)
  bindings                 — [{var, kind}] config->service bindings that should be found
  provision_expectations   — [{kind, expect}] ground truth for the provisioning eval: one
                             entry per kind in `services_declared`, "provisionable" unless
                             the kind is a known non-provisionable one (see provision_corpus.py)
  source, notes            — provenance + the case's headline question
"""
from __future__ import annotations

LABELS: dict[str, dict] = {
    "multi_docker_eval_jhao104__proxy_pool": {
        "source": "inspection 2026-07: docker-compose.yml, requirements-test.txt, tests/conftest.py",
        "services_declared": ["redis"],
        "declared_but_mocked": ["redis"],
        "services_truly_required": [],
        "bindings": [{"var": "DB_CONN", "kind": "redis"}],
        "provision_expectations": [{"kind": "redis", "expect": "provisionable"}],
        "notes": ("Mock trap: redis is declared + healthchecked (service 'proxy_redis', image "
                  "'redis', DB_CONN=redis://...), but CI tests patch it with fakeredis "
                  "(requirements-test.txt + conftest). HEADLINE: does confidence stay OFF "
                  "'confirmed' via the dev_mock downgrade?"),
    },
    "multi_docker_eval_LibreTranslate__LibreTranslate": {
        "source": "inspection 2026-07: docker-compose.yml",
        "services_declared": [],
        "declared_but_mocked": [],
        "services_truly_required": [],
        "bindings": [],
        "provision_expectations": [],
        "notes": ("Only the app container is declared; storage defaults to memory:// (no "
                  "service). HEADLINE: the module must surface NO backing service (no false "
                  "positive from the app container name)."),
    },
    "multi_docker_eval_NevaMind-AI__memU-server": {
        "source": "inspection 2026-07: docker-compose.yml (pgvector/pgvector:pg16), tests/*.py",
        "services_declared": ["postgres"],
        "declared_but_mocked": ["postgres"],
        "services_truly_required": [],
        "bindings": [],
        "provision_expectations": [{"kind": "postgres", "expect": "provisionable"}],
        "notes": ("KNOWN BLIND SPOT: postgres is declared, but the tests mock the service layer "
                  "via unittest.mock.patch('app.main.create_memory_service') — invisible to the "
                  "dev-dep/conftest scanner. Expected: the module CANNOT downgrade postgres "
                  "(mock_downgrade FAILS). This case quantifies the unittest.mock gap, not a bug "
                  "to hide."),
    },
}
