# tests/test_v1_repo_layout_cap.py
"""Tests for Fix 1: smart repo_layout derivation.

Covers:
  (a) _is_test_entry: True/False for various filenames and paths
  (b) _derive_repo_layout: tests/ buried at line 150 in a big docs/ subtree
      still appears in the result
  (c) Sentinel lines are excluded from the result
  (d) Result has <= 90 entries
  (e) De-dup: tests/ within first 60 lines is not duplicated
"""
from __future__ import annotations

import pytest

from agent import _is_test_entry, _derive_repo_layout


# ---------------------------------------------------------------------------
# (a) _is_test_entry true/false cases
# ---------------------------------------------------------------------------

class TestIsTestEntry:
    """_is_test_entry must match test-suite markers without false positives."""

    # True cases
    def test_bare_tests_dir(self):
        assert _is_test_entry("tests/") is True

    def test_bare_test_dir(self):
        assert _is_test_entry("test/") is True

    def test_test_foo_py(self):
        assert _is_test_entry("test_foo.py") is True

    def test_foo_test_py(self):
        assert _is_test_entry("foo_test.py") is True

    def test_conftest_py(self):
        assert _is_test_entry("conftest.py") is True

    def test_foo_tests_py(self):
        assert _is_test_entry("foo_tests.py") is True

    def test_nested_tests_dir(self):
        """A path like src/tests/ also signals a test suite."""
        assert _is_test_entry("src/tests/") is True

    def test_nested_test_foo_py(self):
        assert _is_test_entry("project/test_bar.py") is True

    # False cases — names that merely contain "test" as a substring
    def test_latest_py(self):
        assert _is_test_entry("latest.py") is False

    def test_fastest_py(self):
        assert _is_test_entry("fastest.py") is False

    def test_contest_py(self):
        assert _is_test_entry("contest.py") is False

    def test_manifest_in(self):
        assert _is_test_entry("manifest.in") is False

    def test_requirements_txt(self):
        assert _is_test_entry("requirements.txt") is False

    def test_setup_py(self):
        assert _is_test_entry("setup.py") is False

    def test_plain_txt_file(self):
        assert _is_test_entry("README.md") is False


# ---------------------------------------------------------------------------
# (b) tests/ buried past line 60 is still recovered
# ---------------------------------------------------------------------------

def _build_deep_structure(tests_entry: str = "tests/") -> str:
    """
    Construct a synthetic repo_structure string where tests/ appears after
    a large docs/ subtree.

    Layout:
      ------ begin repository structure ------
      repo/
      docs/a1.md
      docs/a2.md
      ...
      docs/a150.md
      tests/           ← the buried entry at line ~153
      ------ end repository structure ------
    """
    lines = ["------ begin repository structure ------", "repo/"]
    for i in range(1, 151):
        lines.append(f"  docs/a{i}.md")
    lines.append(f"  {tests_entry}")
    lines.append("------ end repository structure ------")
    return "\n".join(lines)


def test_buried_tests_dir_is_recovered():
    """tests/ at structure line ~153 (after 150 docs/ lines) must appear in the layout."""
    structure = _build_deep_structure("tests/")
    layout = _derive_repo_layout(structure)
    # The stripped entry value
    assert "tests/" in layout, (
        f"'tests/' not found in layout; got first 10: {layout[:10]}"
    )


def test_buried_test_foo_py_is_recovered():
    """test_foo.py buried past line 60 must be recovered."""
    lines = ["------ begin repository structure ------", "repo/"]
    for i in range(1, 151):
        lines.append(f"  docs/a{i}.md")
    lines.append("  test_integration.py")
    lines.append("------ end repository structure ------")
    structure = "\n".join(lines)
    layout = _derive_repo_layout(structure)
    assert "test_integration.py" in layout


def test_buried_conftest_py_is_recovered():
    """conftest.py buried past line 60 must be recovered."""
    lines = ["------ begin repository structure ------", "repo/"]
    for i in range(1, 151):
        lines.append(f"  docs/a{i}.md")
    lines.append("  conftest.py")
    lines.append("------ end repository structure ------")
    structure = "\n".join(lines)
    layout = _derive_repo_layout(structure)
    assert "conftest.py" in layout


# ---------------------------------------------------------------------------
# (c) Sentinel lines are excluded
# ---------------------------------------------------------------------------

def test_begin_sentinel_excluded():
    """'------ begin repository structure ------' must NOT appear in the layout."""
    structure = _build_deep_structure()
    layout = _derive_repo_layout(structure)
    sentinel = "------ begin repository structure ------"
    assert sentinel not in layout
    assert sentinel.lower() not in [e.lower() for e in layout]


def test_end_sentinel_excluded():
    """'------ end repository structure ------' must NOT appear in the layout."""
    structure = _build_deep_structure()
    layout = _derive_repo_layout(structure)
    sentinel = "------ end repository structure ------"
    assert sentinel not in layout
    assert sentinel.lower() not in [e.lower() for e in layout]


def test_sentinels_case_insensitive_excluded():
    """Sentinels in uppercase are also excluded."""
    structure = (
        "------ BEGIN REPOSITORY STRUCTURE ------\n"
        "repo/\n"
        "tests/\n"
        "------ END REPOSITORY STRUCTURE ------\n"
    )
    layout = _derive_repo_layout(structure)
    for entry in layout:
        assert "begin repository structure" not in entry.lower()
        assert "end repository structure" not in entry.lower()


# ---------------------------------------------------------------------------
# (d) Result has <= 90 entries
# ---------------------------------------------------------------------------

def test_result_has_at_most_90_entries():
    """60 context + up to 30 test lines = max 90 entries."""
    # Build a structure with 60 normal lines + 40 test-named lines beyond line 60
    lines = ["------ begin repository structure ------", "repo/"]
    for i in range(1, 61):
        lines.append(f"  src/module_{i}.py")
    for i in range(1, 41):
        lines.append(f"  test_extra_{i}.py")
    lines.append("------ end repository structure ------")
    structure = "\n".join(lines)
    layout = _derive_repo_layout(structure)
    assert len(layout) <= 90, f"Expected <= 90, got {len(layout)}"


def test_result_capped_at_30_extra_test_lines():
    """Even if there are 50 test-named lines past line 60, only up to 30 are included."""
    lines = ["------ begin repository structure ------", "repo/"]
    for i in range(1, 61):
        lines.append(f"  src/mod_{i}.py")
    for i in range(1, 51):   # 50 extra test lines
        lines.append(f"  test_extra_{i}.py")
    lines.append("------ end repository structure ------")
    structure = "\n".join(lines)
    layout = _derive_repo_layout(structure)
    # 60 context lines + at most 30 extra test lines
    assert len(layout) <= 90


# ---------------------------------------------------------------------------
# (e) De-dup: tests/ in first 60 lines is not duplicated
# ---------------------------------------------------------------------------

def test_no_duplicate_when_tests_in_first_60():
    """If tests/ appears within the first 60 lines, it must not appear twice."""
    lines = ["------ begin repository structure ------", "repo/"]
    lines.append("  tests/")
    for i in range(1, 60):
        lines.append(f"  src/mod_{i}.py")
    lines.append("  tests/")   # also appears past line 60
    lines.append("------ end repository structure ------")
    structure = "\n".join(lines)
    layout = _derive_repo_layout(structure)
    count = sum(1 for e in layout if e == "tests/")
    assert count == 1, f"'tests/' appears {count} times, expected exactly 1"


def test_general_dedup():
    """Duplicate non-test entries are deduplicated."""
    lines = ["repo/", "src/", "src/"]
    structure = "\n".join(lines)
    layout = _derive_repo_layout(structure)
    assert layout.count("src/") == 1


# ---------------------------------------------------------------------------
# Bonus: result is a tuple
# ---------------------------------------------------------------------------

def test_result_is_tuple():
    """_derive_repo_layout must return a tuple (matches WorldModelMap.repo_layout type)."""
    structure = "repo/\ntests/\n"
    layout = _derive_repo_layout(structure)
    assert isinstance(layout, tuple)


def test_empty_structure_returns_empty_tuple():
    """An empty structure string returns an empty tuple."""
    assert _derive_repo_layout("") == ()


def test_only_sentinels_returns_empty_tuple():
    """A structure that is only sentinels returns an empty tuple."""
    structure = (
        "------ begin repository structure ------\n"
        "------ end repository structure ------\n"
    )
    assert _derive_repo_layout(structure) == ()
