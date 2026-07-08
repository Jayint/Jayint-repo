import textwrap

from python_deps.depgraph.provisioning_spec import (
    parse_provisioning_spec,
    iter_provisioning_specs,
)


def _write_compose(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))
    return p


def test_parse_postgres_entry():
    entry = {
        "image": "postgres:16",
        "environment": {
            "POSTGRES_DB": "app",
            "POSTGRES_USER": "u",
            "POSTGRES_PASSWORD": "p",
        },
        "ports": ["5432:5432"],
        "healthcheck": {"test": ["CMD", "pg_isready"]},
    }
    spec = parse_provisioning_spec("db", entry)
    assert spec.kind == "postgres"
    assert spec.params == {"db": "app", "user": "u", "password": "p"}
    assert spec.probe == "pg_isready"
    assert spec.port == 5432


def test_probe_from_healthcheck_list_strips_CMD():
    entry = {
        "image": "redis:7",
        "healthcheck": {"test": ["CMD", "redis-cli", "ping"]},
    }
    spec = parse_provisioning_spec("cache", entry)
    assert spec.probe == "redis-cli ping"


def test_init_files_from_volumes():
    entry = {
        "image": "postgres:16",
        "volumes": ["./init.sql:/docker-entrypoint-initdb.d/init.sql"],
    }
    spec = parse_provisioning_spec("db", entry)
    assert spec.init_files == ("./init.sql",)


def test_iter_two_services(tmp_path):
    _write_compose(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:16
            ports:
              - "5432:5432"
          cache:
            image: redis:7
            ports:
              - "6379:6379"
    """)
    specs = list(iter_provisioning_specs(str(tmp_path)))
    assert len(specs) == 2
    kinds = {s.kind for s in specs}
    assert kinds == {"postgres", "redis"}


def test_port_from_string_mapping():
    entry = {"image": "redis:7", "ports": ["6379:6379"]}
    spec = parse_provisioning_spec("cache", entry)
    assert spec.port == 6379


# ---------------------------------------------------------------------------
# GitHub Actions `jobs.<job>.services:` discovery (rq/rq gap — findings §5).
# ---------------------------------------------------------------------------

def _write_workflow(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))
    return p


def test_iter_picks_up_ci_only_service(tmp_path):
    _write_workflow(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          test:
            services:
              redis:
                image: redis:7
                ports:
                  - "6379:6379"
    """)
    specs = list(iter_provisioning_specs(str(tmp_path)))
    assert len(specs) == 1
    assert specs[0].service_name == "redis"
    assert specs[0].kind == "redis"
    assert specs[0].port == 6379


def test_iter_ci_service_env_params():
    # GH Actions services set vars via `env:`, not compose's `environment:`.
    entry = {"image": "postgres:15", "env": {"POSTGRES_DB": "app", "POSTGRES_USER": "u",
                                              "POSTGRES_PASSWORD": "p"}}
    spec = parse_provisioning_spec("postgres", entry)
    assert spec.params == {"db": "app", "user": "u", "password": "p"}


def test_iter_dedupes_same_service_compose_and_ci(tmp_path):
    _write_compose(tmp_path, "docker-compose.yml", """
        services:
          redis:
            image: redis:7
    """)
    _write_workflow(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          test:
            services:
              redis:
                image: redis:7
    """)
    specs = list(iter_provisioning_specs(str(tmp_path)))
    assert len(specs) == 1
    assert specs[0].service_name == "redis"


def test_iter_multiple_workflow_jobs_and_files(tmp_path):
    _write_workflow(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          unit:
            services:
              redis:
                image: redis:7
          integration:
            services:
              postgres:
                image: postgres:15
    """)
    specs = list(iter_provisioning_specs(str(tmp_path)))
    assert {s.service_name for s in specs} == {"redis", "postgres"}


def test_iter_skips_malformed_workflow_without_raising(tmp_path):
    _write_compose(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:16
    """)
    # Unclosed flow sequence with nested colons — a genuine YAML ParserError.
    _write_workflow(tmp_path, ".github/workflows/broken.yml",
                     "jobs:\n  test:\n    services: [redis: image: redis:7\n")
    specs = list(iter_provisioning_specs(str(tmp_path)))
    assert {s.service_name for s in specs} == {"db"}


def test_iter_skips_workflow_with_non_mapping_services(tmp_path):
    _write_workflow(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          test:
            services: "not-a-mapping"
    """)
    assert list(iter_provisioning_specs(str(tmp_path))) == []


def test_iter_no_workflows_dir_is_noop(tmp_path):
    assert list(iter_provisioning_specs(str(tmp_path))) == []
