import importlib.util
from pathlib import Path


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "runanything"
    / "src"
    / "libkit"
    / "tools"
    / "run_npm_test.py"
)
_SPEC = importlib.util.spec_from_file_location("rat_run_npm_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_ava_pass_summary_preserves_real_test_count():
    parsed = _MODULE.parse_test_output(
        "  17 tests passed\n",
        "npm",
        verbose=False,
    )

    assert parsed["success"] is True
    assert parsed["summary"] == {
        "total_tests": 17,
        "passed": 17,
        "failed": 0,
        "skipped": 0,
        "status": "SUCCESS",
        "test_time": "N/A",
    }


def test_ava_failure_summary_counts_passed_and_failed_tests():
    parsed = _MODULE.parse_test_output(
        "  16 tests passed\n  1 test failed\n",
        "npm",
        verbose=False,
    )

    assert parsed["success"] is False
    assert parsed["summary"]["total_tests"] == 17
    assert parsed["summary"]["passed"] == 16
    assert parsed["summary"]["failed"] == 1


def test_uvu_ansi_summary_preserves_real_test_count():
    parsed = _MODULE.parse_test_output(
        (
            "\x1b[1m\x1b[90m• \x1b[39m\x1b[32m  (744 / 744)\n"
            "\x1b[39m\n"
            "  Total:     744\x1b[32m\n"
            "  Passed:    744\x1b[39m\n"
            "  Skipped:   0\n"
            "  Duration:  502.55ms\n"
        ),
        "pnpm",
        verbose=False,
    )

    assert parsed["success"] is True
    assert parsed["summary"]["total_tests"] == 744
    assert parsed["summary"]["passed"] == 744
    assert parsed["summary"]["failed"] == 0
    assert parsed["summary"]["skipped"] == 0
    assert parsed["summary"]["status"] == "SUCCESS"


def test_uvu_summary_derives_failed_count():
    parsed = _MODULE.parse_test_output(
        "Total: 12\nPassed: 9\nSkipped: 1\n",
        "npm",
        verbose=False,
    )

    assert parsed["success"] is False
    assert parsed["summary"]["total_tests"] == 12
    assert parsed["summary"]["passed"] == 9
    assert parsed["summary"]["failed"] == 2
    assert parsed["summary"]["skipped"] == 1
    assert parsed["summary"]["status"] == "FAILURE"


def test_grunt_validation_suite_counts_completed_tasks():
    parsed = _MODULE.parse_test_output(
        (
            'Running "eslint:all" (eslint) task\n'
            "✖ 89 problems (0 errors, 89 warnings)\n"
            'Running "stylelint:all" (stylelint) task\n'
            ">> Linted 19 files without errors\n"
            'Running "banana:UploadWizard" (banana) task\n'
            ">> 2 message directories checked.\n"
            "Done.\n"
        ),
        "npm",
        verbose=False,
    )

    assert parsed["success"] is True
    assert parsed["summary"]["total_tests"] == 3
    assert parsed["summary"]["passed"] == 3
    assert parsed["summary"]["failed"] == 0
    assert parsed["summary"]["status"] == "SUCCESS"


def test_grunt_validation_suite_counts_an_aborted_task_as_failed():
    parsed = _MODULE.parse_test_output(
        (
            'Running "eslint:all" (eslint) task\n'
            'Warning: Task "eslint:all" failed. Use --force to continue.\n'
            "Aborted due to warnings.\n"
        ),
        "npm",
        verbose=False,
    )

    assert parsed["success"] is False
    assert parsed["summary"]["total_tests"] == 1
    assert parsed["summary"]["passed"] == 0
    assert parsed["summary"]["failed"] == 1
    assert parsed["summary"]["status"] == "FAILURE"
