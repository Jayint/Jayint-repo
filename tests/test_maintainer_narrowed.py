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


def test_resolved_drops_listed_problem():
    m = _map(open_problems=(OpenProblem("pg_config not found", "x", "system"),))
    text = '```json\n{"open_problems": [], "resolved": ["pg_config not found"], "notes": []}\n```'
    out = parse_v1_maintainer_reply(text, m, _report())
    assert out.open_problems == ()


def test_appends_new_problem_and_note():
    m = _map()
    text = '```json\n{"open_problems": [{"signature":"E1","interpretation":"i","layer":"deps"}], "resolved": [], "notes": ["careful"]}\n```'
    out = parse_v1_maintainer_reply(text, m, _report())
    assert out.open_problems[0].signature == "E1"
    assert "careful" in out.notes


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
# Phase 5b — parse-level reclassify: collection signature forced to tests layer
# ---------------------------------------------------------------------------

def test_parse_reclassifies_tests_module_not_found_from_deps_to_tests():
    """An LLM reply with kind=language_package_missing but a tests.* signature
    must be reclassified to layer='tests' at parse time."""
    m = _map()
    problem = {
        "signature": "ModuleNotFoundError: No module named 'tests.test_x'",
        "kind": "language_package_missing",
        "hypothesis": "wrong import path",
        "root_or_downstream": "root",
    }
    text = "```json\n" + json.dumps({
        "open_problems": [problem],
        "resolved": [],
        "planner_notes": [],
    }) + "\n```"
    out = parse_v1_maintainer_reply(text, m, _report())
    assert len(out.open_problems) == 1
    assert out.open_problems[0].layer == "tests", (
        f"Expected layer='tests' but got {out.open_problems[0].layer!r}"
    )


def test_parse_does_NOT_reclassify_genuine_dep_signature():
    """A genuine third-party dep (fastapi) with kind=language_package_missing
    must remain on layer='deps' — no false reclassification."""
    m = _map()
    problem = {
        "signature": "ModuleNotFoundError: No module named 'fastapi'",
        "kind": "language_package_missing",
        "hypothesis": "fastapi not installed",
        "root_or_downstream": "root",
    }
    text = "```json\n" + json.dumps({
        "open_problems": [problem],
        "resolved": [],
        "planner_notes": [],
    }) + "\n```"
    out = parse_v1_maintainer_reply(text, m, _report())
    assert len(out.open_problems) == 1
    assert out.open_problems[0].layer == "deps", (
        f"Expected layer='deps' but got {out.open_problems[0].layer!r}"
    )


# ---------------------------------------------------------------------------
# Phase 5b — MAINTAINER_SYSTEM_PROMPT guidance checks
# ---------------------------------------------------------------------------

def test_prompt_mentions_collection_errors_are_test_failure():
    """Prompt must distinguish pytest collection/import-mode errors from deps."""
    low = MAINTAINER_SYSTEM_PROMPT.lower()
    # Must mention that collection errors (import file mismatch / tests.*) are test_failure
    assert (
        "import file mismatch" in low
        or "collection" in low
    ), "Prompt should mention collection errors"


def test_prompt_mentions_no_module_named_tests_is_not_deps():
    """Prompt must call out that 'No module named tests...' is test_failure, not deps."""
    assert (
        "no module named 'tests" in MAINTAINER_SYSTEM_PROMPT.lower()
        or "tests." in MAINTAINER_SYSTEM_PROMPT.lower()
    ), "Prompt should mention tests.* ModuleNotFoundError classification"


def test_prompt_mentions_collapsing_duplicate_problems():
    """Prompt must instruct the LLM to collapse/dedup problems sharing one mechanism."""
    low = MAINTAINER_SYSTEM_PROMPT.lower()
    assert (
        "collapse" in low
        or "dedup" in low
        or "duplicate" in low
        or "single entry" in low
        or "one entry" in low
    ), "Prompt should instruct collapsing duplicate/near-duplicate problems"


def test_prompt_mentions_stale_problem_pruning():
    """Prompt must instruct the LLM to include resolved when a later rc=0 shows
    the problem no longer occurs (prune stale entries)."""
    low = MAINTAINER_SYSTEM_PROMPT.lower()
    assert (
        "stale" in low
        or "no longer" in low
        or "rc=0" in low
        or "rc==0" in low
        or "resolved" in low
    ), "Prompt should mention pruning stale/resolved problems"


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
