# tests/depgraph/test_service_binding.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from python_deps.depgraph.service_scan import service_bind_url  # noqa: E402


def test_bind_url_preserves_app_scheme_and_overrides_host_creds():
    assert service_bind_url("postgresql", 5432, "postgres") == \
        "postgresql://postgres:postgres@127.0.0.1:5432/postgres"


def test_bind_url_preserves_dialect_suffix():
    assert service_bind_url("postgresql+psycopg2", 5432, "appdb") == \
        "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/appdb"


def test_bind_url_custom_port_and_db():
    assert service_bind_url("postgresql", 5433, "mydb") == \
        "postgresql://postgres:postgres@127.0.0.1:5433/mydb"


import textwrap  # noqa: E402
from python_deps.depgraph.service_scan import scan_env_bindings  # noqa: E402


def _write(tmp_path, name, body):
    (tmp_path / name).write_text(textwrap.dedent(body), encoding="utf-8")


def test_scan_env_bindings_list_form(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        version: "3.7"
        services:
          api:
            depends_on: [db]
            environment:
              - DB_STRING=postgresql://postgres:test@db:5432/appdb
          db:
            image: postgres:14.5
    """)
    out = scan_env_bindings(str(tmp_path))
    assert "postgres" in out
    b = out["postgres"]
    assert b["var"] == "DB_STRING"
    assert b["url"] == "postgresql://postgres:test@db:5432/appdb"
    assert b["db"] == "appdb"


def test_scan_env_bindings_map_form_default_db(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            environment:
              DATABASE_URL: postgresql://postgres:test@db:5432/postgres
          db:
            image: postgres:14.5
    """)
    out = scan_env_bindings(str(tmp_path))
    assert out["postgres"]["var"] == "DATABASE_URL"
    assert out["postgres"]["db"] == "postgres"


def test_scan_env_bindings_ignores_nonservice_urls(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          api:
            environment:
              - SOME_HTTP=https://example.com/x
    """)
    assert scan_env_bindings(str(tmp_path)) == {}


from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State  # noqa: E402
from python_deps.depgraph.ids import service_id, config_id  # noqa: E402
from python_deps.depgraph.service_scan import (  # noqa: E402
    attach_in_image_provisioning, scan_services,
)


# ---------------------------------------------------------------------------
# Finding 1: the NEW compose/CI `environment:` env-binding absorption onto a
# confirmed SERVICE node is ARM-GATED (`bind_env`). Off-arm it must NOT fire
# (off-state byte-identity); on-arm it does. The pre-existing inferred
# CONFIG-URL binding path is unaffected (always-on).
# ---------------------------------------------------------------------------

def _compose_with_env_db_url(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          api:
            depends_on: [db]
            environment:
              - DB_STRING=postgresql://postgres:test@db:5432/appdb
          db:
            image: postgres:14.5
    """)
    return DepGraph(nodes=(), edges=())


def test_env_binding_not_absorbed_off_arm(tmp_path):
    # OFF-arm (bind_env=False, the default): the confirmed postgres node carries
    # NO env-derived bound_config_url / db. (The inferred CONFIG-URL path may
    # still set bound_config when a CONFIG node exists; here there is none.)
    g = _compose_with_env_db_url(tmp_path)
    out = scan_services(str(tmp_path), g)
    node = out.get(service_id("postgres"))
    assert node is not None and node.data["service_confidence"] == "confirmed"
    assert node.data.get("bound_config_url") is None
    assert node.data.get("db") is None
    # Default == explicit False (the env path must not fire either way).
    assert scan_services(str(tmp_path), g, bind_env=False).to_dict() == out.to_dict()


def test_env_binding_absorbed_on_arm(tmp_path):
    # ON-arm (bind_env=True): the env DB-URL IS absorbed onto the confirmed node.
    g = _compose_with_env_db_url(tmp_path)
    out = scan_services(str(tmp_path), g, bind_env=True)
    node = out.get(service_id("postgres"))
    assert node is not None and node.data["service_confidence"] == "confirmed"
    assert node.data.get("bound_config") == "DB_STRING"
    assert node.data.get("bound_config_url") == \
        "postgresql://postgres:test@db:5432/appdb"
    assert node.data.get("db") == "appdb"


def _confirmed_pg_graph():
    svc = Node(
        id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
        layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
        check_command="pg_isready -h postgres -p 5432", fix_candidates=("service:postgres:14",),
        chosen_fix="service:postgres:14", evidence="compose", provenance="service scan",
        data={"service_confidence": "confirmed", "host": "postgres", "port": 5432,
              "bound_config": "DB_STRING",
              "bound_config_url": "postgresql://postgres:test@db:5432/appdb", "db": "appdb"},
    )
    return DepGraph(nodes=(svc,), edges=())


def test_attach_adds_binding_node_and_edge_when_enabled():
    g = attach_in_image_provisioning(_confirmed_pg_graph(), enabled=True)
    bnode = g.get(config_id("DB_STRING"))
    assert bnode is not None
    assert bnode.type is NodeType.CONFIG
    assert bnode.data.get("binding") is True
    assert bnode.chosen_fix == "env:DB_STRING=postgresql://postgres:postgres@127.0.0.1:5432/appdb"
    assert bnode.check_command == \
        'psql "postgresql://postgres:postgres@127.0.0.1:5432/appdb" -c "select 1"'
    assert bnode.data["bind_recipe"]["var"] == "DB_STRING"
    assert "ALTER USER postgres PASSWORD" in bnode.data["bind_recipe"]["alter_user"]
    assert "/etc/profile.d/zz_service_bind.sh" in bnode.data["bind_recipe"]["bind_profile"]
    assert any(e.src == config_id("DB_STRING") and e.dst == service_id("postgres")
               for e in g.edges)


def test_attach_no_binding_node_when_disabled():
    g = attach_in_image_provisioning(_confirmed_pg_graph(), enabled=False)
    assert g.get(config_id("DB_STRING")) is None


def test_attach_no_binding_node_without_bound_config():
    g0 = _confirmed_pg_graph()
    svc = g0.nodes[0]
    bare = DepGraph(nodes=(svc.__class__(**{**svc.__dict__,
        "data": {"service_confidence": "confirmed", "host": "postgres", "port": 5432}}),), edges=())
    g = attach_in_image_provisioning(bare, enabled=True)
    # no bound env var discovered -> no binding node (service still provisioned)
    assert all(n.type is not NodeType.CONFIG for n in g.nodes)


from python_deps.depgraph.certify import certify  # noqa: E402


class _PsqlExec:
    """Executor: psql probe rc by `ok`; everything else rc 0."""
    def __init__(self, ok): self.ok = ok

    def run(self, command, *, timeout: int = 300):
        from python_deps.depgraph.executor import CommandResult
        rc = 0 if ("psql" in command and self.ok) else (1 if "psql" in command else 0)
        return CommandResult(command=command, returncode=rc, stdout="", stderr="")


def test_binding_certifies_satisfied_only_when_psql_connects():
    g = attach_in_image_provisioning(_confirmed_pg_graph(), enabled=True)
    bid = config_id("DB_STRING")
    sat = certify(g, bid, _PsqlExec(ok=True), 0)
    assert sat.get(bid).state is State.SATISFIED
    miss = certify(g, bid, _PsqlExec(ok=False), 0)
    assert miss.get(bid).state is not State.SATISFIED
