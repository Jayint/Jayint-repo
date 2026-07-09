import textwrap

from python_deps.depgraph.service_construct import build_service_nodes


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_backing_service_is_certifiable_and_records_its_rungs(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:16
            ports: ["5432:5432"]
            environment: {POSTGRES_DB: app}
            command: postgres -c max_connections=200
            volumes: ["./init.sql:/docker-entrypoint-initdb.d/init.sql"]
            healthcheck:
              test: ["CMD", "pg_isready", "-U", "u"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.id == "service:db" and node.image_repo == "postgres" and node.image_tag == "16"
    assert node.port == 5432 and node.port_source == "ports"
    assert node.endpoint == "localhost:5432"
    assert node.check.source == "declared_healthcheck" and node.check.command == "pg_isready -U u"
    assert node.state == "certifiable_obligation"
    assert node.command == "postgres -c max_connections=200"
    assert len(node.seed) == 1
    assert node.relevance == "root_compose"


def test_app_services_are_excluded(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            build: .
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    assert [n.name for n in build_service_nodes(str(tmp_path))] == ["db"]


def test_first_party_image_without_port_or_healthcheck_is_the_app(tmp_path):
    # OpenCTI: `opencti/connector-x` in owner OpenCTI-Platform, no ports, no healthcheck.
    _write(tmp_path, "docker-compose.yml", """
        services:
          connector:
            image: opencti/connector-rst:1.0
            environment: {OPENCTI_URL: http://external:8080}
          minio:
            image: minio/minio:latest
            ports: ["9000:9000"]
    """)
    names = [n.name for n in build_service_nodes(str(tmp_path), owner="OpenCTI-Platform")]
    assert names == ["minio"]


def test_first_party_image_WITH_a_port_stays_a_backing_service(tmp_path):
    # supabase/postgres in owner supabase — first-party, but exposes a port.
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: supabase/postgres:15.1
            ports: ["5432:5432"]
    """)
    assert [n.name for n in build_service_nodes(str(tmp_path), owner="supabase")] == ["db"]


def test_image_named_after_the_repo_is_the_app(tmp_path):
    """CLASSIFY rule 2. Direction matters: the REPO name is a substring of the IMAGE
    name (`podman-compose` in `podman-compose-test`), not the reverse."""
    root = tmp_path / "podman-compose"
    root.mkdir()
    _write(root, "docker-compose.yml", """
        services:
          app:
            image: containers/podman-compose-test:1
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    assert [n.name for n in build_service_nodes(str(root))] == ["db"]


def test_a_digest_pinned_image_does_not_report_an_unresolved_tag(tmp_path):
    """`repo@sha256:...` legitimately has no tag. Only a `:tag` that vanished to a
    template is `unresolved`."""
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres@sha256:abcdef0123456789
            ports: ["5432:5432"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.image_tag is None
    assert "image_tag" not in node.unresolved


def test_a_templated_tag_does_report_an_unresolved_tag(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            services:
              valkey:
                image: valkey/valkey:${{ matrix.valkey-version }}
                ports: ["6379:6379"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.image_repo == "valkey/valkey" and node.image_tag is None
    assert "image_tag" in node.unresolved


def test_fuse_merges_compose_and_ci_evidence_for_the_same_name(tmp_path):
    # compose has volumes but no healthcheck; CI has the --health-cmd. Keep BOTH.
    _write(tmp_path, "docker-compose.yml", """
        services:
          redis:
            image: redis:7
            volumes: ["./r.conf:/etc/redis.conf"]
    """)
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            services:
              redis:
                image: redis:7
                ports: ["6379:6379"]
                options: --health-cmd "redis-cli ping" --health-retries 5
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.check.source == "declared_healthcheck"     # from CI
    assert node.check.command == "redis-cli ping"
    assert len(node.volumes) == 1                          # from compose
    assert node.port == 6379
    assert {p.kind for p in node.provenance} == {"compose", "ci"}
    assert set(node.raw) == {"compose", "ci"}


def test_no_healthcheck_but_a_port_yields_a_tcp_check(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    (node,) = build_service_nodes(str(tmp_path))
    assert node.check.source == "tcp_port" and node.state == "certifiable_obligation"


def test_no_healthcheck_and_no_port_is_admitted_but_unverifiable(tmp_path):
    # Spoolman-style: real backing service, no evidence for a check.
    _write(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres:11-alpine\n")
    (node,) = build_service_nodes(str(tmp_path))
    assert node.check.source == "none"
    assert node.state == "declared_unverifiable"           # surfaced, NOT dropped


def test_sibling_dsn_rescues_the_port(tmp_path):
    # `web` carries `build:` so CLASSIFY rule 1 drops it, exactly as Spoolman declares it.
    # Its env still feeds `doc_env_values`, which is per-DOCUMENT, so `db` is still rescued.
    # (A bare `image: myapp:1` app with no port and no healthcheck would be SURFACED as a
    # backing service under `owner=""` — the documented limit of evidence-only CLASSIFY,
    # not something a fourth rule should paper over.)
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            build: .
            environment: {DATABASE_URL: "postgres://u:p@db:5432/app"}
          db:
            image: postgres:16
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.name == "db" and node.port == 5432 and node.port_source == "sibling_dsn"
    assert node.state == "certifiable_obligation"


def test_templated_image_name_drops_node_templated_tag_does_not(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            services:
              valkey:
                image: valkey/valkey:${{ matrix.v }}
                ports: ["6379:6379"]
              broken:
                image: $REGISTRY_URL:$TAG
    """)
    nodes = build_service_nodes(str(tmp_path))
    assert [n.name for n in nodes] == ["valkey"]
    (n,) = nodes
    assert n.image_repo == "valkey/valkey" and n.image_tag is None
    assert "image_tag" in n.unresolved


def test_depends_on_in_degree_marks_backing_even_for_first_party(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            image: acme/web:1
            depends_on: [cache]
          cache:
            image: acme/cache:1
    """)
    # `cache` has in-degree 1 -> backing, despite being first-party with no port.
    assert [n.name for n in build_service_nodes(str(tmp_path), owner="acme")] == ["cache"]
