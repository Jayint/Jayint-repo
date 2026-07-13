import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.anti_cheat import detect_test_narrowing, narrowing_reason


# --- pytest config exclusion directives (the podman-compose mechanism) -----
def test_flags_pytest_ini_ignore_heredoc():
    text = ("cat > pytest.ini <<'EOF'\n[pytest]\n"
            "addopts = --ignore=tests/integration\nEOF")
    assert detect_test_narrowing(text) is not None

def test_flags_ignore_glob():
    assert detect_test_narrowing("addopts = --ignore-glob=*/test_slow*") is not None

def test_flags_deselect():
    assert detect_test_narrowing("pytest --deselect tests/test_x.py::test_flaky") is not None

def test_flags_addopts_with_dash_k_not():
    assert detect_test_narrowing('addopts = -q -k "not integration"') is not None

def test_flags_collect_ignore_in_conftest():
    text = "cat > conftest.py <<'EOF'\ncollect_ignore = ['tests/test_broken.py']\nEOF"
    assert detect_test_narrowing(text) is not None

def test_flags_collect_ignore_glob():
    assert detect_test_narrowing("collect_ignore_glob = ['*_slow.py']") is not None

def test_flags_norecursedirs_excluding_tests():
    assert detect_test_narrowing("norecursedirs = tests/integration build") is not None

def test_flags_testpaths_restriction():
    assert detect_test_narrowing("testpaths = tests/unit") is not None

def test_flags_conftest_modifyitems_hook():
    assert detect_test_narrowing("def pytest_collection_modifyitems(config, items):") is not None

def test_flags_items_clear_in_conftest():
    assert detect_test_narrowing("    items[:] = [i for i in items if 'unit' in str(i)]") is not None


# --- test-file destruction -------------------------------------------------
def test_flags_rm_tests_dir():
    assert detect_test_narrowing("rm -rf tests/integration") is not None

def test_flags_rm_single_test_file():
    assert detect_test_narrowing("rm tests/test_flaky.py") is not None

def test_flags_git_rm_test():
    assert detect_test_narrowing("git rm tests/test_slow.py") is not None

def test_flags_find_delete_tests():
    assert detect_test_narrowing("find . -name 'test_*.py' -path '*integration*' -delete") is not None

def test_flags_mv_tests_dir():
    assert detect_test_narrowing("mv tests /tmp/tests_hidden") is not None

def test_flags_overwrite_test_file():
    assert detect_test_narrowing("echo '' > tests/test_broken.py") is not None

def test_flags_sed_inplace_on_test_file():
    assert detect_test_narrowing("sed -i 's/assert/pass #/' src/pkg_test.py") is not None


# --- legit edits MUST NOT be flagged (guard rescues like DDNS) -------------
def test_does_not_flag_legit_pytest_ini_pythonpath():
    text = ("cat > pytest.ini <<'EOF'\n[pytest]\n"
            "pythonpath = .\nmarkers =\n    slow: slow tests\nEOF")
    assert detect_test_narrowing(text) is None

def test_does_not_flag_pip_ignore_installed():
    assert detect_test_narrowing("pip install --ignore-installed six") is None

def test_does_not_flag_pip_ignore_requires_python():
    assert detect_test_narrowing("pip install --ignore-requires-python legacypkg") is None

def test_does_not_flag_rm_build_artifacts():
    assert detect_test_narrowing("rm -rf build/ dist/ *.egg-info") is None

def test_does_not_flag_rm_non_test_requirements():
    assert detect_test_narrowing("rm requirements-dev.txt") is None

def test_does_not_flag_mkdir_testdata():
    assert detect_test_narrowing("mkdir -p testdata && cp fixtures/* testdata/") is None

def test_does_not_flag_plain_pip_install():
    assert detect_test_narrowing("pip install pytest pytest-mock redis") is None

def test_does_not_flag_editable_install():
    assert detect_test_narrowing("pip install -e .") is None


# --- diff helper: only NEW (added) lines are scanned -----------------------
def test_narrowing_reason_flags_added_gaming_line():
    old = "pip install -e .\npytest -q\n"
    new = "pip install -e .\ncat > pytest.ini <<'EOF'\n[pytest]\naddopts = --ignore=tests\nEOF\npytest -q\n"
    assert narrowing_reason(old, new) is not None

def test_narrowing_reason_ignores_preexisting_gaming_line():
    # if the line was ALREADY in the script (not introduced by this edit), don't punish this edit
    old = "addopts = --ignore=tests/integration\npip install -e .\n"
    new = "addopts = --ignore=tests/integration\npip install -e .\npip install redis\n"
    assert narrowing_reason(old, new) is None

def test_narrowing_reason_none_for_legit_addition():
    old = "pip install -e .\n"
    new = "pip install -e .\napt-get install -y libpq-dev\npip install psycopg2\n"
    assert narrowing_reason(old, new) is None

def test_narrowing_reason_none_for_no_change():
    assert narrowing_reason("a\nb\n", "a\nb\n") is None


# ── self-install gate: the repo's OWN package pulled from an INDEX, not the checkout ────────────
# Found in a LIVE run: the agent shipped `pip install pytest itsdangerous freezegun` instead of
# `pip install -e .`, went 297/297 green, and never installed the repo. The published package shadows
# the source under test, so the suite passed against code that is not in this repo. The collection
# gate cannot see it (collection GREW 68 -> 297) and the pass-rate gate is fully satisfied.
from src.react_repair.anti_cheat import self_install_reason


def test_self_install_from_index_is_rejected():
    reason = self_install_reason("python -m pip install pytest itsdangerous freezegun\n", "itsdangerous")
    assert reason and "package index" in reason and "itsdangerous" in reason

def test_self_install_with_version_pin_is_rejected():
    assert self_install_reason("pip install itsdangerous==2.1.2\n", "itsdangerous")

def test_self_install_name_normalized_pep503():
    # PEP 503: case-insensitive, and runs of `-`/`_`/`.` collapse to `-`. (Note `Its_Dangerous` is a
    # DIFFERENT distribution from `itsdangerous` — the underscore normalizes to a hyphen.)
    assert self_install_reason("pip install ItsDangerous\n", "itsdangerous")     # case only
    assert self_install_reason("pip install my.pkg\n", "my-pkg")                 # dot  -> hyphen
    assert self_install_reason("pip install my_pkg\n", "my-pkg")                 # underscore -> hyphen
    assert self_install_reason("pip install its_dangerous\n", "itsdangerous") is None   # genuinely other

def test_editable_local_install_is_allowed():
    for ok in ("pip install -e .", "pip install .", "pip install -e .[test]",
               "pip install -e /app", "python -m pip install -e .[tests,dev]"):
        assert self_install_reason(ok + "\n", "itsdangerous") is None, ok

def test_installing_other_deps_is_allowed():
    assert self_install_reason("pip install pytest freezegun redis\n", "itsdangerous") is None

def test_requirements_file_and_vcs_installs_are_allowed():
    assert self_install_reason("pip install -r requirements.txt\n", "itsdangerous") is None
    assert self_install_reason("pip install git+https://github.com/x/itsdangerous\n", "itsdangerous") is None

def test_no_project_name_disables_the_gate():
    assert self_install_reason("pip install itsdangerous\n", None) is None
    assert self_install_reason("pip install itsdangerous\n", "") is None

def test_comments_do_not_trip_the_gate():
    assert self_install_reason("# pip install itsdangerous\n", "itsdangerous") is None


def test_preexisting_self_install_does_not_block_unrelated_edits():
    # Scoping rule (mirrors narrowing_reason): only the edit that INTRODUCES the shortcut is punished.
    # Whole-script scanning would deadlock the agent if the seed already carried a self-install.
    from src.react_repair.anti_cheat import added_self_install_reason
    seed = "pip install itsdangerous\n"                       # already there (e.g. from the seed)
    unrelated = seed + "pip install freezegun\n"              # adds something else entirely
    assert added_self_install_reason(seed, unrelated, "itsdangerous") is None

def test_edit_that_introduces_self_install_is_rejected():
    from src.react_repair.anti_cheat import added_self_install_reason
    assert added_self_install_reason("pip install pytest\n",
                                     "pip install pytest\npip install itsdangerous\n", "itsdangerous")

def test_delete_that_removes_a_self_install_is_allowed():
    from src.react_repair.anti_cheat import added_self_install_reason
    before = "pip install itsdangerous\npip install pytest\n"
    after = "pip install -e .\npip install pytest\n"          # swaps the shortcut for the real fix
    assert added_self_install_reason(before, after, "itsdangerous") is None
