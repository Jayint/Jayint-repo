import textwrap

from graph.python.services.service_sources import (
    ComposeSource, GithubActionsSource, discover_all,
)


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))
    return p


def test_compose_source_yields_each_service_with_locator_and_blob(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            image: myapp:1
            environment:
              DATABASE_URL: postgres://u:p@db:5432/app
          db:
            image: postgres:16
    """)
    decls = list(ComposeSource().discover(str(tmp_path)))
    assert {d.name for d in decls} == {"web", "db"}
    db = next(d for d in decls if d.name == "db")
    assert db.kind == "compose"
    assert db.locator == "services.db"
    assert db.file == "docker-compose.yml"
    assert any("db:5432" in v for v in db.doc_env_values)   # sibling evidence carried along
    assert isinstance(db.doc_env_values, tuple)             # values, not a blob


def test_ci_source_reads_jobs_services_and_requires_an_image(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          test:
            services:
              valkey:
                image: valkey/valkey:8
              disabled: "not-a-mapping"
              imageless:
                ports: ["1:1"]
    """)
    decls = list(GithubActionsSource().discover(str(tmp_path)))
    assert [d.name for d in decls] == ["valkey"]
    assert decls[0].kind == "ci"
    assert decls[0].locator == "jobs.test.services.valkey"
    assert decls[0].doc_env_values == ()


def test_vendored_trees_are_pruned(tmp_path):
    """A compose file under node_modules/ belongs to a dependency, not this repo."""
    _write(tmp_path, "node_modules/pkg/docker-compose.yml", """
        services:
          vendored:
            image: postgres:16
    """)
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:16
    """)
    assert [d.name for d in ComposeSource().discover(str(tmp_path))] == ["db"]


def test_list_form_environment_reaches_doc_env_values(tmp_path):
    """compose allows `environment:` as a LIST of `K=V` strings, not only a mapping."""
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            image: app:1
            environment:
              - DATABASE_URL=postgres://u:p@db:5432/app
          db:
            image: postgres:16
    """)
    db = next(d for d in ComposeSource().discover(str(tmp_path)) if d.name == "db")
    assert any("db:5432" in v for v in db.doc_env_values)


def test_sources_never_raise_on_malformed_yaml(tmp_path):
    _write(tmp_path, "docker-compose.yml", "services: [redis: image: redis:7\n")
    _write(tmp_path, ".github/workflows/x.yml", "jobs:\n  t:\n    services: 'nope'\n")
    assert discover_all(str(tmp_path)) == []


def test_discover_all_returns_compose_then_ci(tmp_path):
    _write(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres:16\n")
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  t:\n    services:\n      redis:\n        image: redis:7\n")
    kinds = [d.kind for d in discover_all(str(tmp_path))]
    assert kinds == ["compose", "ci"]
