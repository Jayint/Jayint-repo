import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.observe import compact_pytest_blocks

# The real thing, from the live itsdangerous run: 5 test modules, ONE cause, 62 lines.
FIVE_COLLECTION_ERRORS = """\
==================================== ERRORS ====================================
__________ ERROR collecting tests/test_itsdangerous/test_encoding.py ___________
ImportError while importing test module '/app/tests/test_itsdangerous/test_encoding.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/local/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_itsdangerous/test_encoding.py:3: in <module>
    from itsdangerous.encoding import base64_decode
E   ModuleNotFoundError: No module named 'itsdangerous'
__________ ERROR collecting tests/test_itsdangerous/test_signer.py ___________
ImportError while importing test module '/app/tests/test_itsdangerous/test_signer.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/local/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_itsdangerous/test_signer.py:6: in <module>
    from itsdangerous.exc import BadSignature
E   ModuleNotFoundError: No module named 'itsdangerous'
=========================== short test summary info ============================
ERROR tests/test_itsdangerous/test_encoding.py
ERROR tests/test_itsdangerous/test_signer.py
2 errors in 0.06s
"""


def test_collapses_same_cause_blocks_keeping_the_first_verbatim():
    out = compact_pytest_blocks(FIVE_COLLECTION_ERRORS)
    # first block survives with its REAL frame (this is not a synthesized histogram)
    assert "from itsdangerous.encoding import base64_decode" in out
    assert "E   ModuleNotFoundError: No module named 'itsdangerous'" in out
    # the duplicate's body is gone, but the module is named in the roster
    assert "from itsdangerous.exc import BadSignature" not in out
    assert "1 more block, same cause" in out
    assert "tests/test_itsdangerous/test_signer.py" in out

def test_drops_importlib_bootstrap_boilerplate():
    out = compact_pytest_blocks(FIVE_COLLECTION_ERRORS)
    assert "_bootstrap._gcd_import" not in out
    assert "Hint: make sure your test modules" not in out
    assert "importlib/__init__.py:126" not in out
    assert "^^^^^^" not in out                       # the carets belonged to the dropped frame

def test_pytest_own_summary_section_passes_through():
    out = compact_pytest_blocks(FIVE_COLLECTION_ERRORS)
    assert "short test summary info" in out
    assert "2 errors in 0.06s" in out

def test_it_actually_shrinks_the_real_thing():
    out = compact_pytest_blocks(FIVE_COLLECTION_ERRORS)
    assert len(out) < len(FIVE_COLLECTION_ERRORS) * 0.6

def test_idempotent():
    once = compact_pytest_blocks(FIVE_COLLECTION_ERRORS)
    assert compact_pytest_blocks(once) == once


# --- it must NOT merge genuinely different failures ------------------------
TWO_CAUSES = """\
==================================== ERRORS ====================================
__________ ERROR collecting tests/test_a.py ___________
tests/test_a.py:3: in <module>
    import psycopg2
E   ModuleNotFoundError: No module named 'psycopg2'
__________ ERROR collecting tests/test_b.py ___________
tests/test_b.py:4: in <module>
    import redis
E   ModuleNotFoundError: No module named 'redis'
"""


def test_distinct_causes_are_both_kept_in_full():
    out = compact_pytest_blocks(TWO_CAUSES)
    assert "import psycopg2" in out and "import redis" in out
    assert "same cause" not in out                   # nothing to collapse — two real, different bugs

def test_cause_key_ignores_the_differing_file_path():
    # same cause, different modules AND different line numbers → still one group
    text = TWO_CAUSES.replace("import redis\nE   ModuleNotFoundError: No module named 'redis'",
                              "import psycopg2\nE   ModuleNotFoundError: No module named 'psycopg2'")
    out = compact_pytest_blocks(text)
    assert "1 more block, same cause" in out

def test_block_without_an_E_line_is_never_merged():
    text = ("==== ERRORS ====\n"
            "___ ERROR collecting tests/test_a.py ___\nsomething odd happened\n"
            "___ ERROR collecting tests/test_b.py ___\nsomething odd happened\n")
    out = compact_pytest_blocks(text)
    assert out.count("something odd happened") == 2  # unknown cause → keep both, don't guess

def test_no_blocks_is_a_noop():
    for text in ("", "5 passed in 0.1s", "no underscores here"):
        assert compact_pytest_blocks(text) == text

def test_single_block_keeps_content_but_strips_boilerplate():
    text = ("___ ERROR collecting tests/test_a.py ___\n"
            "Hint: make sure your test modules/packages have valid Python names.\n"
            "tests/test_a.py:3: in <module>\n    import psycopg2\n"
            "E   ModuleNotFoundError: No module named 'psycopg2'\n")
    out = compact_pytest_blocks(text)
    assert "import psycopg2" in out and "Hint: make sure" not in out
    assert "same cause" not in out

def test_failures_section_dedups_too():
    text = ("=================================== FAILURES ===================================\n"
            "___ test_one ___\ntests/test_x.py:2: in test_one\nE   AssertionError: assert 1 == 2\n"
            "___ test_two ___\ntests/test_x.py:9: in test_two\nE   AssertionError: assert 1 == 2\n")
    out = compact_pytest_blocks(text)
    assert "1 more block, same cause" in out and "AssertionError" in out
