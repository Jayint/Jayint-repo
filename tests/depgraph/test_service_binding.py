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
