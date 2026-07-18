import os
import textwrap
from graph.python.config_scan import scan_env_reads
from graph.python.config_scan import scan_framework_config_reads
from graph.python.config_scan import parse_env_example, configured_vars
from graph.python.config_scan import scan_env_defaults
from graph.python.config_scan import _config_node
from graph.python.config_scan import (
    authoritative_ambiguous_vars,
    scan_authoritative_config,
    scan_pyproject_pytest,
    scan_pytest_ini,
    scan_setup_cfg_pytest,
    scan_tox_setenv,
)


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


# ── FIX 1 (A1): os.environ.setdefault(...) — the canonical Django manage.py/wsgi.py idiom ──

def test_scan_finds_os_environ_setdefault(tmp_path):
    _write(tmp_path, "manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
    """)
    found = scan_env_reads(str(tmp_path))
    assert "DJANGO_SETTINGS_MODULE" in found
    assert "manage.py" in found["DJANGO_SETTINGS_MODULE"]


def test_scan_finds_bare_environ_setdefault(tmp_path):
    _write(tmp_path, "wsgi.py", """
        from os import environ
        environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')
    """)
    found = scan_env_reads(str(tmp_path))
    assert "DJANGO_SETTINGS_MODULE" in found


# ── FIX 3 — scan_env_defaults: deterministic walk + AMBIGUOUS -> drop, never pick a variant ──

def test_scan_env_defaults_conflicting_values_across_files_drops_the_var(tmp_path):
    """AMBIGUOUS -> never pick a variant (mirrors `depgraph.python.lanes.install.ground.choose_provider`'s
    AMBIGUOUS branch). Two files disagreeing on a var's literal default must yield
    NEITHER value -- not whichever file the (previously undefined-order) walk
    happened to visit first."""
    _write(tmp_path, "manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'prod.settings')
    """)
    _write(tmp_path, "tests/conftest.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'test.settings')
    """)
    d = scan_env_defaults(str(tmp_path))
    assert "DJANGO_SETTINGS_MODULE" not in d


def test_scan_env_defaults_same_value_across_files_is_not_ambiguous(tmp_path):
    """Two files agreeing on the SAME literal default is not a conflict -- only
    genuinely DIFFERENT values are ambiguous."""
    _write(tmp_path, "manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    _write(tmp_path, "wsgi.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    d = scan_env_defaults(str(tmp_path))
    assert d["DJANGO_SETTINGS_MODULE"] == "proj.settings"


def test_scan_env_defaults_is_deterministic_across_repeated_calls(tmp_path):
    """The walk (dirs + files) is sorted, so repeated scans of the same repo
    always agree -- a bake decision must never depend on filesystem/OS
    iteration order."""
    _write(tmp_path, "z_app/settings.py", """
        import os
        DEBUG = os.environ.get('DEBUG', 'false')
    """)
    _write(tmp_path, "a_app/config.py", """
        import os
        FEATURE_FLAG = os.getenv('FEATURE_FLAG', 'on')
    """)
    _write(tmp_path, "manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    first = scan_env_defaults(str(tmp_path))
    assert first == {
        "DEBUG": "false", "FEATURE_FLAG": "on", "DJANGO_SETTINGS_MODULE": "proj.settings",
    }
    for _ in range(5):
        assert scan_env_defaults(str(tmp_path)) == first


def test_scan_env_defaults_captures_setdefault_value(tmp_path):
    # The "bonus that matters": setdefault's 2nd arg is both the var's default
    # AND the correct value to bake into the image — the same value gold's
    # winning Dockerfile hand-writes (DJANGO_SETTINGS_MODULE=settings).
    _write(tmp_path, "manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
    """)
    d = scan_env_defaults(str(tmp_path))
    assert d["DJANGO_SETTINGS_MODULE"] == "settings"


def test_scan_setdefault_requires_environ_owner(tmp_path):
    # A plain dict's .setdefault() is NOT an env-var read (setdefault is an
    # extremely common dict method name) — must not false-positive.
    _write(tmp_path, "app/x.py", """
        d = {}
        d.setdefault('NOT_AN_ENV_VAR', 'x')
    """)
    found = scan_env_reads(str(tmp_path))
    assert "NOT_AN_ENV_VAR" not in found


# ── config_scan._config_node: the CONFIG-node value convention synthesis.py's
# bakeable_config_env already reads (chosen_fix="env:VAR=value" / "env:VAR=?") ──

def test_config_node_encodes_known_value():
    node = _config_node("DJANGO_SETTINGS_MODULE", "settings")
    assert node.chosen_fix == "env:DJANGO_SETTINGS_MODULE=settings"
    assert node.id == "config:DJANGO_SETTINGS_MODULE"
    assert node.name == "DJANGO_SETTINGS_MODULE"


def test_config_node_encodes_unknown_value_as_sentinel():
    node = _config_node("DEBUG", None)
    assert node.chosen_fix == "env:DEBUG=?"


# ---------------------------------------------------------------------------
# Authoritative config-source scanners (MEASURED REGRESSION FIX).
# ---------------------------------------------------------------------------

def test_scan_tox_setenv_reads_base_testenv_block(tmp_path):
    _write(tmp_path, "tox.ini", """
        [tox]
        envlist = py311

        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings
            OTHER_VAR = other-value
        commands = pytest
    """)
    d = scan_tox_setenv(str(tmp_path))
    assert d["DJANGO_SETTINGS_MODULE"] == "tests.settings"
    assert d["OTHER_VAR"] == "other-value"


def test_scan_tox_setenv_base_wins_over_testenv_specific_section(tmp_path):
    """Per-interpreter `[testenv:*]` sections conventionally REPEAT the base
    value; the base section wins, and a `testenv:*`-only var is still picked up
    when the base doesn't define it."""
    _write(tmp_path, "tox.ini", """
        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings

        [testenv:py311]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings
            PY311_ONLY = yes
    """)
    d = scan_tox_setenv(str(tmp_path))
    assert d["DJANGO_SETTINGS_MODULE"] == "tests.settings"
    assert d["PY311_ONLY"] == "yes"


def test_scan_tox_setenv_missing_file_returns_empty(tmp_path):
    assert scan_tox_setenv(str(tmp_path)) == {}


def test_scan_pytest_ini_reads_pytest_section(tmp_path):
    _write(tmp_path, "pytest.ini", """
        [pytest]
        DJANGO_SETTINGS_MODULE = tests.settings
    """)
    assert scan_pytest_ini(str(tmp_path))["DJANGO_SETTINGS_MODULE"] == "tests.settings"


def test_scan_setup_cfg_pytest_reads_tool_pytest_section(tmp_path):
    _write(tmp_path, "setup.cfg", """
        [tool:pytest]
        DJANGO_SETTINGS_MODULE = tests.settings
    """)
    assert scan_setup_cfg_pytest(str(tmp_path))["DJANGO_SETTINGS_MODULE"] == "tests.settings"


def test_scan_pyproject_pytest_reads_ini_options_table(tmp_path):
    _write(tmp_path, "pyproject.toml", """
        [tool.pytest.ini_options]
        DJANGO_SETTINGS_MODULE = "tests.settings"
        addopts = ["-v"]
        timeout = 30
    """)
    d = scan_pyproject_pytest(str(tmp_path))
    assert d["DJANGO_SETTINGS_MODULE"] == "tests.settings"
    assert d["timeout"] == "30"
    assert "addopts" not in d          # list-shaped value, never stringified


def test_scan_authoritative_config_merges_all_four_sources(tmp_path):
    _write(tmp_path, "tox.ini", """
        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings
    """)
    _write(tmp_path, "pyproject.toml", """
        [tool.pytest.ini_options]
        FLASK_APP = "myapp.wsgi"
    """)
    d = scan_authoritative_config(str(tmp_path))
    assert d["DJANGO_SETTINGS_MODULE"] == "tests.settings"
    assert d["FLASK_APP"] == "myapp.wsgi"


def test_scan_authoritative_config_drops_a_var_two_sources_disagree_on(tmp_path):
    _write(tmp_path, "tox.ini", """
        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tox.settings
    """)
    _write(tmp_path, "pytest.ini", """
        [pytest]
        DJANGO_SETTINGS_MODULE = pytest.settings
    """)
    assert "DJANGO_SETTINGS_MODULE" not in scan_authoritative_config(str(tmp_path))
    assert "DJANGO_SETTINGS_MODULE" in authoritative_ambiguous_vars(str(tmp_path))


def test_scan_authoritative_config_same_value_across_sources_is_not_ambiguous(tmp_path):
    _write(tmp_path, "tox.ini", """
        [testenv]
        setenv =
            DJANGO_SETTINGS_MODULE = tests.settings
    """)
    _write(tmp_path, "pytest.ini", """
        [pytest]
        DJANGO_SETTINGS_MODULE = tests.settings
    """)
    assert scan_authoritative_config(str(tmp_path))["DJANGO_SETTINGS_MODULE"] == "tests.settings"
    assert not authoritative_ambiguous_vars(str(tmp_path))


def test_scan_env_defaults_excludes_vendored_tests_app_directory(tmp_path):
    """Regression guard at the unit level: `tests/app/` is a vendored-example-
    app path, so a `setdefault` default found only there must never surface
    from the last-resort `scan_env_defaults` fallback."""
    _write(tmp_path, "tests/app/idp/manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'idp.settings')
    """)
    assert "DJANGO_SETTINGS_MODULE" not in scan_env_defaults(str(tmp_path))


def test_scan_env_defaults_still_reads_a_first_party_tests_conftest(tmp_path):
    """`tests/conftest.py` (bare `tests/`, not `tests/app/`) is first-party and
    must still be scanned -- the vendored-fixture exclusion is compound-path
    (`tests/app`, `tests/fixtures`), never a bare `tests` segment."""
    _write(tmp_path, "tests/conftest.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    assert scan_env_defaults(str(tmp_path))["DJANGO_SETTINGS_MODULE"] == "proj.settings"
