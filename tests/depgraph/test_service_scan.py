import textwrap

from python_deps.depgraph.service_scan import service_from_url, scan_compose_services, scan_ci_services


def _w(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_postgres_url_full():
    assert service_from_url("postgres://u:p@db:5432/app") == ("postgres", "db", 5432)


def test_scheme_aliases():
    assert service_from_url("postgresql://x")[0] == "postgres"
    assert service_from_url("redis://cache:6379/0") == ("redis", "cache", 6379)
    assert service_from_url("mongodb://m/db")[0] == "mongo"
    assert service_from_url("amqp://broker")[0] == "rabbitmq"


def test_sqlite_and_unknown_return_none():
    assert service_from_url("sqlite:///db.sqlite3") is None
    assert service_from_url("not-a-url") is None
    assert service_from_url("") is None


def test_scan_compose_services(tmp_path):
    _w(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:15
            ports: ["5432:5432"]
          cache:
            image: redis:7
    """)
    found = scan_compose_services(str(tmp_path))
    assert found["postgres"]["image"] == "postgres:15"
    assert "redis" in found


def test_scan_ci_services_and_presence(tmp_path):
    _w(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          test:
            services:
              postgres:
                image: postgres:14
    """)
    found, present = scan_ci_services(str(tmp_path))
    assert present is True
    assert "postgres" in found


def test_scan_ci_no_services_block(tmp_path):
    _w(tmp_path, ".github/workflows/ci.yml", "jobs:\n  test:\n    steps: []\n")
    found, present = scan_ci_services(str(tmp_path))
    assert found == {} and present is False


from python_deps.depgraph.service_scan import classify_service_error


def test_classify_service_errors():
    assert classify_service_error("psycopg2.OperationalError: could not connect to server") == "postgres"
    assert classify_service_error("redis.exceptions.ConnectionError: Error 111 connecting") == "redis"
    assert classify_service_error("pymongo.errors.ServerSelectionTimeoutError: ...") == "mongo"
    assert classify_service_error("ImportError: no module named foo") is None


from python_deps.depgraph.service_scan import scan_services
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, DiscoveredBy, EdgeType, State,
)
from python_deps.depgraph.ids import service_id, syslib_id, package_id, project_id, config_id


def _graph(pkgs=("psycopg2",), configs=()):
    g = DepGraph().with_node(Node(id=project_id("app"), type=NodeType.PROJECT,
        name="app", layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN))
    for p in pkgs:
        g = g.with_node(Node(id=package_id(p, "1.0"), type=NodeType.PACKAGE, name=p,
            layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="1.0"))
    for var, fix in configs:
        g = g.with_node(Node(id=config_id(var), type=NodeType.CONFIG, name=var,
            layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN,
            fix_candidates=(fix,)))
    return g


def test_confirmed_ci_service_gets_node_and_package_edge(tmp_path):
    _w(tmp_path, ".github/workflows/ci.yml",
       "jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:14\n")
    g = scan_services(str(tmp_path), _graph(pkgs=("psycopg2",)))
    node = g.get(service_id("postgres"))
    assert node is not None and node.type is NodeType.SERVICE and node.tier == 5
    assert node.data["service_confidence"] == "confirmed"
    assert any(e.src == package_id("psycopg2", "1.0") and e.dst == service_id("postgres")
               and e.relation is EdgeType.REQUIRES for e in g.edges)


def test_inferred_package_service_has_no_requires_edge(tmp_path):
    g = scan_services(str(tmp_path), _graph(pkgs=("psycopg2",)))   # no CI/compose
    node = g.get(service_id("postgres"))
    assert node is not None and node.data["service_confidence"] == "inferred"
    assert node.data.get("inducing_package") == "psycopg2"
    assert not any(e.dst == service_id("postgres") for e in g.edges)  # no structural edge


def test_inferred_suppressed_when_ci_block_present_without_it(tmp_path):
    # CI declares redis only; psycopg2-inferred postgres must be suppressed.
    _w(tmp_path, ".github/workflows/ci.yml",
       "jobs:\n  test:\n    services:\n      redis:\n        image: redis:7\n")
    g = scan_services(str(tmp_path), _graph(pkgs=("psycopg2",)))
    assert g.get(service_id("postgres")) is None
    assert g.get(service_id("redis")) is not None


def test_inferred_from_config_url(tmp_path):
    g = scan_services(str(tmp_path),
        _graph(pkgs=(), configs=[("DATABASE_URL", "env:DATABASE_URL=postgres://db:5432/x")]))
    node = g.get(service_id("postgres"))
    assert node.data["service_confidence"] == "inferred"
    assert node.data.get("bound_config") == "DATABASE_URL"
    assert node.data.get("port") == 5432


def test_scan_compose_services_modern_compose_yaml(tmp_path):
    # Compose v2 canonical filename — must be discovered (gap ③ regression test).
    _w(tmp_path, "compose.yml", """
        services:
          db:
            image: postgres:18
    """)
    found = scan_compose_services(str(tmp_path))
    assert "postgres" in found and found["postgres"]["image"] == "postgres:18"


def test_scan_compose_services_override_variant(tmp_path):
    _w(tmp_path, "compose.override.yml", """
        services:
          cache:
            image: redis:7
    """)
    assert "redis" in scan_compose_services(str(tmp_path))


def test_scan_compose_services_ignores_lookalike(tmp_path):
    # must NOT match red herrings like composer.yml / compose-notes.yml
    _w(tmp_path, "composer.yml", "services:\n  db:\n    image: postgres:16\n")
    _w(tmp_path, "compose-notes.yml", "services:\n  db:\n    image: postgres:16\n")
    assert scan_compose_services(str(tmp_path)) == {}


def test_broker_suppressed_when_concrete_redis_confirmed(tmp_path):
    # celery -> abstract broker, but compose declares a concrete redis -> broker is redundant.
    _w(tmp_path, "docker-compose.yml", "services:\n  cache:\n    image: redis:7\n")
    g = scan_services(str(tmp_path), _graph(pkgs=("celery",)))
    assert g.get(service_id("redis")) is not None          # concrete broker kept
    assert g.get(service_id("broker")) is None             # abstract broker suppressed


def test_broker_suppressed_when_concrete_redis_inferred(tmp_path):
    # No compose; celery + redis packages -> redis inferred, abstract broker suppressed.
    g = scan_services(str(tmp_path), _graph(pkgs=("celery", "redis")))
    assert g.get(service_id("redis")) is not None
    assert g.get(service_id("broker")) is None


def test_broker_kept_when_no_concrete_broker(tmp_path):
    # celery alone, no concrete redis/rabbitmq anywhere -> the generic broker hint is kept.
    g = scan_services(str(tmp_path), _graph(pkgs=("celery",)))
    node = g.get(service_id("broker"))
    assert node is not None and node.data["service_confidence"] == "inferred"


def test_confirmed_service_absorbs_config_url_binding(tmp_path):
    # compose-confirmed redis + a CONFIG node whose URL points at redis ->
    # the confirmed node must carry bound_config and the advisory must show it.
    _w(tmp_path, "docker-compose.yml", "services:\n  cache:\n    image: redis:7\n")
    g0 = _graph(pkgs=(), configs=[("CELERY_BROKER_URL", "env:CELERY_BROKER_URL=redis://cache:6379/0")])
    g = scan_services(str(tmp_path), g0)
    node = g.get(service_id("redis"))
    assert node.data["service_confidence"] == "confirmed"   # still confirmed
    assert node.data.get("bound_config") == "CELERY_BROKER_URL"
    from python_deps.depgraph.advise import render_dep_graph_advisory
    assert "addresses: CELERY_BROKER_URL" in render_dep_graph_advisory(g)


def test_confirmed_service_without_config_url_has_no_binding(tmp_path):
    # confirmed postgres, no matching config URL -> bound_config stays absent.
    _w(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres:16\n")
    g = scan_services(str(tmp_path), _graph(pkgs=("psycopg2",)))
    node = g.get(service_id("postgres"))
    assert node.data["service_confidence"] == "confirmed"
    assert node.data.get("bound_config") is None


from python_deps.depgraph.service_scan import (
    attach_in_image_provisioning, postgres_start_recipe, service_db_from_url,
)


def _confirmed_pg_graph(port=5432, db=None):
    data = {"service_confidence": "confirmed", "image": "postgres:14",
            "host": "postgres", "port": port}
    if db:
        data["db"] = db
    svc = Node(id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=State.UNKNOWN,
               check_command=f"pg_isready -h postgres -p {port}",
               data=data)
    return DepGraph().with_node(svc)


def test_service_db_from_url():
    assert service_db_from_url("postgres://u:p@db:5432/appdb") == "appdb"
    assert service_db_from_url("postgresql://h/only_db") == "only_db"
    assert service_db_from_url("postgres://h:5432/") is None
    assert service_db_from_url("not-a-url") is None


def test_recipe_is_root_safe_and_version_resolved():
    r = postgres_start_recipe(5432, "appdb")
    assert "runuser -u postgres" in r["start"]
    assert "/etc/postgresql" in r["start"]            # runtime version resolution
    assert "pg_isready -h 127.0.0.1 -p 5432" == r["certify"]
    assert "createdb" in r["createdb"] and "appdb" in r["createdb"]
    assert "|| true" not in r["createdb"]             # FATAL
    r2 = postgres_start_recipe(5432, None)
    assert r2["createdb"] is None                     # no name -> no createdb line


def test_attach_disabled_is_noop():
    g = _confirmed_pg_graph()
    assert attach_in_image_provisioning(g, enabled=False) is g or \
        attach_in_image_provisioning(g, enabled=False).to_dict() == g.to_dict()


def test_attach_adds_systemlib_edge_loopback_and_recipe():
    g = attach_in_image_provisioning(_confirmed_pg_graph(db="appdb"), enabled=True)
    sysl = g.get(syslib_id("postgresql"))
    assert sysl is not None and sysl.type is NodeType.SYSTEM_LIB
    assert sysl.chosen_fix == "apt:postgresql"
    assert any(e.src == service_id("postgres") and e.dst == syslib_id("postgresql")
               and e.relation is EdgeType.REQUIRES for e in g.edges)
    svc = g.get(service_id("postgres"))
    assert svc.check_command == "pg_isready -h 127.0.0.1 -p 5432"   # loopback rewrite
    assert svc.data["start_recipe"]["system_package"] == "postgresql"
    assert "127.0.0.1" in svc.data["start_recipe"]["certify"]


def test_attach_skips_inferred_service():
    data = {"service_confidence": "inferred", "host": "postgres", "port": 5432}
    svc = Node(id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.RESOLVER,
               state=State.UNKNOWN, check_command="pg_isready -h postgres -p 5432",
               data=data)
    g = attach_in_image_provisioning(DepGraph().with_node(svc), enabled=True)
    assert g.get(syslib_id("postgresql")) is None          # inferred not promoted
    assert g.get(service_id("postgres")).check_command == "pg_isready -h postgres -p 5432"
