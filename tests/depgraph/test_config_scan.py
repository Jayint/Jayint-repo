import os
import textwrap
from python_deps.depgraph.config_scan import scan_env_reads
from python_deps.depgraph.config_scan import scan_framework_config_reads
from python_deps.depgraph.config_scan import parse_env_example, configured_vars
from python_deps.depgraph.config_scan import scan_env_defaults


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))
    return p


def test_scan_finds_os_environ_subscript(tmp_path):
    _write(tmp_path, "app/settings.py", """
        import os
        SECRET_KEY = os.environ['SECRET_KEY']
        DEBUG = os.getenv('DEBUG')
        DB = os.environ.get('DATABASE_URL')
    """)
    found = scan_env_reads(str(tmp_path))
    assert set(found) == {"SECRET_KEY", "DEBUG", "DATABASE_URL"}
    assert "settings.py" in found["SECRET_KEY"]


def test_scan_skips_excluded_dirs(tmp_path):
    _write(tmp_path, "examples/demo.py", "import os\nX = os.environ['SHOULD_BE_IGNORED']\n")
    found = scan_env_reads(str(tmp_path))
    assert "SHOULD_BE_IGNORED" not in found


def test_scan_handles_unparseable_file(tmp_path):
    _write(tmp_path, "app/ok.py", "import os\nA = os.getenv('A')\n")
    _write(tmp_path, "app/broken.py", "def (:\n")  # syntax error
    found = scan_env_reads(str(tmp_path))
    assert "A" in found  # broken file skipped, good file still scanned


def test_scan_decouple_and_environs(tmp_path):
    _write(tmp_path, "app/conf.py", """
        from decouple import config
        from environs import Env
        env = Env()
        SECRET = config('SECRET_KEY')
        PORT = env.int('PORT')
    """)
    found = scan_framework_config_reads(str(tmp_path))
    assert "SECRET_KEY" in found
    assert "PORT" in found


def test_scan_pydantic_basesettings_fields(tmp_path):
    _write(tmp_path, "app/settings.py", """
        from pydantic_settings import BaseSettings
        class Settings(BaseSettings):
            database_url: str
            redis_url: str = "redis://localhost"
    """)
    found = scan_framework_config_reads(str(tmp_path))
    assert "DATABASE_URL" in found
    assert "REDIS_URL" in found


def test_parse_env_example_values(tmp_path):
    _write(tmp_path, ".env.example", "DEBUG=True\nDATABASE_URL=postgres://localhost/db\n# comment\nEMPTY=\n")
    vals = parse_env_example(str(tmp_path))
    assert vals["DEBUG"] == "True"
    assert vals["DATABASE_URL"] == "postgres://localhost/db"
    assert vals.get("EMPTY", "") == ""


def test_configured_vars_from_real_dotenv_and_pytest_ini(tmp_path):
    _write(tmp_path, ".env", "ALREADY_SET=1\n")
    _write(tmp_path, "pytest.ini", "[pytest]\nenv =\n    DJANGO_SETTINGS_MODULE=app.settings\n")
    provided = configured_vars(str(tmp_path))
    assert "ALREADY_SET" in provided
    assert "DJANGO_SETTINGS_MODULE" in provided


# --- Task 9: scan_config orchestrator ---

from python_deps.depgraph.config_scan import scan_config
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, DiscoveredBy, State, EdgeType,
)
from python_deps.depgraph.ids import project_id, package_id, config_id


def _graph_with_project_and_pkg(proj="app", pkg="django"):
    p = Node(id=project_id(proj), type=NodeType.PROJECT, name=proj, layer=Layer.PIP,
             discovered_by=DiscoveredBy.STATIC_SCAN)
    d = Node(id=package_id(pkg, "4.2"), type=NodeType.PACKAGE, name=pkg, layer=Layer.PIP,
             discovered_by=DiscoveredBy.RESOLVER, version="4.2")
    return DepGraph().with_node(p).with_node(d)


def test_project_induced_config_node_and_edge(tmp_path):
    _write(tmp_path, "app/settings.py", "import os\nSECRET_KEY = os.environ['SECRET_KEY']\n")
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg())
    node = g.get(config_id("SECRET_KEY"))
    assert node is not None and node.type is NodeType.CONFIG and node.tier == 6
    assert node.check_command == "printenv SECRET_KEY"
    assert node.fix_candidates == ("env:SECRET_KEY=?",)
    assert any(e.src == project_id("app") and e.dst == config_id("SECRET_KEY")
               for e in g.edges)


def test_package_induced_config_node_and_edge(tmp_path):
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg(pkg="django"))
    node = g.get(config_id("DJANGO_SETTINGS_MODULE"))
    assert node is not None
    assert any(e.src == package_id("django", "4.2") and e.dst == config_id("DJANGO_SETTINGS_MODULE")
               for e in g.edges)


def test_value_hint_from_env_example(tmp_path):
    _write(tmp_path, "app/s.py", "import os\nX = os.getenv('DEBUG')\n")
    _write(tmp_path, ".env.example", "DEBUG=False\n")
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg())
    assert g.get(config_id("DEBUG")).fix_candidates == ("env:DEBUG=False",)


def test_already_configured_var_is_suppressed(tmp_path):
    _write(tmp_path, "app/s.py", "import os\nX = os.environ['ALREADY']\n")
    _write(tmp_path, ".env", "ALREADY=1\n")
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg())
    assert g.get(config_id("ALREADY")) is None


def test_package_default_not_lost_when_var_also_project_read(tmp_path):
    """Package curated default must survive even when the same var is project-read first."""
    _write(tmp_path, "app/s.py", "import os\nREGION = os.environ['AWS_DEFAULT_REGION']\n")
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg(pkg="boto3"))
    node = g.get(config_id("AWS_DEFAULT_REGION"))
    assert node is not None, "CONFIG node for AWS_DEFAULT_REGION must be created"
    assert node.fix_candidates == ("env:AWS_DEFAULT_REGION=us-east-1",), (
        f"Expected curated default us-east-1, got: {node.fix_candidates}"
    )


def test_plain_baseconfig_class_not_treated_as_settings(tmp_path):
    # A plain (non-pydantic) `class BaseConfig` and its subclass must NOT have their
    # annotated attrs harvested as env vars (gap ④ false-positive regression test).
    _write(tmp_path, "app/config.py", """
        import os
        class BaseConfig:
            DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///x")
            DATABASE_CONNECT_DICT: dict = {}
        class DevelopmentConfig(BaseConfig):
            DATABASE_CONNECT_DICT: dict = {"pool_pre_ping": True}
    """)
    found = scan_framework_config_reads(str(tmp_path))
    # DATABASE_CONNECT_DICT is a plain dict attr, never read from env -> must be absent.
    assert "DATABASE_CONNECT_DICT" not in found


def test_scan_env_defaults_captures_string_literal(tmp_path):
    _write(tmp_path, "app/config.py", """
        import os
        BROKER = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
        FLAG = os.getenv("FEATURE_FLAG", "on")
        DB = os.environ.get("DATABASE_URL", f"sqlite:///{x}")
        BARE = os.environ.get("NO_DEFAULT")
    """)
    d = scan_env_defaults(str(tmp_path))
    assert d["CELERY_BROKER_URL"] == "redis://127.0.0.1:6379/0"
    assert d["FEATURE_FLAG"] == "on"
    assert "DATABASE_URL" not in d      # f-string default: not statically resolvable
    assert "NO_DEFAULT" not in d        # no default argument


def test_scan_config_uses_env_get_default_as_value(tmp_path):
    _write(tmp_path, "app/config.py",
           'import os\nB = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")\n')
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg())
    node = g.get(config_id("CELERY_BROKER_URL"))
    assert node.fix_candidates == ("env:CELERY_BROKER_URL=redis://127.0.0.1:6379/0",)


def test_env_example_value_wins_over_code_default(tmp_path):
    # .env.example must take precedence over the in-code os.environ.get default.
    _write(tmp_path, "app/s.py", 'import os\nX = os.environ.get("DEBUG", "True")\n')
    _write(tmp_path, ".env.example", "DEBUG=False\n")
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg())
    assert g.get(config_id("DEBUG")).fix_candidates == ("env:DEBUG=False",)
