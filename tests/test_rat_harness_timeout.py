import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value, expected", [
    (None, 1800),
    ("2400", 2400),
    ("0", 1800),
    ("-2", 1800),
    ("invalid", 1800),
])
def test_pytest_timeout_env_is_positive(monkeypatch, value, expected):
    module = _load("rat_run_pytest", "runanything/src/libkit/tools/run_pytest.py")
    if value is None:
        monkeypatch.delenv("RAT_PYTEST_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("RAT_PYTEST_TIMEOUT", value)
    assert module._timeout_from_env("RAT_PYTEST_TIMEOUT", 1800) == expected


@pytest.mark.parametrize("value, expected", [
    (None, 300),
    ("450", 450),
    ("0", 300),
    ("bad", 300),
])
def test_collect_timeout_env_is_positive(monkeypatch, value, expected):
    module = _load(
        "rat_run_pytest_collect", "runanything/src/libkit/tools/run_pytest_collect.py"
    )
    if value is None:
        monkeypatch.delenv("RAT_PYTEST_COLLECT_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("RAT_PYTEST_COLLECT_TIMEOUT", value)
    assert module._timeout_from_env("RAT_PYTEST_COLLECT_TIMEOUT", 300) == expected


def test_junit_summary_counts_successful_subtests_from_suite_totals(tmp_path):
    module = _load("rat_run_pytest_subtests", "runanything/src/libkit/tools/run_pytest.py")
    report = tmp_path / "junit.xml"
    report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
        <testsuites>
          <testsuite name="pytest" tests="7" failures="1" errors="0" skipped="1">
            <testcase classname="tests.test_demo" name="test_parent_pass" />
            <testcase classname="tests.test_demo" name="test_parent_fail">
              <failure message="assertion failed">AssertionError</failure>
            </testcase>
            <testcase classname="tests.test_demo" name="test_parent_skip">
              <skipped message="not available" />
            </testcase>
          </testsuite>
        </testsuites>
        """,
        encoding="utf-8",
    )

    parsed = module.parse_junit_xml(str(report))

    assert parsed["summary"] == {
        "total_tests": 7,
        "passed": 5,
        "failed": 1,
        "skipped": 1,
        "errors": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    assert len(parsed["failed_tests"]) == 1
