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


# ── Task 3 (B1 residual a): scan_env_defaults_provenance splits the code-scan
# fallback into rung-3a (os.environ.setdefault -> `code_scan_setdefault`,
# bake-eligible) vs rung-3b (os.environ.get/getenv fallback -> `code_scan_fallback`,
# advisory-only). scan_env_defaults stays a thin {var: value} projection of it. ──

def test_scan_env_defaults_provenance_tags_setdefault_as_setdefault(tmp_path):
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DJANGO_SETTINGS_MODULE"] == ("proj.settings", "code_scan_setdefault")


def test_scan_env_defaults_provenance_tags_get_fallback_as_fallback(tmp_path):
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/config.py", """
        import os
        BROKER = os.environ.get('CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
        FLAG = os.getenv('FEATURE_FLAG', 'on')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["CELERY_BROKER_URL"] == ("redis://127.0.0.1:6379/0", "code_scan_fallback")
    assert prov["FEATURE_FLAG"] == ("on", "code_scan_fallback")


def test_scan_env_defaults_provenance_setdefault_wins_over_matching_fallback(tmp_path):
    # Same value written by a setdefault entrypoint AND read via a get-fallback:
    # the env-WRITING setdefault is what the code puts in the environment, so the
    # bake-eligible `code_scan_setdefault` tag wins (single agreed value -> kept).
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    _write(tmp_path, "app/settings.py", """
        import os
        MOD = os.environ.get('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DJANGO_SETTINGS_MODULE"] == ("proj.settings", "code_scan_setdefault")


def test_settings_environ_setdefault_is_not_3a_bake_eligible(tmp_path):
    # SAFETY: `settings.environ.setdefault(...)` does NOT genuinely resolve to
    # os.environ (the receiver base is `settings`, not the `os` module), so it
    # must classify as advisory 3b `code_scan_fallback`, never bake-eligible 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/conf.py", """
        import settings
        settings.environ.setdefault('DJANGO_SETTINGS_MODULE', 'evil.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DJANGO_SETTINGS_MODULE"] == ("evil.settings", "code_scan_fallback")


def test_from_os_import_environ_alias_setdefault_is_3a(tmp_path):
    # FALSE-NEGATIVE FIX: `from os import environ as env; env.setdefault(...)`
    # genuinely resolves to os.environ and must be bake-eligible 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "wsgi.py", """
        from os import environ as env
        env.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DJANGO_SETTINGS_MODULE"] == ("proj.settings", "code_scan_setdefault")


def test_from_os_import_getenv_alias_is_detected_and_3b(tmp_path):
    # FALSE-NEGATIVE FIX: a bare `getenv` bound via `from os import getenv` is a
    # real env read (previously missed); getenv is a read-time default -> 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/x.py", """
        from os import getenv
        FLAG = getenv('FEATURE_FLAG', 'on')
    """)
    assert "FEATURE_FLAG" in scan_env_reads(str(tmp_path))
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["FEATURE_FLAG"] == ("on", "code_scan_fallback")


def test_unresolvable_environ_receiver_setdefault_falls_closed_to_3b(tmp_path):
    # A bare `environ` name NOT bound from os (no `from os import environ`) is
    # unresolvable -> fail-closed to advisory 3b, never 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/y.py", """
        environ = get_some_other_mapping()
        environ.setdefault('DJANGO_SETTINGS_MODULE', 'x.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    # detected (broad, advisory) but never bake-eligible
    assert prov.get("DJANGO_SETTINGS_MODULE") == ("x.settings", "code_scan_fallback")


def test_rebound_os_name_setdefault_falls_closed_to_3b(tmp_path):
    # `import os` then `os = object()` -- the name `os` is REBOUND, so
    # `os.environ.setdefault` can no longer be proven to hit the real os.environ;
    # detected (advisory) but fail-closed to 3b, never bake-eligible 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/a.py", """
        import os
        os = object()
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DJANGO_SETTINGS_MODULE"] == ("proj.settings", "code_scan_fallback")


def test_function_param_named_os_setdefault_falls_closed_to_3b(tmp_path):
    # A function parameter named `os` shadows any module `os`; the receiver is
    # unresolvable -> never 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/b.py", """
        import os
        def f(os):
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("DJANGO_SETTINGS_MODULE") != ("proj.settings", "code_scan_setdefault")


def test_rebound_environ_alias_setdefault_is_not_3a(tmp_path):
    # `from os import environ as env` then `env = {}` -- the alias is rebound to a
    # plain dict, so `env.setdefault` must NOT be bake-eligible 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/c.py", """
        from os import environ as env
        env = {}
        env.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("DJANGO_SETTINGS_MODULE") != ("proj.settings", "code_scan_setdefault")


def test_walrus_rebound_os_setdefault_is_not_3a(tmp_path):
    # `os := fake` (walrus) rebinds `os` at module level -> receiver unresolvable.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/w.py", """
        import os
        if (os := object()):
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("DJANGO_SETTINGS_MODULE") != ("proj.settings", "code_scan_setdefault")


def test_match_capture_rebound_os_setdefault_is_not_3a(tmp_path):
    # A structural-pattern capture `case {"os": os}` rebinds `os` -> not 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/m.py", """
        import os
        def handle(payload):
            match payload:
                case {"os": os}:
                    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("DJANGO_SETTINGS_MODULE") != ("proj.settings", "code_scan_setdefault")


def test_shadowing_import_as_os_setdefault_is_not_3a(tmp_path):
    # `import fake as os` shadows the genuine `import os` regardless of order.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/s.py", """
        import os
        import collections as os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("DJANGO_SETTINGS_MODULE") != ("proj.settings", "code_scan_setdefault")


def test_from_x_import_environ_shadow_setdefault_is_not_3a(tmp_path):
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/s2.py", """
        from collections import OrderedDict as environ
        environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("DJANGO_SETTINGS_MODULE") != ("proj.settings", "code_scan_setdefault")


def test_unrelated_os_param_does_not_demote_genuine_toplevel_setdefault(tmp_path):
    # THE FALSE-NEGATIVE FIX: a genuine module-level `os` + a call inside `main`
    # stays 3a even though an UNRELATED `helper(os)` has an `os` parameter -- local
    # bindings poison only their own function subtree, not sibling scopes.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/main.py", """
        import os

        def main():
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')

        def helper(os):
            return os
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DJANGO_SETTINGS_MODULE"] == ("proj.settings", "code_scan_setdefault")


def test_local_os_param_shadows_at_the_call_site(tmp_path):
    # `def main(os): os.environ.setdefault(...)` -- the param shadows os locally.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/main2.py", """
        import os
        def main(os):
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("DJANGO_SETTINGS_MODULE") != ("proj.settings", "code_scan_setdefault")


def test_global_os_assignment_poisons_module_level_setdefault(tmp_path):
    # `global os` + assignment inside a function escapes to module scope -> the
    # module-level setdefault must fail closed to advisory.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "app/g.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')

        def reconfigure():
            global os
            os = object()
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("DJANGO_SETTINGS_MODULE") != ("proj.settings", "code_scan_setdefault")


def test_canonical_django_manage_py_stays_3a(tmp_path):
    # The canonical entrypoint shape must remain bake-eligible 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "manage.py", """
        import os
        def main():
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
        if __name__ == '__main__':
            main()
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DJANGO_SETTINGS_MODULE"] == ("proj.settings", "code_scan_setdefault")


# ── Round 4: POLARITY INVERSION -- unknown binding constructs demote to 3b.
# The analyzer models an explicit handled-set; every other construct that can
# bind/mutate a tracked name (function-local imports, star imports, del, TypeAlias,
# os.environ reassignment) demotes the receiver rather than defaulting it genuine. ──

def test_func_local_from_os_import_environ_as_os_is_not_3a(tmp_path):
    # HOLE #1: `import os` module-wide + a function-local `from os import environ
    # as os` rebinds `os` to a MAPPING inside f; `os.environ.setdefault` there is
    # not the genuine os.environ -> must demote to advisory 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "shadow.py", """
        import os
        def f():
            from os import environ as os
            os.environ.setdefault('X_VAR', 'shadow.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["X_VAR"] == ("shadow.settings", "code_scan_fallback")


def test_func_local_import_os_is_locally_genuine_3a(tmp_path):
    # HOLE #1 (other direction): a genuine import INSIDE a function makes the name
    # genuine WITHIN that subtree -- with no module-level `import os` at all, the
    # function-local `import os` is the real module -> stays bake-eligible 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "localimp.py", """
        def f():
            import os
            os.environ.setdefault('LOCAL_VAR', 'local.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["LOCAL_VAR"] == ("local.settings", "code_scan_setdefault")


def test_module_setdefault_before_import_is_not_3a(tmp_path):
    # HOLE #1 (ordering): a MODULE-level call site lexically BEFORE the first
    # module-level genuine import cannot see it yet -> unresolved -> 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "early.py", """
        os.environ.setdefault('EARLY_VAR', 'early.settings')
        import os
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["EARLY_VAR"] == ("early.settings", "code_scan_fallback")


def test_setdefault_in_def_below_toplevel_import_stays_3a(tmp_path):
    # The ordering guard is MODULE-level only: a call inside a def runs after
    # module import time, so a def below a top-of-file `import os` stays 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "late.py", """
        import os
        def main():
            os.environ.setdefault('LATE_VAR', 'late.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["LATE_VAR"] == ("late.settings", "code_scan_setdefault")


def test_setdefault_in_def_above_bottom_import_stays_3a(tmp_path):
    # A def defined ABOVE a bottom-of-file `import os` still sees the genuine os
    # at call time (function bodies run after import) -> 3a, no ordering demote.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "bottom.py", """
        def main():
            os.environ.setdefault('BOTTOM_VAR', 'bottom.settings')
        import os
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["BOTTOM_VAR"] == ("bottom.settings", "code_scan_setdefault")


def test_star_import_demotes_module_setdefault_to_3b(tmp_path):
    # HOLE #2: `from fake import *` may shadow `os`; a star import poisons all
    # tracked names FILE-WIDE -> the setdefault demotes to advisory 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "star.py", """
        import os
        from fake import *
        os.environ.setdefault('STAR_VAR', 'star.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["STAR_VAR"] == ("star.settings", "code_scan_fallback")


def test_func_del_os_demotes_to_3b(tmp_path):
    # HOLE #3: `del os` unbinds the name; poison it file-wide -> 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "delos.py", """
        import os
        def f():
            del os
            os.environ.setdefault('DEL_VAR', 'del.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DEL_VAR"] == ("del.settings", "code_scan_fallback")


def test_type_alias_shadowing_os_demotes_to_3b(tmp_path):
    # HOLE #3: `type os = ...` (PEP 695, 3.12+) rebinds `os` -> poison file-wide.
    import sys
    if sys.version_info < (3, 12):
        import pytest
        pytest.skip("ast.TypeAlias requires Python 3.12+")
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "typealias.py", """
        import os
        type os = int
        os.environ.setdefault('TYPE_VAR', 'type.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["TYPE_VAR"] == ("type.settings", "code_scan_fallback")


def test_environ_attribute_reassigned_demotes_to_3b(tmp_path):
    # HOLE #4: `os.environ = {}` replaces the mapping; a later `os.environ.set
    # default` operates on the replacement dict -> poison `os` file-wide -> 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "reassign.py", """
        import os
        os.environ = {}
        os.environ.setdefault('REASSIGN_VAR', 'reassign.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["REASSIGN_VAR"] == ("reassign.settings", "code_scan_fallback")


def test_round4_django_with_unrelated_os_param_stays_3a(tmp_path):
    # REGRESSION: the canonical Django entrypoint stays bake-eligible 3a even with
    # an unrelated `def helper(os)` whose param shadows os only in its own subtree.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "manage.py", """
        import os

        def main():
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')

        def helper(os):
            return os

        if __name__ == '__main__':
            main()
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DJANGO_SETTINGS_MODULE"] == ("myproj.settings", "code_scan_setdefault")


# ── Round 5: three residual class-closing holes (Codex round 5). ──
#  1. conflicting genuine imports of DIFFERENT kinds onto one name -> poison.
#  2. class scopes do not flow bindings into methods (Python scoping).
#  3. escape analysis: a genuine name leaking out of its receiver slot -> poison. ──

def test_conflicting_genuine_import_kinds_onto_one_name_is_not_3a(tmp_path):
    # HOLE #1: `import os as x` (module) + `from os import environ as x` binds `x`
    # to the module AND to the environ mapping -- `x` is neither, `x.environ` is
    # nonsense -> poison in-scope -> 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "conflict.py", """
        import os as x
        from os import environ as x
        x.environ.setdefault('CONFLICT_VAR', 'conflict.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("CONFLICT_VAR") != ("conflict.settings", "code_scan_setdefault")


def test_duplicate_same_kind_import_os_stays_3a(tmp_path):
    # HOLE #1 (other direction): two identical `import os` are a same-kind
    # duplicate, NOT a conflict -> stays genuine 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "dup.py", """
        import os
        import os
        os.environ.setdefault('DUP_VAR', 'dup.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["DUP_VAR"] == ("dup.settings", "code_scan_setdefault")


def test_class_body_import_does_not_reach_method_is_not_3a(tmp_path):
    # HOLE #2: a class-body `import os` does NOT flow into a method body (Python
    # methods do not close over the class namespace) -> with no module-level
    # `import os`, the method receiver is unresolved -> 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "clsmethod.py", """
        class C:
            import os
            def f(self):
                os.environ.setdefault('CLS_VAR', 'cls.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("CLS_VAR") != ("cls.settings", "code_scan_setdefault")


def test_class_body_import_plus_module_import_method_resolves_via_module_3a(tmp_path):
    # HOLE #2 (semantics): the method resolves to MODULE scope (class binding is
    # skipped, not a file-wide demote) -> a module-level `import os` keeps it 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "clsmod.py", """
        import os
        class C:
            import os
            def f(self):
                os.environ.setdefault('CLSMOD_VAR', 'clsmod.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["CLSMOD_VAR"] == ("clsmod.settings", "code_scan_setdefault")


def test_class_body_direct_call_resolves_via_class_binding_3a(tmp_path):
    # HOLE #2 (direct-body): a call written DIRECTLY in the class body DOES see the
    # class-body binding -> 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "clsdirect.py", """
        class C:
            import os
            os.environ.setdefault('CLSDIRECT_VAR', 'clsdirect.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["CLSDIRECT_VAR"] == ("clsdirect.settings", "code_scan_setdefault")


def test_setattr_on_os_environ_demotes_to_3b(tmp_path):
    # HOLE #3: `setattr(os, 'environ', {})` passes the module object to code that
    # mutates it; `os` appears as a call ARGUMENT (not a receiver) -> escape -> 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "setattr.py", """
        import os
        setattr(os, 'environ', {})
        os.environ.setdefault('SETATTR_VAR', 'setattr.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("SETATTR_VAR") != ("setattr.settings", "code_scan_setdefault")


def test_os_passed_as_call_argument_demotes_to_3b(tmp_path):
    # HOLE #3 (general escape): passing the module object into ANY call
    # (`configure(os)`) could mutate it -> escape -> 3b.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "configure.py", """
        import os
        configure(os)
        os.environ.setdefault('CONFIGURE_VAR', 'configure.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov.get("CONFIGURE_VAR") != ("configure.settings", "code_scan_setdefault")


def test_os_attribute_access_is_not_an_escape_stays_3a(tmp_path):
    # HOLE #3 (allowed): plain attribute access (`os.path.join(...)`) is a receiver
    # position, NOT an escape -> the setdefault stays 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "attraccess.py", """
        import os
        print(os.path.join('a', 'b'))
        os.environ.setdefault('ATTR_VAR', 'attr.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["ATTR_VAR"] == ("attr.settings", "code_scan_setdefault")


def test_environ_alias_subscript_is_allowed_stays_3a(tmp_path):
    # HOLE #3 (environ-kind): an environ alias may be subscripted (`env['X']`) as
    # well as attribute-accessed (`env.setdefault`) -> both are receiver positions,
    # no escape -> 3a.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "envsub.py", """
        from os import environ as env
        env['SUB_READ']
        env.setdefault('SUB_VAR', 'sub.settings')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert prov["SUB_VAR"] == ("sub.settings", "code_scan_setdefault")


def test_scan_env_defaults_is_a_value_projection_of_provenance(tmp_path):
    # scan_env_defaults must stay byte-identical to "just the values" of the
    # provenance scan -- existing callers (_dsn_configs) depend on the plain shape.
    from graph.python.config_scan import scan_env_defaults_provenance
    _write(tmp_path, "manage.py", """
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')
    """)
    _write(tmp_path, "app/config.py", """
        import os
        FLAG = os.getenv('FEATURE_FLAG', 'on')
    """)
    prov = scan_env_defaults_provenance(str(tmp_path))
    assert scan_env_defaults(str(tmp_path)) == {v: val for v, (val, _s) in prov.items()}


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
