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
