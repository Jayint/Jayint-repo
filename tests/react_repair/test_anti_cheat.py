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
