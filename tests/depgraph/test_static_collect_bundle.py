# tests/depgraph/test_static_collect_bundle.py
import json
from python_deps.depgraph.static_collect import (
    DeterministicHit, collect_static_evidence, compact_bundle_json,
)


def _repo(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "test.yml").write_text(
        "jobs:\n  t:\n    services:\n      postgres:\n        image: postgres:15\n"
        "        ports: ['5432:5432']\n")
    (tmp_path / ".env.example").write_text("DATABASE_URL=postgres://localhost/db\n")
    return str(tmp_path)


def test_ci_postgres_and_env_var_hits(tmp_path):
    hits = collect_static_evidence(_repo(tmp_path))
    kinds = {h.kind for h in hits}
    assert "ci_service" in kinds                       # postgres from CI
    assert any(h.kind == "env_var" and h.name == "DATABASE_URL" for h in hits)
    # every hit has a stable evidence_id and a file
    assert all(h.evidence_id and h.file for h in hits)


def test_compact_bundle_json_shape(tmp_path):
    hits = collect_static_evidence(_repo(tmp_path))
    bundle = json.loads(compact_bundle_json(hits))
    assert "goal" in bundle and isinstance(bundle["deterministic_hits"], list)
    assert {"evidence_id", "file", "kind"} <= set(bundle["deterministic_hits"][0])
