import textwrap

from graph.service_construct import _fuse, build_service_nodes
from graph.service_sources import RawDeclaration


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


def test_depends_on_in_an_unreferenced_compose_does_not_reclassify_the_app(tmp_path):
    """`depends_on` is a WITHIN-DOCUMENT relation. Unioning it repo-wide let an unrelated
    compose force the repo's own app (build: + image:) to be surfaced as a backing service."""
    root = tmp_path / "myrepo"
    root.mkdir()
    _write(root, "docker-compose.yml", """
        services:
          app:
            build: .
            image: myorg/app:dev
            ports: ["8000:8000"]
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    _write(root, "sub/docker-compose.yml", """
        services:
          other:
            image: nginx:1
            depends_on: [app]
    """)
    names = sorted(n.name for n in build_service_nodes(str(root)))
    assert "app" not in names          # rule 1 (build:) still fires
    assert names == ["db", "other"]


def test_same_name_different_images_is_flagged_not_silently_merged(tmp_path):
    """mlflow ships a postgres variant and a mysql variant, both naming the service `db`.
    Keep the highest-relevance image, but SAY the image is ambiguous, and keep both raws."""
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    _write(tmp_path, "docker-compose.mysql.yml", """
        services:
          db:
            image: mysql:8
            ports: ["3306:3306"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.image == "postgres:16"                 # root_compose outranks unreferenced
    assert "image" in node.unresolved                  # ...but the ambiguity is surfaced
    assert set(node.raw) == {"compose:docker-compose.yml",
                             "compose:docker-compose.mysql.yml"}
    assert node.raw["compose:docker-compose.mysql.yml"]["image"] == "mysql:8"


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
    # `raw` is keyed "<kind>:<file>" uniformly, so a second file declaring the same
    # service name cannot silently overwrite the first.
    assert set(node.raw) == {"compose:docker-compose.yml",
                             "ci:.github/workflows/ci.yml"}


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


def test_a_templated_image_in_one_declaration_does_not_delete_the_service(tmp_path):
    """One declaration's image NAME is genuinely opaque (a `${VAR}` with no declared
    default, so interpolation cannot recover it); another resolves. `_fuse` must take the
    first RESOLVABLE image, surface the ambiguity via `_image_conflict`, and never delete
    the NODE (spec §3.0.2 inv. 4). This is the WITNESS for that rule: it fails against a
    `_fuse` that takes the first non-empty image string.

    NOTE: PostHog's own `db` used to motivate this test with
    `${DOCKER_REGISTRY_PREFIX:-}postgres:15.12-alpine`. That is a `${VAR:-}` empty default,
    which `expand_declared_defaults` now interpolates to `postgres:15.12-alpine` inside
    `parse_image` (Task 16). Both of PostHog's declarations therefore resolve identically
    upstream and there is no longer any conflict to surface — `db` is recovered by
    interpolation alone, not by this `_fuse` rung. A `${VAR:-default}` fixture would make
    this test vacuous (it would pass even against the old first-non-empty `_fuse`), so a
    genuinely opaque NAME is required.

    `docker-compose.yml` is `root_compose` and the `.prod.` variant is
    `unreferenced_compose`, so the opaque declaration is deterministically FIRST in
    `_fuse`'s `(relevance, file, locator)` ordering — independent of `os.walk` order.
    """
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: ${POSTGRES_IMAGE}:15.12-alpine
            ports: ["5432:5432"]
    """)
    _write(tmp_path, "docker-compose.prod.yml", """
        services:
          db:
            image: postgres:15.12-alpine
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.name == "db"
    assert node.image_repo == "postgres"            # taken from the declaration that RESOLVES
    assert "image" in node.unresolved               # ...and the ambiguity is surfaced
    # the opaque declaration is still evidence the agent can read
    assert "compose:docker-compose.yml" in node.raw


def test_a_service_templated_in_EVERY_declaration_is_still_dropped(tmp_path):
    """The rule is `all`, not `any`. No declaration resolves -> no evidence -> no node."""
    _write(tmp_path, "docker-compose.yml", """
        services:
          broken:
            image: $REGISTRY_URL:$TAG
    """)
    # NOTE: the brief's original fixture used `${OTHER_REGISTRY}/img:$TAG`, but that RESOLVES
    # -- `parse_image` templates only the image NAME (the last path segment `img`), not the
    # registry prefix, so its repo is `${OTHER_REGISTRY}/img` and the node is NOT dropped.
    # To exercise the `all`-not-`any` rule the docstring describes, both NAMES must template.
    _write(tmp_path, "docker-compose.other.yml", """
        services:
          broken:
            image: ${OTHER_IMAGE}:$TAG
    """)
    assert build_service_nodes(str(tmp_path)) == []


def test_declaration_order_within_a_relevance_tier_is_deterministic(tmp_path):
    """Two same-tier declarations naming different images must resolve the same way on every
    filesystem. Today `sorted` is stable over `os.walk` order, so the winner is incidental."""
    _write(tmp_path, "b/docker-compose.yml", """
        services:
          db:
            image: mysql:8
            ports: ["3306:3306"]
    """)
    _write(tmp_path, "a/docker-compose.yml", """
        services:
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    # `a/...` sorts before `b/...`; both are `unreferenced_compose`.
    assert node.image == "postgres:16"
    assert node.port == 5432
    assert "image" in node.unresolved


def _decl(file, image, port):
    return RawDeclaration(
        name="db", entry={"image": image, "ports": [f"{port}:{port}"]},
        file=file, locator="services.db", kind="compose", doc_env_values=(),
    )


def test_fuse_is_independent_of_declaration_input_order():
    """The order-independence guarantee, pinned where it actually lives.

    Going through `build_service_nodes` cannot pin this: discovery order comes from
    `os.walk`, so on a filesystem that happens to yield `a/` before `b/` the test passes
    even against a `_fuse` that has no tiebreak at all. Drive `_fuse` directly with both
    input orders instead — pre-tiebreak, `sorted` is stable on a rank-only key, so the
    two calls return different images and this fails.
    """
    a = _decl("a/docker-compose.yml", "postgres:16", 5432)
    b = _decl("b/docker-compose.yml", "mysql:8", 3306)

    forward = _fuse("db", [a, b], frozenset())
    reverse = _fuse("db", [b, a], frozenset())

    assert forward.image == reverse.image == "postgres:16"
    assert forward.port == reverse.port == 5432
    assert forward.provenance == reverse.provenance
    assert "image" in forward.unresolved            # ambiguity still surfaced, not hidden
