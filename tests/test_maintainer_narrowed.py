# tests/test_maintainer_narrowed.py
import json
from types import SimpleNamespace
from src.envstate.maintainer import (
    parse_v1_maintainer_reply,
    MAINTAINER_SYSTEM_PROMPT,
    _is_collection_signature,
)
from src.envstate.world_model import (
    initial_map, merge_map, Fact, OpenProblem, CommandRecord, TaskReport,
)


def _map(**kw):
    base = initial_map(base_image="python:3.12", workdir="/app", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, **kw)


def _report(cmds=(), status="done"):
    return TaskReport(task_goal="g", status=status, commands=tuple(cmds), learning="")


def test_does_not_touch_installed_or_progress():
    m = _map(installed=(Fact("flask", "3.0.0"),), progress={"base": True, "system": False,
             "runtime": True, "deps": True, "build": False, "tests": False})
    text = '```json\n{"open_problems": [], "resolved": [], "notes": [], "installed": [{"name":"HACK"}], "progress": {"tests": true}}\n```'
    out = parse_v1_maintainer_reply(text, m, _report())
    assert out.installed == (Fact("flask", "3.0.0"),)   # stray installed ignored
    assert out.progress["tests"] is False                # stray progress ignored


def test_done_flag_fires_on_empty_llm_output():
    m = _map()
    # Must use a real execution command with passed output to trigger done_flag.
    report = _report(cmds=(CommandRecord("python -m pytest -q", 0, "3 passed in 0.5s"),))
    out = parse_v1_maintainer_reply("", m, report)
    assert out.done_flag is True


# ---------------------------------------------------------------------------
# Phase 5b — _is_collection_signature unit tests (RED until implemented)
# ---------------------------------------------------------------------------

def test_is_collection_signature_tests_module_not_found():
    """ModuleNotFoundError for a tests.* sub-package is a topology error → True."""
    assert _is_collection_signature(
        "ModuleNotFoundError: No module named 'tests.test_docx_converter'"
    ) is True


def test_is_collection_signature_import_file_mismatch():
    """'import file mismatch' is a pytest topology error → True."""
    assert _is_collection_signature("import file mismatch") is True


def test_is_collection_signature_error_during_collection():
    """'error during collection' is a collection-phase error → True."""
    assert _is_collection_signature("error during collection") is True


def test_is_collection_signature_collected_0_items():
    """'collected 0 items' means nothing was collected — topology issue → True."""
    assert _is_collection_signature("collected 0 items") is True


def test_is_collection_signature_internalerror():
    """pytest INTERNALERROR is a collection-phase crash → True."""
    assert _is_collection_signature("INTERNALERROR> ...") is True


def test_is_collection_signature_conftest_error():
    """conftest import error is a topology/collection error → True."""
    assert _is_collection_signature("conftest.py:3: in <module>") is True


def test_is_collection_signature_real_dep_fastapi():
    """ModuleNotFoundError for a real third-party package (fastapi) → False."""
    assert _is_collection_signature("ModuleNotFoundError: No module named 'fastapi'") is False


def test_is_collection_signature_real_dep_numpy():
    """ModuleNotFoundError for numpy (ordinary dep) → False."""
    assert _is_collection_signature("ModuleNotFoundError: No module named 'numpy'") is False


def test_is_collection_signature_pg_config():
    """pg_config executable not found is a native build dep, not collection → False."""
    assert _is_collection_signature("pg_config executable not found") is False


def test_is_collection_signature_empty():
    """Empty string → False (no signal)."""
    assert _is_collection_signature("") is False


# ---------------------------------------------------------------------------
# Fix 2 — ANSI-stripping in _shows_execution (Edit B)
# ---------------------------------------------------------------------------

from src.envstate.maintainer import _shows_execution  # noqa: E402


def test_shows_execution_ansi_bold_passed():
    """\x1b[1m5 passed\x1b[0m — ANSI-wrapped count → True."""
    assert _shows_execution("\x1b[1m5 passed\x1b[0m") is True


def test_shows_execution_ansi_green_passed():
    """\x1b[32m182 passed\x1b[0m in 3.21s — green ANSI around count → True."""
    assert _shows_execution("\x1b[32m182 passed\x1b[0m in 3.21s") is True


def test_shows_execution_ran_n_tests():
    """Plain unittest 'Ran 5 tests in 0.1s' → True (no ANSI needed)."""
    assert _shows_execution("Ran 5 tests in 0.1s") is True


def test_shows_execution_collected_only():
    """'collected 5 items' (pure --collect-only) → False."""
    assert _shows_execution("collected 5 items") is False


def test_shows_execution_zero_passed():
    """'0 passed' — N must be >= 1 → False."""
    assert _shows_execution("0 passed") is False


def test_shows_execution_empty_string():
    """Empty string → False."""
    assert _shows_execution("") is False
