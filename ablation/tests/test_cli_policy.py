from __future__ import annotations

import pytest

from ablation.run_execute_only import (
    _parse_environment,
    _sandbox_compatible_auto_image,
    build_parser,
)


def test_cli_defaults_to_fifty_turns_per_decision():
    args = build_parser().parse_args(["/tmp/repo"])
    assert args.max_turns_per_decision == 50


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ("python:3-alpine", "python:3-slim"),
        ("python:3.11-alpine", "python:3.11-slim"),
        ("python:3.11.9-alpine3.20", "python:3.11.9-slim"),
    ],
)
def test_auto_python_alpine_is_normalized_for_bash(selected, expected):
    image, reason = _sandbox_compatible_auto_image(selected, automatic=True)
    assert image == expected
    assert selected in reason
    assert expected in reason


def test_explicit_alpine_and_compatible_auto_images_are_unchanged():
    assert _sandbox_compatible_auto_image(
        "python:3-alpine", automatic=False
    ) == ("python:3-alpine", None)
    assert _sandbox_compatible_auto_image(
        "python:3.11-slim", automatic=True
    ) == ("python:3.11-slim", None)


def test_environment_parser_allows_non_filtering_values():
    assert _parse_environment(
        [
            "DATABASE_URL=postgresql://localhost/demo",
            "PYTEST_ADDOPTS=--import-mode=importlib",
        ]
    ) == {
        "DATABASE_URL": "postgresql://localhost/demo",
        "PYTEST_ADDOPTS": "--import-mode=importlib",
    }


@pytest.mark.parametrize(
    "entry",
    [
        "PYTEST_ADDOPTS=--ignore=tests/test_bad.py",
        "PYTEST_ADDOPTS=-k smoke",
        "TESTBRIDGE_TEST_ONLY=ParserTests",
        "VSTEST_TESTCASEFILTER=Category=Fast",
        "JEST_TEST_NAME_PATTERN=smoke",
        "GOFLAGS=-run TestOne",
        "NODE_OPTIONS=--require /tmp/test-hook.js",
        "INVALID-KEY=value",
    ],
)
def test_environment_parser_rejects_test_filter_and_injection_controls(entry):
    with pytest.raises(ValueError):
        _parse_environment([entry])
