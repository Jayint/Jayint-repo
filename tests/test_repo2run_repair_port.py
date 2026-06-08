"""TDD tests for repo2run_repair_port.py — Stage A + Stage B.

Stage A (module skeleton + import isolation):
- test_no_suspect_imports: import isolation check (no src.recipe_repair / src.artifact_verify)
- test_module_imports_cleanly: module-level smoke
- test_key_symbols_present: spot-check that the verbatim symbols were copied
- test_symbol_count: rough lower-bound check on exported symbols

Stage B (glue parsers):
- junit_to_pytest_results: schema parity, error_breakdown buckets, regex fallback (RISK-2),
  timeout shortcut, on-disk golden fixture validation
- real_test_command: strip --collect-only, preserve poetry/uv prefix, append --junitxml,
  fallback to default when nothing runnable, no double-add of --junitxml
"""
from __future__ import annotations

import importlib
import json
import sys
import textwrap
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / golden fixture
# ---------------------------------------------------------------------------

GOLDEN_FIXTURE_PATH = (
    Path(__file__).parent.parent
    / "rat_run/output/resend/resend-python/run_pytest_results.json"
)

# Expected top-level keys from the real framework (scorers read these)
EXPECTED_TOP_KEYS = {
    "summary",
    "error_breakdown",
    "failed_tests",
    "error_tests",
    "raw_output",
    "returncode",
    "parse_method",
}

EXPECTED_SUMMARY_KEYS = {
    "total_tests",
    "passed",
    "failed",
    "errors",
    "skipped",
    "xfailed",
    "xpassed",
}


def _make_junit_xml(testcases: list[dict]) -> str:
    """Build a minimal JUnit XML string for testing.

    Each testcase dict: {classname, name, failure_msg (optional), error_msg (optional)}
    """
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    total = len(testcases)
    failures = sum(1 for t in testcases if "failure_msg" in t)
    errors = sum(1 for t in testcases if "error_msg" in t)
    lines.append(
        f'<testsuite name="pytest" tests="{total}" failures="{failures}" errors="{errors}" skipped="0">'
    )
    for tc in testcases:
        cn = tc.get("classname", "tests.test_foo")
        nm = tc.get("name", "test_something")
        lines.append(f'  <testcase classname="{cn}" name="{nm}" time="0.001">')
        if "failure_msg" in tc:
            msg = tc["failure_msg"].replace('"', "&quot;").replace("<", "&lt;")
            lines.append(f'    <failure message="{msg}">{msg}</failure>')
        if "error_msg" in tc:
            msg = tc["error_msg"].replace('"', "&quot;").replace("<", "&lt;")
            lines.append(f'    <error message="{msg}">{msg}</error>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 0 — Module skeleton + import isolation
# ---------------------------------------------------------------------------


def test_module_imports_cleanly():
    """The module must import without error from a clean sys.modules state."""
    # Force a fresh import to catch any top-level errors
    if "repo2run_repair_port" in sys.modules:
        del sys.modules["repo2run_repair_port"]
    mod = importlib.import_module("repo2run_repair_port")
    assert mod is not None


def test_no_suspect_imports():
    """After importing repo2run_repair_port, neither src.recipe_repair nor
    src.artifact_verify should appear in sys.modules.  The forbidden modules
    must not be pulled in transitively.
    """
    # Ensure the module is imported
    if "repo2run_repair_port" in sys.modules:
        del sys.modules["repo2run_repair_port"]
    # Also purge any cached forbidden modules to get a clean read
    for key in list(sys.modules.keys()):
        if key in ("src.recipe_repair", "src.artifact_verify"):
            del sys.modules[key]

    importlib.import_module("repo2run_repair_port")

    assert "src.recipe_repair" not in sys.modules, (
        "repo2run_repair_port must not import src.recipe_repair"
    )
    assert "src.artifact_verify" not in sys.modules, (
        "repo2run_repair_port must not import src.artifact_verify"
    )


def test_key_constants_present():
    """Spot-check a selection of the 17 constants that must be copied verbatim."""
    import repo2run_repair_port as m

    assert m.DOCKER_TIMEOUT_EXIT_CODE == 124
    assert m.OBSERVED_PIP_CONSTRAINTS_PATH == "/tmp/jayint-pip-constraints.txt"
    assert m.PYTORCH_CPU_INDEX_URL == "https://download.pytorch.org/whl/cpu"
    assert m.DOCKERFILE_REPAIR_LOG_LIMIT == 12000
    assert isinstance(m.DOCKERFILE_REPAIR_SYSTEM_PROMPT, str)
    # Rule 5 verbatim text from run_repo2run_benchmark.py:64
    assert "restoring omitted successful setup commands" in m.DOCKERFILE_REPAIR_SYSTEM_PROMPT
    assert isinstance(m.DOCKERFILE_REPAIR_USER_PROMPT, str)
    assert isinstance(m.TEST_EXECUTION_SHELL_WRAPPER, str)
    assert isinstance(m.UNSAFE_COLLECT_COMMAND_SUBSTRINGS, tuple)
    assert isinstance(m.DISALLOWED_COLLECT_TOKENS, set)
    assert isinstance(m._SHELL_CONTROL_TOKENS, set)


def test_key_functions_present():
    """All 54+ verbatim symbols must be callable or present."""
    import repo2run_repair_port as m

    verbatim_callables = [
        "_decode_command_stream",
        "normalize_command_list",
        "write_text",
        "_strip_requirement_line",
        "_normalize_pip_constraint_name",
        "_pip_requirement_name",
        "_split_pip_install_command",
        "_pip_installed_requirement_names",
        "_normalize_dockerfile_path_value",
        "_parse_dockerfile_env_instruction",
        "_expand_dockerfile_variables",
        "_normalize_dockerfile_workdir",
        "infer_workdir_from_dockerfile",
        "_pip_install_command_has_constraint",
        "_pip_install_command_needs_observed_constraints",
        "_add_observed_constraints_to_pip_command",
        "_is_bare_pip_install_command",
        "_is_bare_uv_pip_install_command",
        "_is_uv_shell_installer_command",
        "_is_generated_uv_pip_retry_command",
        "_is_apt_install_replay_command",
        "_extract_generated_pip_retry_inner_command",
        "_extract_generated_apt_retry_inner_command",
        "_extract_generated_retry_inner_shell_command",
        "_iter_pip_install_segments",
        "_local_pip_install_project_names",
        "_drop_reinstalled_local_projects",
        "_is_exact_torch_requirement",
        "_is_broad_torch_requirement",
        "_is_torch_cpu_split_candidate",
        "_exact_torch_requirements",
        "_compatible_torchvision_requirement",
        "_pip_command_installs_torch_replacement",
        "_pip_command_installs_mosaicml_stack",
        "_dockerfile_exact_torch_replacement_requirement",
        "_dockerfile_contains_torch_replacement",
        "_dockerfile_contains_mosaicml_stack",
        "_drop_redundant_broad_torch_bootstrap",
        "_add_compatible_torchvision_constraint",
        "_is_redundant_exact_torch_reinstall",
        "_is_cuda_local_installer_scaffolding_command",
        "_rewrite_absolute_tests_redirect_to_workdir",
        "_harden_cuda_skipped_local_source_install",
        "_drop_replay_poetry_lock_command",
        "_dockerfile_may_include_poetry_lock",
        "_repair_generated_apt_retry_status_variables",
        "_add_no_deps_to_known_force_reinstall",
        "_shell_single_quote",
        "_has_unclosed_shell_quote",
        "_format_multiline_run_as_script",
        "_is_top_level_dockerfile_instruction",
        "_join_dockerfile_continued_lines",
        "_collect_continued_dockerfile_instruction",
        "_collect_raw_multiline_run",
        "_collect_generated_apt_retry_with_orphan_continuations",
        "_render_observed_pip_constraints_instruction",
        "build_resilient_pip_install_run_instruction",
        "build_resilient_apt_install_run_instruction",
        "build_resilient_uv_install_run_instruction",
        "split_heavy_pip_install_replay_commands",
        "extract_observed_pip_install_constraints_from_text",
        "collect_observed_pip_install_constraints",
        "normalize_eval_dockerfile_for_replay",
        "build_test_execution_script",
        "discover_internal_import_prefixes",
        "output_has_collection_error_signal",
        "output_has_invocation_error_signal",
        "output_has_internal_repo_import_error_signal",
        "classify_test_execution",
        "extract_missing_python_modules_from_test_execution",
        "_find_declared_requirement_in_workspace",
        "_requirement_for_missing_module",
        "_preferred_pip_invocation_for_dockerfile",
        "_dockerfile_already_installs_requirement",
        "_insert_run_instruction_before_final_command",
        "repair_dockerfile_for_missing_python_modules",
        "should_add_postgres_host_alias",
        "evaluate_built_image",
        "truncate_for_repair_prompt",
        "extract_json_object_candidates",
        "extract_dockerfile_repair_json",
        "build_dockerfile_repair_input",
        "repair_dockerfile_with_llm",
        "docker_build_failed_due_to_unavailable_daemon",
        "run_command",
        "create_openai_client_from_env",
        "derive_verification_commands",
    ]

    missing = [name for name in verbatim_callables if not hasattr(m, name)]
    assert not missing, f"Missing symbols: {missing}"


def test_test_signal_detector_present():
    """TEST_SIGNAL_DETECTOR must be a live Synthesizer instance."""
    import repo2run_repair_port as m

    detector = m.TEST_SIGNAL_DETECTOR
    assert hasattr(detector, "observation_has_effective_test_signal")
    assert hasattr(detector, "observation_has_empty_test_run_signal")
    assert hasattr(detector, "observation_looks_like_help_text")
    assert hasattr(detector, "observation_has_test_failure_signal")


def test_todo_stubs_present():
    """Stage A must include clearly-marked TODO stubs for B/C functions."""
    import repo2run_repair_port as m

    # These should be present as TODO stubs (callable but minimal/raising NotImplementedError)
    assert hasattr(m, "junit_to_pytest_results"), "junit_to_pytest_results stub missing"
    assert hasattr(m, "real_test_command"), "real_test_command stub missing"
    assert hasattr(m, "_repair_and_rescore"), "_repair_and_rescore stub missing"


def test_forbidden_import_grep_empty():
    """Run the equivalent of: grep -nE 'from src.(recipe_repair|artifact_verify)' in the module source.

    Only actual Python import statements are checked (lines that start with
    'from' or 'import', not comments or docstrings).
    """
    import pathlib
    import re

    module_path = pathlib.Path(__file__).parent.parent / "repo2run_repair_port.py"
    assert module_path.exists(), f"Module file not found at {module_path}"

    source = module_path.read_text(encoding="utf-8")
    # Match only import statements at the start of a line (not inside strings/comments)
    pattern = re.compile(
        r"^(?:from|import)\s+src\.(recipe_repair|artifact_verify)",
        re.MULTILINE,
    )
    matches = pattern.findall(source)
    assert not matches, (
        f"Forbidden imports found in repo2run_repair_port.py: {matches}"
    )


# ---------------------------------------------------------------------------
# Stage B — Step 1: junit_to_pytest_results
# ---------------------------------------------------------------------------


def test_junit_to_pytest_results_schema_keys():
    """junit_to_pytest_results must return all 7 top-level keys required by the schema."""
    import repo2run_repair_port as m

    execution = {
        "stdout": "1 passed in 0.1s",
        "stderr": "",
        "returncode": 0,
        "timed_out": False,
    }
    # No actual docker cp needed — patch _try_docker_cp_junit to return None (no XML)
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert EXPECTED_TOP_KEYS.issubset(set(result.keys())), (
        f"Missing keys: {EXPECTED_TOP_KEYS - set(result.keys())}"
    )
    assert EXPECTED_SUMMARY_KEYS.issubset(set(result["summary"].keys())), (
        f"Missing summary keys: {EXPECTED_SUMMARY_KEYS - set(result['summary'].keys())}"
    )
    assert isinstance(result["error_breakdown"], dict)
    assert isinstance(result["failed_tests"], list)
    assert isinstance(result["error_tests"], list)


def test_junit_to_pytest_results_timeout_shortcut():
    """timed_out=True must emit TimeoutError bucket directly without XML parsing."""
    import repo2run_repair_port as m

    execution = {
        "stdout": "",
        "stderr": "",
        "returncode": 124,
        "timed_out": True,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    # Must match run_pytest.py:517 timeout path
    assert result["error_breakdown"] == {"TimeoutError": 1}
    assert result["summary"]["total_tests"] == 0
    assert result["summary"]["passed"] == 0
    assert result["summary"]["failed"] == 0
    assert result["summary"]["errors"] == 0
    assert result["summary"]["skipped"] == 0


def test_junit_to_pytest_results_all_summary_ints():
    """All summary values must be integers."""
    import repo2run_repair_port as m

    execution = {
        "stdout": "3 passed, 1 failed in 0.5s",
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    for k in EXPECTED_SUMMARY_KEYS:
        assert isinstance(result["summary"][k], int), (
            f"summary.{k} should be int, got {type(result['summary'][k])}"
        )


def test_junit_to_pytest_results_parse_method_values():
    """parse_method must be 'junit_xml' or 'regex_fallback'."""
    import repo2run_repair_port as m

    execution = {
        "stdout": "1 passed in 0.1s",
        "stderr": "",
        "returncode": 0,
        "timed_out": False,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert result["parse_method"] in ("junit_xml", "regex_fallback")


def test_junit_to_pytest_results_golden_schema_parity():
    """Output schema must be structurally identical to the real on-disk file from resend-python."""
    import repo2run_repair_port as m

    if not GOLDEN_FIXTURE_PATH.exists():
        pytest.skip(f"Golden fixture not found: {GOLDEN_FIXTURE_PATH}")

    golden = json.loads(GOLDEN_FIXTURE_PATH.read_text())
    # Check all top-level keys are present and have same types
    for key in EXPECTED_TOP_KEYS:
        assert key in golden, f"Key missing from golden: {key}"

    execution = {
        "stdout": "1 passed in 0.1s",
        "stderr": "",
        "returncode": 0,
        "timed_out": False,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    # All keys from golden must be present in result
    for key in golden:
        assert key in result, f"Key '{key}' in golden but missing from result"
    # summary keys
    for key in golden["summary"]:
        assert key in result["summary"], (
            f"summary.{key} in golden but missing from result"
        )
    # error_breakdown must be a dict
    assert isinstance(result["error_breakdown"], dict)
    # failed_tests and error_tests must be lists of dicts with correct keys
    for item in result["failed_tests"]:
        for k in ("test_id", "error_type", "error_message"):
            assert k in item, f"failed_tests item missing key '{k}'"
    for item in result["error_tests"]:
        for k in ("test_id", "error_type", "error_message"):
            assert k in item, f"error_tests item missing key '{k}'"


# ---------------------------------------------------------------------------
# Stage B — Step 1b: error_breakdown bucket categorization
# ---------------------------------------------------------------------------


def test_categorize_error_module_not_found():
    """ModuleNotFoundError: prefix maps to ModuleNotFoundError bucket."""
    import repo2run_repair_port as m

    xml_str = _make_junit_xml([
        {
            "name": "test_import",
            "failure_msg": "ModuleNotFoundError: No module named 'foo'",
        }
    ])
    execution = {
        "stdout": "FAILED tests/test_import.py::test_import",
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
        "_junit_xml_override": xml_str,  # injected for testing
    }
    result = m.junit_to_pytest_results(
        execution, image_tag="test-image", junit_container_path="/tmp/repair_junit.xml"
    )
    assert result["error_breakdown"].get("ModuleNotFoundError", 0) >= 1, (
        f"Expected ModuleNotFoundError bucket, got: {result['error_breakdown']}"
    )


def test_categorize_error_import_error():
    """ImportError: prefix maps to ImportError bucket (distinct from ModuleNotFoundError)."""
    import repo2run_repair_port as m

    xml_str = _make_junit_xml([
        {
            "name": "test_import",
            "failure_msg": "ImportError: cannot import name 'Bar' from 'foo'",
        }
    ])
    execution = {
        "stdout": "FAILED tests/test_import.py::test_import",
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
        "_junit_xml_override": xml_str,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert result["error_breakdown"].get("ImportError", 0) >= 1, (
        f"Expected ImportError bucket, got: {result['error_breakdown']}"
    )


def test_categorize_error_assertion_no_colon():
    """AssertionError (no colon) maps to AssertionError bucket."""
    import repo2run_repair_port as m

    xml_str = _make_junit_xml([
        {
            "name": "test_assert",
            "failure_msg": "AssertionError",
        }
    ])
    execution = {
        "stdout": "FAILED tests/test_assert.py::test_assert",
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
        "_junit_xml_override": xml_str,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert result["error_breakdown"].get("AssertionError", 0) >= 1, (
        f"Expected AssertionError bucket, got: {result['error_breakdown']}"
    )


def test_categorize_error_other_error_catch_all():
    """Text that matches no pattern maps to OtherError catch-all."""
    import repo2run_repair_port as m

    xml_str = _make_junit_xml([
        {
            "name": "test_async",
            "failure_msg": "Failed: async def functions are not natively supported.",
        }
    ])
    execution = {
        "stdout": "FAILED tests/test_async.py::test_async",
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
        "_junit_xml_override": xml_str,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert result["error_breakdown"].get("OtherError", 0) >= 1, (
        f"Expected OtherError catch-all bucket, got: {result['error_breakdown']}"
    )


def test_categorize_error_first_match_wins():
    """First-match wins: ModuleNotFoundError before ImportError."""
    import repo2run_repair_port as m

    # Text has both — ModuleNotFoundError comes first in vocab so it wins
    xml_str = _make_junit_xml([
        {
            "name": "test_mixed",
            "failure_msg": "ModuleNotFoundError: No module named 'foo'\nImportError: foo",
        }
    ])
    execution = {
        "stdout": "",
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
        "_junit_xml_override": xml_str,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    # ModuleNotFoundError comes before ImportError in the 21-bucket vocab
    assert "ModuleNotFoundError" in result["error_breakdown"], (
        f"Expected ModuleNotFoundError (first-match), got: {result['error_breakdown']}"
    )


def test_categorize_error_all_21_buckets_known():
    """Spot-test several of the 21 defined buckets."""
    import repo2run_repair_port as m

    known_buckets = [
        ("TypeError:", "TypeError"),
        ("ValueError:", "ValueError"),
        ("KeyError:", "KeyError"),
        ("IndexError:", "IndexError"),
        ("NameError:", "NameError"),
        ("FileNotFoundError:", "FileNotFoundError"),
        ("RuntimeError:", "RuntimeError"),
        ("OSError:", "OSError"),
        ("IOError:", "IOError"),
        ("ZeroDivisionError:", "ZeroDivisionError"),
        ("SyntaxError:", "SyntaxError"),
        ("IndentationError:", "IndentationError"),
        ("MemoryError:", "MemoryError"),
        ("RecursionError:", "RecursionError"),
        ("TimeoutError:", "TimeoutError"),
        ("ConnectionError:", "ConnectionError"),
        ("PermissionError:", "PermissionError"),
    ]
    for failure_text, expected_bucket in known_buckets:
        xml_str = _make_junit_xml([{"name": "t", "failure_msg": f"{failure_text} boom"}])
        execution = {
            "stdout": "",
            "stderr": "",
            "returncode": 1,
            "timed_out": False,
            "_junit_xml_override": xml_str,
        }
        result = m.junit_to_pytest_results(execution, image_tag="test-image")
        assert result["error_breakdown"].get(expected_bucket, 0) >= 1, (
            f"Input '{failure_text}' → expected bucket '{expected_bucket}', "
            f"got: {result['error_breakdown']}"
        )


def test_junit_to_pytest_results_fully_passing():
    """Fully passing run: failed=0, errors=0, error_breakdown={}, parse_method=junit_xml."""
    import repo2run_repair_port as m

    xml_str = _make_junit_xml([
        {"classname": "tests.test_foo", "name": "test_a"},
        {"classname": "tests.test_foo", "name": "test_b"},
        {"classname": "tests.test_foo", "name": "test_c"},
    ])
    execution = {
        "stdout": "3 passed in 0.1s",
        "stderr": "",
        "returncode": 0,
        "timed_out": False,
        "_junit_xml_override": xml_str,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert result["summary"]["total_tests"] == 3
    assert result["summary"]["passed"] == 3
    assert result["summary"]["failed"] == 0
    assert result["summary"]["errors"] == 0
    assert result["error_breakdown"] == {}
    assert result["failed_tests"] == []
    assert result["parse_method"] == "junit_xml"


def test_junit_to_pytest_results_mixed_pass_fail():
    """Mixed run: correct counts and breakdown for 1 pass + 1 ModuleNotFoundError failure."""
    import repo2run_repair_port as m

    xml_str = _make_junit_xml([
        {"classname": "tests.test_basic", "name": "test_one"},
        {
            "classname": "tests.test_import",
            "name": "test_two",
            "failure_msg": "ModuleNotFoundError: No module named 'foo'",
        },
    ])
    execution = {
        "stdout": "1 passed, 1 failed in 0.5s",
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
        "_junit_xml_override": xml_str,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert result["summary"]["total_tests"] == 2
    assert result["summary"]["passed"] == 1
    assert result["summary"]["failed"] == 1
    assert result["summary"]["errors"] == 0
    assert result["error_breakdown"] == {"ModuleNotFoundError": 1}
    assert len(result["failed_tests"]) == 1
    assert result["failed_tests"][0]["error_type"] == "ModuleNotFoundError"


# ---------------------------------------------------------------------------
# Stage B — Step 1c: RISK-2 regex fallback (when JUnit XML absent)
# ---------------------------------------------------------------------------


def test_junit_to_pytest_results_regex_fallback_no_xml():
    """RISK-2: When JUnit XML is unavailable, regex fallback over stdout still yields summary."""
    import repo2run_repair_port as m

    # Simulate stdout with no XML available (no _junit_xml_override, docker cp would fail)
    stdout = textwrap.dedent("""\
        ============================= test session starts ==============================
        collected 3 items

        tests/test_foo.py::test_a PASSED
        tests/test_foo.py::test_b PASSED
        tests/test_foo.py::test_c FAILED - AssertionError

        ========================= 2 passed, 1 failed in 0.3s ==========================
    """)
    execution = {
        "stdout": stdout,
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
        # No _junit_xml_override → forces regex fallback path
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    # Must not raise — must return valid schema
    assert "summary" in result
    assert "error_breakdown" in result
    assert isinstance(result["summary"]["total_tests"], int)
    assert isinstance(result["summary"]["passed"], int)
    assert isinstance(result["summary"]["failed"], int)
    assert result["parse_method"] == "regex_fallback"


def test_junit_to_pytest_results_regex_fallback_counts():
    """RISK-2: Regex fallback correctly extracts passed/failed counts from pytest summary line."""
    import repo2run_repair_port as m

    stdout = "5 passed, 2 failed, 1 error in 1.2s"
    execution = {
        "stdout": stdout,
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert result["parse_method"] == "regex_fallback"
    assert result["summary"]["passed"] == 5
    assert result["summary"]["failed"] == 2
    # total_tests = passed + failed + errors (at minimum)
    assert result["summary"]["total_tests"] >= 7


def test_junit_to_pytest_results_regex_fallback_skipped():
    """RISK-2: Regex fallback extracts skipped count."""
    import repo2run_repair_port as m

    stdout = "3 passed, 2 skipped in 0.5s"
    execution = {
        "stdout": stdout,
        "stderr": "",
        "returncode": 0,
        "timed_out": False,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert result["parse_method"] == "regex_fallback"
    assert result["summary"]["passed"] == 3
    assert result["summary"]["skipped"] == 2


def test_junit_to_pytest_results_empty_output_no_crash():
    """Empty stdout/stderr and no XML must produce a valid zero-count schema."""
    import repo2run_repair_port as m

    execution = {
        "stdout": "",
        "stderr": "",
        "returncode": 1,
        "timed_out": False,
    }
    result = m.junit_to_pytest_results(execution, image_tag="test-image")
    assert "summary" in result
    assert "error_breakdown" in result
    assert isinstance(result["summary"]["total_tests"], int)
    assert result["parse_method"] in ("junit_xml", "regex_fallback")


# ---------------------------------------------------------------------------
# Stage B — Step 2: real_test_command
# ---------------------------------------------------------------------------


def test_real_test_command_strips_collect_only():
    """--collect-only is stripped; --junitxml appended."""
    import repo2run_repair_port as m

    recipe = {
        "logs": {
            "verified_test_commands": ["pytest --collect-only -q --disable-warnings"],
        }
    }
    cmd, junit_path = m.real_test_command(recipe)
    assert "--collect-only" not in cmd
    assert "--junitxml=" in cmd
    assert junit_path == "/tmp/repair_junit.xml"


def test_real_test_command_strips_quiet_flag():
    """--quiet / -q flags are stripped along with --collect-only."""
    import repo2run_repair_port as m

    recipe = {
        "logs": {
            "verified_test_commands": ["pytest -q --collect-only"],
        }
    }
    cmd, junit_path = m.real_test_command(recipe)
    assert "--collect-only" not in cmd
    # -q removal is optional per spec: "Strip each command of --collect-only and any -q/--quiet flags"
    # just verify it doesn't crash and returns something valid
    assert "--junitxml=" in cmd


def test_real_test_command_preserves_poetry_prefix():
    """poetry run pytest prefix is preserved."""
    import repo2run_repair_port as m

    recipe = {
        "logs": {
            "verified_test_commands": ["poetry run pytest --collect-only -q"],
        }
    }
    cmd, junit_path = m.real_test_command(recipe)
    assert cmd.startswith("poetry run pytest")
    assert "--collect-only" not in cmd
    assert "--junitxml=" in cmd


def test_real_test_command_preserves_uv_prefix():
    """uv run pytest prefix is preserved."""
    import repo2run_repair_port as m

    recipe = {
        "logs": {
            "verified_test_commands": ["uv run pytest --collect-only"],
        }
    }
    cmd, junit_path = m.real_test_command(recipe)
    assert cmd.startswith("uv run pytest")
    assert "--collect-only" not in cmd
    assert "--junitxml=" in cmd


def test_real_test_command_preserves_python_m_pytest():
    """python -m pytest prefix is preserved."""
    import repo2run_repair_port as m

    recipe = {
        "logs": {
            "verified_test_commands": ["python -m pytest --collect-only tests/"],
        }
    }
    cmd, junit_path = m.real_test_command(recipe)
    assert cmd.startswith("python -m pytest")
    assert "--collect-only" not in cmd
    assert "--junitxml=" in cmd


def test_real_test_command_fallback_empty_logs():
    """No logs key → fallback 'pytest -q --disable-warnings --junitxml=...'."""
    import repo2run_repair_port as m

    recipe: dict = {"logs": {}}
    cmd, junit_path = m.real_test_command(recipe)
    assert "pytest" in cmd
    assert "--junitxml=" in cmd
    assert junit_path == "/tmp/repair_junit.xml"


def test_real_test_command_fallback_no_logs():
    """No logs at all → fallback."""
    import repo2run_repair_port as m

    recipe: dict = {}
    cmd, junit_path = m.real_test_command(recipe)
    assert "pytest" in cmd
    assert "--junitxml=" in cmd


def test_real_test_command_no_double_junitxml():
    """If --junitxml is already present, do not add a second one."""
    import repo2run_repair_port as m

    recipe = {
        "logs": {
            "verified_test_commands": ["pytest --junitxml=/tmp/existing.xml"],
        }
    }
    cmd, junit_path = m.real_test_command(recipe)
    assert cmd.count("--junitxml=") == 1


def test_real_test_command_no_runnable_after_strip():
    """When stripping --collect-only leaves nothing meaningful, fallback to default."""
    import repo2run_repair_port as m

    recipe = {
        "logs": {
            "verified_test_commands": ["--collect-only"],
        }
    }
    cmd, junit_path = m.real_test_command(recipe)
    assert "pytest" in cmd
    assert "--junitxml=" in cmd


def test_real_test_command_returns_tuple():
    """real_test_command must return a (str, str) tuple."""
    import repo2run_repair_port as m

    recipe: dict = {}
    result = m.real_test_command(recipe)
    assert isinstance(result, tuple) and len(result) == 2
    cmd, junit_path = result
    assert isinstance(cmd, str)
    assert isinstance(junit_path, str)


def test_real_test_command_junit_path_is_tmp():
    """Returned junit_container_path must be under /tmp."""
    import repo2run_repair_port as m

    recipe: dict = {}
    _, junit_path = m.real_test_command(recipe)
    assert junit_path.startswith("/tmp/")


def test_real_test_command_preserves_non_collect_flags():
    """Flags other than --collect-only/-q/--quiet must be preserved."""
    import repo2run_repair_port as m

    recipe = {
        "logs": {
            "verified_test_commands": [
                "pytest --collect-only --disable-warnings tests/unit/"
            ],
        }
    }
    cmd, _ = m.real_test_command(recipe)
    assert "--collect-only" not in cmd
    # --disable-warnings and test path should remain
    assert "--disable-warnings" in cmd
    assert "tests/unit/" in cmd


# ---------------------------------------------------------------------------
# Stage C — Step 4+5+6: _repair_and_rescore tests
# ---------------------------------------------------------------------------

import os
import tempfile
from unittest.mock import MagicMock, call, patch


def _make_out_dict(root_path: str, full_name: str = "owner/repo") -> dict:
    """Minimal predict() return dict for testing."""
    return {
        "status": "success",
        "failure_reason": None,
        "root_path": root_path,
        "full_name": full_name,
        "requested_model": "test-model",
        "base_image": "python:3.11",
        "head_sha": "abc123",
    }


def _make_recipe(verified_test_commands=None, build_commands=None) -> dict:
    """Minimal recipe JSON structure."""
    return {
        "logs": {
            "verified_test_commands": verified_test_commands or ["pytest --collect-only"],
            "build_recipe": {
                "source": "agent",
                "build_commands": build_commands or ["pip install -r requirements.txt"],
                "runtime_commands": [],
            },
        }
    }


def _make_run_summary(successful_actions=None) -> dict:
    """Minimal agent_run_summary.json structure."""
    return {
        "repo_url": "https://github.com/owner/repo",
        "configuration_success": True,
        "base_image": "python:3.11",
        "verified_test_commands": ["pytest -q"],
        "verified_runtime_preparation_commands": [],
        "successful_actions": successful_actions or [],
        "failed_actions": [],
        "build_recipe": {
            "source": "agent",
            "build_commands": ["pip install -r requirements.txt"],
            "runtime_commands": [],
        },
    }


def _make_hollow_test_execution() -> dict:
    """A test_execution dict that represents a hollow (non-effective) result."""
    return {
        "workdir": "/testbed",
        "runtime_preparation_commands": [],
        "test_commands": ["pytest --junitxml=/tmp/repair_junit.xml"],
        "results": [
            {
                "test_command": "pytest --junitxml=/tmp/repair_junit.xml",
                "runtime_preparation_commands": [],
                "script": "set -e\ncd /testbed\npytest\n",
                "execution": {
                    "stdout": "ModuleNotFoundError: No module named 'foo'",
                    "stderr": "",
                    "returncode": 1,
                    "timed_out": False,
                },
                "classification": {
                    "effective": False,
                    "reason": "missing_third_party_module",
                },
            }
        ],
        "effective_test_command_count": 0,
        "all_test_commands_effective": False,
    }


def _make_effective_test_execution() -> dict:
    """A test_execution dict that represents a passing (effective) result."""
    return {
        "workdir": "/testbed",
        "runtime_preparation_commands": [],
        "test_commands": ["pytest --junitxml=/tmp/repair_junit.xml"],
        "results": [
            {
                "test_command": "pytest --junitxml=/tmp/repair_junit.xml",
                "runtime_preparation_commands": [],
                "script": "set -e\ncd /testbed\npytest\n",
                "execution": {
                    "stdout": "5 passed in 0.5s",
                    "stderr": "",
                    "returncode": 0,
                    "timed_out": False,
                },
                "classification": {
                    "effective": True,
                    "reason": "tests_passed",
                },
            }
        ],
        "effective_test_command_count": 1,
        "all_test_commands_effective": True,
    }


def _make_docker_build_success() -> dict:
    return {"returncode": 0, "stdout": "Successfully built abc123", "stderr": "", "timed_out": False}


def _make_docker_build_failure() -> dict:
    return {"returncode": 1, "stdout": "", "stderr": "Build failed", "timed_out": False}


def _make_docker_build_daemon_unavailable() -> dict:
    """Simulates docker daemon unavailable error."""
    return {"returncode": 1, "stdout": "", "stderr": "Cannot connect to the Docker daemon", "timed_out": False}


def _setup_repair_tmpdir(tmp_path, full_name="owner/repo", recipe=None, run_summary=None, dockerfile=None):
    """Set up the on-disk layout expected by _repair_and_rescore."""
    slug = full_name.replace("/", "__")
    out_dir = tmp_path / "output" / full_name
    eval_build = out_dir / "eval_build"
    eval_build.mkdir(parents=True, exist_ok=True)

    # Write a minimal Dockerfile
    df_text = dockerfile or "FROM python:3.11\nWORKDIR /testbed\nRUN pip install pytest\n"
    (eval_build / "Dockerfile").write_text(df_text)

    # Write recipe JSON
    recipe_data = recipe or _make_recipe()
    (out_dir / f"{slug}.json").write_text(json.dumps(recipe_data))

    # Write agent_run_summary.json at the deterministic workplace path
    summary_data = run_summary or _make_run_summary()
    workplace_dir = tmp_path / "workplace" / f"multi_docker_eval_{slug}"
    workplace_dir.mkdir(parents=True, exist_ok=True)
    (workplace_dir / "agent_run_summary.json").write_text(json.dumps(summary_data))

    return str(tmp_path), out_dir


class TestRepairAndRescore:
    """Stage C TDD tests for _repair_and_rescore.

    Docker is never invoked: run_command, evaluate_built_image, and
    repair_dockerfile_with_llm are patched throughout.
    """

    @pytest.fixture(autouse=True)
    def _point_workplace_root_at_tmp(self, tmp_path, monkeypatch):
        # _repair_and_rescore loads agent_run_summary.json from
        # $DOCKERAGENT_ROOT/workplace/multi_docker_eval_{slug}/ — point it at the
        # test's tmp_path so the summary written by _setup_repair_tmpdir is found
        # (mirrors the real layout: the agent writes ./workplace relative to the
        # repo root / DOCKERAGENT_ROOT, NOT under the run's root_path).
        monkeypatch.setenv("DOCKERAGENT_ROOT", str(tmp_path))

    def _base_patches(self):
        """Return a stack of patches that prevent real docker calls."""
        import repo2run_repair_port as m

        return [
            patch.object(m, "run_command"),
            patch.object(m, "evaluate_built_image"),
            patch.object(m, "repair_dockerfile_with_llm"),
            patch("subprocess.run"),  # prevent docker rmi calls from touching real docker
        ]

    def test_hollow_then_deterministic_repair_resolved(self, tmp_path):
        """Scenario: build succeeds, test is hollow (missing module).
        Deterministic repair patches the Dockerfile. Second attempt passes.
        """
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        # Two builds: first succeeds, second succeeds; tests: first hollow, second effective
        build_results = [_make_docker_build_success(), _make_docker_build_success()]
        test_results = [_make_hollow_test_execution(), _make_effective_test_execution()]

        # requirements.txt needed by repair_dockerfile_for_missing_python_modules
        (out_dir / "eval_build" / "requirements.txt").write_text("foo==1.0.0\n")

        with patch.object(m, "run_command", side_effect=build_results), \
             patch.object(m, "evaluate_built_image", side_effect=test_results), \
             patch.object(m, "repair_dockerfile_with_llm") as mock_llm, \
             patch("subprocess.run"):  # prevent real docker rmi

            result = m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=2)

        # Should return out dict
        assert result is out or isinstance(result, dict)
        # LLM should NOT have been called (deterministic repair resolved it)
        mock_llm.assert_not_called()
        # run_pytest_results.json must be written
        pytest_json_path = out_dir / "run_pytest_results.json"
        assert pytest_json_path.exists(), "run_pytest_results.json must be written"
        written = json.loads(pytest_json_path.read_text())
        assert "summary" in written

    def test_hollow_then_llm_repair_resolved(self, tmp_path):
        """Scenario: build fails on first attempt (or test hollow with no deterministic fix).
        LLM repair produces a new Dockerfile; second build + test passes.
        """
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        # First build fails; after LLM repair second build succeeds with effective tests
        build_results = [_make_docker_build_failure(), _make_docker_build_success()]
        test_results = [_make_effective_test_execution()]  # only called on second attempt

        repaired_dockerfile = "FROM python:3.11\nWORKDIR /testbed\nRUN pip install foo pytest\n"
        llm_repair_result = {
            "round": 1,
            "source": "llm",
            "error": None,
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            "raw_content": '{"dockerfile": "...", "rationale": "Fixed", "confidence": "high"}',
            "dockerfile_text": repaired_dockerfile,
            "rationale": "Added missing foo package",
            "confidence": "high",
            "log_path": None,
        }

        with patch.object(m, "run_command", side_effect=build_results), \
             patch.object(m, "evaluate_built_image", side_effect=test_results), \
             patch.object(m, "repair_dockerfile_with_llm", return_value=llm_repair_result), \
             patch.object(m, "create_openai_client_from_env", return_value=MagicMock()), \
             patch("subprocess.run"):

            result = m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=2)

        # run_pytest_results.json must be written
        pytest_json_path = out_dir / "run_pytest_results.json"
        assert pytest_json_path.exists(), "run_pytest_results.json must be written after LLM repair"
        written = json.loads(pytest_json_path.read_text())
        assert "summary" in written

    def test_unconditional_write_on_unresolved(self, tmp_path):
        """PLAN property #1: run_pytest_results.json is written even when repair fails to resolve."""
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        # All build+test attempts fail / stay hollow
        build_results = [_make_docker_build_success()] * 3
        test_results = [_make_hollow_test_execution()] * 3

        llm_repair_result = {
            "round": 1,
            "source": "llm",
            "error": None,
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            "raw_content": "{}",
            "dockerfile_text": "FROM python:3.11\nWORKDIR /testbed\n",
            "rationale": "No fix found",
            "confidence": "low",
            "log_path": None,
        }

        with patch.object(m, "run_command", side_effect=build_results), \
             patch.object(m, "evaluate_built_image", side_effect=test_results), \
             patch.object(m, "repair_dockerfile_with_llm", return_value=llm_repair_result), \
             patch.object(m, "create_openai_client_from_env", return_value=MagicMock()), \
             patch("subprocess.run"):

            result = m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=2)

        # run_pytest_results.json MUST be written even though nothing was resolved
        pytest_json_path = out_dir / "run_pytest_results.json"
        assert pytest_json_path.exists(), (
            "run_pytest_results.json must be written unconditionally even when unresolved"
        )
        written = json.loads(pytest_json_path.read_text())
        assert "summary" in written

    def test_infra_short_circuit_skips_llm_round(self, tmp_path):
        """When docker daemon is unavailable, repair loop must break without calling LLM."""
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        daemon_unavail = _make_docker_build_daemon_unavailable()

        with patch.object(m, "run_command", return_value=daemon_unavail), \
             patch.object(m, "evaluate_built_image") as mock_eval, \
             patch.object(m, "repair_dockerfile_with_llm") as mock_llm, \
             patch.object(m, "create_openai_client_from_env", return_value=MagicMock()), \
             patch("subprocess.run"):

            result = m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=2)

        # LLM should never be called when daemon is unavailable
        mock_llm.assert_not_called()
        # evaluate_built_image should never be called (build failed)
        mock_eval.assert_not_called()
        # Function must return out without raising
        assert result is out or isinstance(result, dict)

    def test_never_raises_on_internal_exception(self, tmp_path):
        """never-raises contract: all internal errors are caught and degrade to original results."""
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        with patch.object(m, "run_command", side_effect=RuntimeError("docker exploded")), \
             patch("subprocess.run"):

            # Must not raise
            result = m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=2)

        assert isinstance(result, dict)

    def test_never_raises_on_missing_files(self, tmp_path):
        """If recipe or Dockerfile is missing, function returns out without raising."""
        import repo2run_repair_port as m

        # Don't set up any files — empty tmp dir
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        with patch("subprocess.run"):
            result = m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=1)

        assert isinstance(result, dict)

    def test_trajectory_successful_actions_reach_llm_payload(self, tmp_path):
        """SPEC H3/H5: successful_actions from agent_run_summary must appear in the
        LLM repair input payload (build_dockerfile_repair_input output).
        This is the primary regression vector for trajectory-aware repair.
        """
        import repo2run_repair_port as m

        editable_action = {
            "step_index": 3,
            "command": "pip install -e .",
            "success": True,
            "mutates_environment": True,
            "is_readonly": False,
            "is_runtime_service": False,
            "is_runtime_healthcheck": False,
        }
        successful_actions = [
            {
                "step_index": 1,
                "command": "pip install -r requirements.txt",
                "success": True,
                "mutates_environment": True,
                "is_readonly": False,
                "is_runtime_service": False,
                "is_runtime_healthcheck": False,
            },
            editable_action,
            {
                "step_index": 2,
                "command": "pytest tests/",
                "success": True,
                "mutates_environment": False,
                "is_readonly": False,
                "is_runtime_service": False,
                "is_runtime_healthcheck": False,
            },
        ]
        run_summary = _make_run_summary(successful_actions=successful_actions)

        # Use build_dockerfile_repair_input directly (no docker needed)
        payload = m.build_dockerfile_repair_input(
            instance={"instance_id": "owner__repo", "full_name": "owner/repo", "sha": "", "repo_url": ""},
            workdir="/testbed",
            dockerfile_text="FROM python:3.11\nWORKDIR /testbed\n",
            run_summary=run_summary,
            runtime_commands=[],
            test_commands=["pytest --junitxml=/tmp/repair_junit.xml"],
            docker_build=None,
            test_execution=None,
        )

        # The payload must carry successful_actions through to the LLM
        agent_summary = payload["agent_run_summary"]
        assert "successful_actions" in agent_summary, (
            "agent_run_summary must include successful_actions key"
        )
        assert len(agent_summary["successful_actions"]) == 3, (
            "All successful_actions must be forwarded to LLM payload"
        )
        # The editable install must be in the payload
        commands = [a["command"] for a in agent_summary["successful_actions"]]
        assert "pip install -e ." in commands, (
            "Editable install (pip install -e .) must be in the LLM payload trajectory"
        )

    def test_trajectory_system_prompt_has_rule5(self):
        """SPEC E step 5: DOCKERFILE_REPAIR_SYSTEM_PROMPT must contain Rule 5 text."""
        import repo2run_repair_port as m

        assert "restore" in m.DOCKERFILE_REPAIR_SYSTEM_PROMPT.lower() or \
               "restoring" in m.DOCKERFILE_REPAIR_SYSTEM_PROMPT.lower(), (
            "Rule 5 text about restoring successful setup commands must be in system prompt"
        )
        # More specific check
        assert "successful setup commands" in m.DOCKERFILE_REPAIR_SYSTEM_PROMPT or \
               "successful_actions" in m.DOCKERFILE_REPAIR_SYSTEM_PROMPT or \
               "trajectory" in m.DOCKERFILE_REPAIR_SYSTEM_PROMPT.lower(), (
            "System prompt must reference successful_actions or trajectory repair concept"
        )

    def test_agent_summary_loaded_from_workplace_path(self, tmp_path):
        """Trajectory loader: agent_run_summary.json is loaded from the deterministic
        workplace path (root_path/workplace/multi_docker_eval_{slug}/agent_run_summary.json).
        """
        import repo2run_repair_port as m

        full_name = "owner/repo"
        slug = full_name.replace("/", "__")

        # Set up recipe and Dockerfile but put agent_run_summary at the RIGHT workplace path
        out_dir = tmp_path / "output" / full_name
        eval_build = out_dir / "eval_build"
        eval_build.mkdir(parents=True, exist_ok=True)
        (eval_build / "Dockerfile").write_text("FROM python:3.11\nWORKDIR /testbed\n")
        (out_dir / f"{slug}.json").write_text(json.dumps(_make_recipe()))

        # Write agent_run_summary at the DETERMINISTIC path
        workplace_dir = tmp_path / "workplace" / f"multi_docker_eval_{slug}"
        workplace_dir.mkdir(parents=True, exist_ok=True)
        special_action = {
            "step_index": 99,
            "command": "echo sentinel_from_workplace",
            "success": True,
            "mutates_environment": False,
            "is_readonly": True,
            "is_runtime_service": False,
            "is_runtime_healthcheck": False,
        }
        run_summary = _make_run_summary(successful_actions=[special_action])
        (workplace_dir / "agent_run_summary.json").write_text(json.dumps(run_summary))

        out = _make_out_dict(str(tmp_path), full_name)

        captured_build_inputs = []

        def capture_repair_input(**kwargs):
            captured_build_inputs.append(kwargs.get("run_summary", {}))
            raise RuntimeError("stop after capture")  # abort after first call

        with patch.object(m, "run_command", return_value=_make_docker_build_failure()), \
             patch.object(m, "build_dockerfile_repair_input", side_effect=capture_repair_input), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=1)

        # If the workplace summary was loaded, at least one build_dockerfile_repair_input
        # call should have seen our sentinel action
        if captured_build_inputs:
            actions = captured_build_inputs[0].get("successful_actions", [])
            commands = [a["command"] for a in actions]
            assert "echo sentinel_from_workplace" in commands, (
                "successful_actions from workplace must reach build_dockerfile_repair_input; "
                f"got commands: {commands}"
            )


# ---------------------------------------------------------------------------
# Stage 2 — Fix (b): build-fail stub
# ---------------------------------------------------------------------------


class TestBuildFailStub:
    """Fix (b): when ALL build attempts fail, run_pytest_results.json must be written
    with a build_failed stub so scorers never read stale data.
    """

    @pytest.fixture(autouse=True)
    def _point_workplace_root_at_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKERAGENT_ROOT", str(tmp_path))

    def test_build_fail_stub_written_when_all_builds_fail(self, tmp_path):
        """When every docker build fails (returncode != 0), run_pytest_results.json
        must be written with parse_method='build_failed' and zero counts.
        """
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        # All build attempts fail
        with patch.object(m, "run_command", return_value=_make_docker_build_failure()), \
             patch.object(m, "evaluate_built_image") as mock_eval, \
             patch.object(m, "repair_dockerfile_with_llm") as mock_llm, \
             patch.object(m, "create_openai_client_from_env", return_value=MagicMock()), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=2)

        # evaluate_built_image must never be called (all builds failed)
        mock_eval.assert_not_called()

        pytest_json_path = out_dir / "run_pytest_results.json"
        assert pytest_json_path.exists(), (
            "run_pytest_results.json must be written even when all builds fail"
        )
        written = json.loads(pytest_json_path.read_text())

        # Must have the canonical stub structure
        assert written.get("parse_method") == "build_failed", (
            f"Expected parse_method='build_failed', got: {written.get('parse_method')}"
        )
        summary = written.get("summary", {})
        assert summary.get("total_tests") == 0
        assert summary.get("passed") == 0
        assert summary.get("failed") == 0
        assert summary.get("errors") == 0
        assert summary.get("skipped") == 0
        assert summary.get("xfailed") == 0
        assert summary.get("xpassed") == 0
        assert written.get("returncode") == 1

    def test_build_fail_stub_not_written_when_test_executed(self, tmp_path):
        """When at least one build succeeds and tests run, the real result (not the stub)
        must be in run_pytest_results.json.
        """
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        # One build succeeds, tests run (hollow)
        with patch.object(m, "run_command", return_value=_make_docker_build_success()), \
             patch.object(m, "evaluate_built_image", return_value=_make_hollow_test_execution()), \
             patch.object(m, "repair_dockerfile_with_llm") as mock_llm, \
             patch.object(m, "create_openai_client_from_env", return_value=MagicMock()), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=0)

        pytest_json_path = out_dir / "run_pytest_results.json"
        assert pytest_json_path.exists()
        written = json.loads(pytest_json_path.read_text())
        # Must NOT be a build_failed stub — real result was written
        assert written.get("parse_method") != "build_failed", (
            "parse_method must not be 'build_failed' when tests actually ran"
        )

    def test_build_fail_does_not_clobber_real_framework_result(self, tmp_path):
        """REGRESSION (clobber bug): a real framework run_pytest_results.json already on
        disk must be PRESERVED when the repair's own rebuild fails on every attempt —
        never overwritten with a build_failed 0-tests stub (mcp-atlassian 2578/2739 -> 0/0)."""
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        # A real framework result with SOME failures (so the early-pass exit does NOT fire
        # and the repair loop actually runs).
        framework = {
            "summary": {"total_tests": 2739, "passed": 2000, "failed": 739,
                        "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "error_breakdown": {"AssertionError": 739}, "failed_tests": [],
            "returncode": 1, "parse_method": "junit_xml",
        }
        (out_dir / "run_pytest_results.json").write_text(json.dumps(framework))

        # Every repair build fails.
        with patch.object(m, "run_command", return_value=_make_docker_build_failure()), \
             patch.object(m, "evaluate_built_image") as mock_eval, \
             patch.object(m, "repair_dockerfile_with_llm"), \
             patch.object(m, "create_openai_client_from_env", return_value=MagicMock()), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=2)

        written = json.loads((out_dir / "run_pytest_results.json").read_text())
        assert written.get("parse_method") == "junit_xml", (
            "framework result must be preserved, not clobbered by a build_failed stub"
        )
        assert written["summary"]["passed"] == 2000, "framework pass count must be intact"
        assert written["summary"]["total_tests"] == 2739


# ---------------------------------------------------------------------------
# Stage 2 — Fix (c): real_test_command cd-prefix preservation
# ---------------------------------------------------------------------------


class TestRealTestCommandCdPrefix:
    """Fix (c): 'cd dir && pytest ...' commands must preserve the cd prefix."""

    def test_cd_prefix_preserved(self):
        """'cd subdir && pytest tests/' preserves the full compound command."""
        import repo2run_repair_port as m

        recipe = {
            "logs": {
                "verified_test_commands": ["cd subdir && pytest tests/"],
            }
        }
        cmd, junit_path = m.real_test_command(recipe)
        assert "cd subdir" in cmd, (
            f"Expected 'cd subdir' to be preserved in command, got: {cmd!r}"
        )
        assert "pytest" in cmd
        assert "--junitxml=" in cmd

    def test_cd_prefix_with_collect_only_stripped(self):
        """'cd dir && pytest --collect-only -q tests/' strips --collect-only but keeps cd prefix."""
        import repo2run_repair_port as m

        recipe = {
            "logs": {
                "verified_test_commands": ["cd dir && pytest --collect-only -q tests/"],
            }
        }
        cmd, junit_path = m.real_test_command(recipe)
        assert "--collect-only" not in cmd, (
            f"--collect-only must be stripped, got: {cmd!r}"
        )
        assert "cd dir" in cmd, (
            f"Expected 'cd dir' to be preserved, got: {cmd!r}"
        )
        assert "pytest" in cmd
        assert "--junitxml=" in cmd

    def test_cd_prefix_with_poetry_run_pytest(self):
        """'cd app && poetry run pytest tests/' preserves full compound command."""
        import repo2run_repair_port as m

        recipe = {
            "logs": {
                "verified_test_commands": ["cd app && poetry run pytest tests/"],
            }
        }
        cmd, junit_path = m.real_test_command(recipe)
        assert "cd app" in cmd, (
            f"Expected 'cd app' prefix preserved, got: {cmd!r}"
        )
        assert "poetry run pytest" in cmd
        assert "--junitxml=" in cmd

    def test_simple_pytest_still_works_without_cd(self):
        """Plain 'pytest tests/' (no cd prefix) still works correctly."""
        import repo2run_repair_port as m

        recipe = {
            "logs": {
                "verified_test_commands": ["pytest --collect-only tests/unit/"],
            }
        }
        cmd, junit_path = m.real_test_command(recipe)
        assert cmd.startswith("pytest")
        assert "--collect-only" not in cmd
        assert "tests/unit/" in cmd
        assert "--junitxml=" in cmd

    def test_cd_prefix_no_double_junitxml(self):
        """Compound command with --junitxml already present must not add a second one."""
        import repo2run_repair_port as m

        recipe = {
            "logs": {
                "verified_test_commands": [
                    "cd subdir && pytest tests/ --junitxml=/tmp/existing.xml"
                ],
            }
        }
        cmd, _ = m.real_test_command(recipe)
        assert cmd.count("--junitxml=") == 1, (
            f"Expected exactly 1 --junitxml=, got: {cmd!r}"
        )


# ---------------------------------------------------------------------------
# Stage 4 — New coverage: pre-loop setup and glue correctness
# ---------------------------------------------------------------------------


class TestDockerignorePreLoop:
    """Gap 1 fix: ensure_eval_dockerignore_includes_test_artifacts is invoked before
    the repair loop, not skipped.
    """

    @pytest.fixture(autouse=True)
    def _point_workplace_root_at_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKERAGENT_ROOT", str(tmp_path))

    def test_dockerignore_rewrite_called_before_loop(self, tmp_path):
        """ensure_eval_dockerignore_includes_test_artifacts must be called in
        _repair_and_rescore before the first docker build attempt.
        """
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        call_order: list[str] = []

        original_ensure = m.ensure_eval_dockerignore_includes_test_artifacts
        original_run_command = m.run_command

        def spy_ensure(*args, **kwargs):
            call_order.append("ensure_dockerignore")
            return original_ensure(*args, **kwargs)

        def spy_run_command(*args, **kwargs):
            call_order.append("run_command")
            return _make_docker_build_failure()

        with patch.object(m, "ensure_eval_dockerignore_includes_test_artifacts", side_effect=spy_ensure), \
             patch.object(m, "run_command", side_effect=spy_run_command), \
             patch.object(m, "evaluate_built_image", return_value=_make_hollow_test_execution()), \
             patch.object(m, "repair_dockerfile_with_llm"), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=0)

        assert "ensure_dockerignore" in call_order, (
            "ensure_eval_dockerignore_includes_test_artifacts was never called"
        )
        assert "run_command" in call_order, (
            "run_command (docker build) was never called"
        )
        # dockerignore rewrite must happen BEFORE the first docker build
        ensure_idx = next(i for i, v in enumerate(call_order) if v == "ensure_dockerignore")
        build_idx = next(i for i, v in enumerate(call_order) if v == "run_command")
        assert ensure_idx < build_idx, (
            f"ensure_eval_dockerignore_includes_test_artifacts (pos {ensure_idx}) must be "
            f"called before run_command / docker build (pos {build_idx}); "
            f"call_order={call_order}"
        )

    def test_dockerignore_receives_test_commands(self, tmp_path):
        """ensure_eval_dockerignore_includes_test_artifacts must receive the collect
        commands (from derive_repo2run_collect_commands), not None.
        """
        import repo2run_repair_port as m

        # A run_summary with a non-empty test_commands to ensure collect commands are non-empty
        run_summary = _make_run_summary()
        run_summary["build_recipe"]["test_commands"] = ["pytest --collect-only -q"]
        root_path, out_dir = _setup_repair_tmpdir(tmp_path, run_summary=run_summary)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        captured_kwargs: list[dict] = []

        def capture_ensure(build_context, test_commands=None, run_summary=None):
            captured_kwargs.append({"test_commands": test_commands, "run_summary": run_summary})
            return {"changed": False, "reason": "no_dockerignore"}

        with patch.object(m, "ensure_eval_dockerignore_includes_test_artifacts", side_effect=capture_ensure), \
             patch.object(m, "run_command", return_value=_make_docker_build_failure()), \
             patch.object(m, "repair_dockerfile_with_llm"), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=0)

        assert captured_kwargs, "ensure_eval_dockerignore_includes_test_artifacts was never called"
        call = captured_kwargs[0]
        # test_commands must not be None — it should come from derive_repo2run_collect_commands
        assert call["test_commands"] is not None, (
            "test_commands passed to ensure_eval_dockerignore_includes_test_artifacts must not be None"
        )


class TestDeriveRepo2RunCollectCommands:
    """Gap 2 fix: _repair_and_rescore must use derive_repo2run_collect_commands
    (which applies filter_runtime_preparation_commands) rather than
    derive_verification_commands (which skips that filter).
    """

    @pytest.fixture(autouse=True)
    def _point_workplace_root_at_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKERAGENT_ROOT", str(tmp_path))

    def test_derive_repo2run_collect_commands_called(self, tmp_path):
        """_repair_and_rescore must call derive_repo2run_collect_commands, not
        derive_verification_commands, for runtime_commands derivation.
        """
        import repo2run_repair_port as m

        root_path, out_dir = _setup_repair_tmpdir(tmp_path)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        collect_calls: list[int] = []
        verify_calls: list[int] = []

        original_collect = m.derive_repo2run_collect_commands
        original_verify = m.derive_verification_commands

        def spy_collect(workspace_root, run_summary=None):
            collect_calls.append(1)
            return original_collect(workspace_root, run_summary)

        def spy_verify(run_summary):
            verify_calls.append(1)
            return original_verify(run_summary)

        with patch.object(m, "derive_repo2run_collect_commands", side_effect=spy_collect), \
             patch.object(m, "derive_verification_commands", side_effect=spy_verify), \
             patch.object(m, "run_command", return_value=_make_docker_build_failure()), \
             patch.object(m, "repair_dockerfile_with_llm"), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=0)

        assert len(collect_calls) >= 1, (
            "derive_repo2run_collect_commands must be called at least once"
        )
        assert len(verify_calls) == 0, (
            "derive_verification_commands must NOT be called by _repair_and_rescore "
            "(use derive_repo2run_collect_commands instead)"
        )

    def test_filter_runtime_preparation_commands_strips_collect_cmds(self):
        """filter_runtime_preparation_commands must strip commands that are
        repo2run collect-only (e.g. 'pytest --collect-only -q').
        """
        import repo2run_repair_port as m

        # A collect-only command must be stripped
        collect_only = "pytest --collect-only -q"
        # A normal runtime command must be kept
        normal_cmd = "pip install -r requirements.txt"

        result = m.filter_runtime_preparation_commands([collect_only, normal_cmd])
        assert collect_only not in result, (
            f"filter_runtime_preparation_commands must strip '{collect_only}'"
        )
        assert normal_cmd in result, (
            f"filter_runtime_preparation_commands must keep '{normal_cmd}'"
        )

    def test_derive_repo2run_collect_commands_applies_filter(self, tmp_path):
        """derive_repo2run_collect_commands must apply filter_runtime_preparation_commands
        to strip collect-only commands from runtime_commands.
        """
        import repo2run_repair_port as m

        # A run_summary where runtime_preparation_commands includes a collect-only command
        run_summary = {
            "build_recipe": {
                "source": "agent",
                "build_commands": [],
                "runtime_commands": ["pytest --collect-only -q", "pip install -r requirements.txt"],
                "test_commands": ["pytest --collect-only -q"],
            }
        }

        runtime_cmds, test_cmds, source = m.derive_repo2run_collect_commands(
            tmp_path, run_summary
        )
        # pytest --collect-only must be filtered OUT of runtime_commands
        assert not any("--collect-only" in cmd for cmd in runtime_cmds), (
            f"filter must remove collect-only from runtime_commands; got {runtime_cmds}"
        )


class TestJunitAcquisitionViaNamedContainer:
    """evaluate_built_image must use --name + docker cp (not --rm) when
    junit_container_path is specified, so the XML survives container exit.
    """

    def test_named_container_used_when_junit_path_set(self):
        """When junit_container_path is provided, docker run must use --name
        (not --rm), and docker cp must be attempted to retrieve the XML.
        """
        import repo2run_repair_port as m

        docker_run_calls: list[list[str]] = []
        subprocess_calls: list[list[str]] = []

        def fake_run_command(cmd, **kwargs):
            docker_run_calls.append(list(cmd))
            return {
                "returncode": 0,
                "stdout": "1 passed in 0.1s",
                "stderr": "",
                "timed_out": False,
            }

        def fake_subprocess_run(cmd, **kwargs):
            subprocess_calls.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            return result

        import tempfile

        with patch.object(m, "run_command", side_effect=fake_run_command), \
             patch("subprocess.run", side_effect=fake_subprocess_run), \
             patch.object(m, "discover_internal_import_prefixes", return_value=None), \
             patch("tempfile.NamedTemporaryFile") as mock_tmpfile, \
             patch("pathlib.Path.read_text", return_value="<xml/>"), \
             patch("pathlib.Path.unlink"):
            # Set up the NamedTemporaryFile mock
            mock_tmp = MagicMock()
            mock_tmp.__enter__ = MagicMock(return_value=mock_tmp)
            mock_tmp.__exit__ = MagicMock(return_value=False)
            mock_tmp.name = "/tmp/fake_junit.xml"
            mock_tmpfile.return_value = mock_tmp

            m.evaluate_built_image(
                image_tag="test-image",
                workdir="/testbed",
                runtime_commands=[],
                test_commands=["pytest --junitxml=/tmp/repair_junit.xml"],
                cwd=Path("/tmp"),
                timeout_seconds=60,
                junit_container_path="/tmp/repair_junit.xml",
                attempt_index=0,
            )

        # docker run must NOT contain --rm
        assert docker_run_calls, "docker run must have been called"
        run_cmd = docker_run_calls[0]
        assert "--rm" not in run_cmd, (
            f"docker run must NOT use --rm when junit_container_path is set; got {run_cmd}"
        )
        # docker run must contain --name
        assert "--name" in run_cmd, (
            f"docker run must use --name when junit_container_path is set; got {run_cmd}"
        )

        # subprocess.run must have been called for docker cp
        cp_calls = [c for c in subprocess_calls if "cp" in c]
        assert cp_calls, (
            f"docker cp must be attempted for JUnit XML retrieval; subprocess calls: {subprocess_calls}"
        )

    def test_rm_used_when_no_junit_path(self):
        """Without junit_container_path, docker run must use --rm (standard path)."""
        import repo2run_repair_port as m

        docker_run_calls: list[list[str]] = []

        def fake_run_command(cmd, **kwargs):
            docker_run_calls.append(list(cmd))
            return {
                "returncode": 0,
                "stdout": "1 passed in 0.1s",
                "stderr": "",
                "timed_out": False,
            }

        with patch.object(m, "run_command", side_effect=fake_run_command), \
             patch.object(m, "discover_internal_import_prefixes", return_value=None):
            m.evaluate_built_image(
                image_tag="test-image",
                workdir="/testbed",
                runtime_commands=[],
                test_commands=["pytest"],
                cwd=Path("/tmp"),
                timeout_seconds=60,
                # No junit_container_path
            )

        assert docker_run_calls, "docker run must have been called"
        run_cmd = docker_run_calls[0]
        assert "--rm" in run_cmd, (
            f"docker run must use --rm when junit_container_path is NOT set; got {run_cmd}"
        )
        assert "--name" not in run_cmd, (
            f"docker run must NOT use --name when junit_container_path is not set; got {run_cmd}"
        )


class TestDockerPlatformFromRecipe:
    """Fix (d): docker_platform must be read from recipe['docker_platform']
    and passed to docker build and evaluate_built_image.
    """

    @pytest.fixture(autouse=True)
    def _point_workplace_root_at_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKERAGENT_ROOT", str(tmp_path))
        # Ensure env var doesn't interfere
        monkeypatch.delenv("DOCKER_DEFAULT_PLATFORM", raising=False)

    def test_docker_platform_read_from_recipe(self, tmp_path):
        """When recipe['docker_platform'] is set, it must be forwarded to
        the docker build command and evaluate_built_image.
        """
        import repo2run_repair_port as m

        # Recipe with docker_platform
        recipe = _make_recipe()
        recipe["docker_platform"] = "linux/amd64"

        root_path, out_dir = _setup_repair_tmpdir(tmp_path, recipe=recipe)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        build_cmds_seen: list[list[str]] = []
        eval_kwargs_seen: list[dict] = []

        def fake_run_command(cmd, **kwargs):
            build_cmds_seen.append(list(cmd))
            return _make_docker_build_success()

        def fake_evaluate(**kwargs):
            eval_kwargs_seen.append(dict(kwargs))
            return _make_effective_test_execution()

        with patch.object(m, "run_command", side_effect=fake_run_command), \
             patch.object(m, "evaluate_built_image", side_effect=fake_evaluate), \
             patch.object(m, "repair_dockerfile_with_llm"), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=0)

        assert build_cmds_seen, "docker build must have been called"
        build_cmd = build_cmds_seen[0]
        assert "--platform" in build_cmd, (
            f"docker build must include --platform when recipe['docker_platform'] is set; got {build_cmd}"
        )
        platform_idx = build_cmd.index("--platform")
        assert build_cmd[platform_idx + 1] == "linux/amd64", (
            f"Expected --platform linux/amd64, got {build_cmd[platform_idx + 1]!r}"
        )

        assert eval_kwargs_seen, "evaluate_built_image must have been called"
        assert eval_kwargs_seen[0].get("docker_platform") == "linux/amd64", (
            f"evaluate_built_image must receive docker_platform='linux/amd64'; "
            f"got {eval_kwargs_seen[0].get('docker_platform')!r}"
        )

    def test_docker_platform_none_when_recipe_has_no_platform(self, tmp_path):
        """When recipe has no docker_platform and env var is absent,
        docker build must NOT include --platform.
        """
        import repo2run_repair_port as m

        recipe = _make_recipe()  # no docker_platform key

        root_path, out_dir = _setup_repair_tmpdir(tmp_path, recipe=recipe)
        full_name = "owner/repo"
        out = _make_out_dict(str(tmp_path), full_name)

        build_cmds_seen: list[list[str]] = []

        def fake_run_command(cmd, **kwargs):
            build_cmds_seen.append(list(cmd))
            return _make_docker_build_failure()  # fail immediately so we don't need eval

        with patch.object(m, "run_command", side_effect=fake_run_command), \
             patch.object(m, "evaluate_built_image"), \
             patch.object(m, "repair_dockerfile_with_llm"), \
             patch("subprocess.run"):
            m._repair_and_rescore(out, str(tmp_path), full_name, "test-model", max_rounds=0)

        assert build_cmds_seen, "docker build must have been called"
        build_cmd = build_cmds_seen[0]
        assert "--platform" not in build_cmd, (
            f"docker build must NOT include --platform when recipe has no docker_platform; "
            f"got {build_cmd}"
        )
