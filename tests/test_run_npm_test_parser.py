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
