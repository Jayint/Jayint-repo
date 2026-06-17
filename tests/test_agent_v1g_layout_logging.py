# tests/test_agent_v1g_layout_logging.py
"""Focused unit tests for BUG-1, BUG-5 (extracted helpers).

BUG-1 — _scan_workplace_structure
  (a) Scans a real temp dir with pyproject.toml + tests/ and produces a non-
      empty string that _derive_repo_layout turns into a non-empty layout.
  (b) Excludes the .git directory from the output.
  (c) Returns '' for a non-existent path.
  (d) Is capped at the specified cap value.

BUG-5 — _decision_target_node_ids
  (a) Returns flattened step targets for an apply_recipe_patch PlannerDecision.
  (b) Returns task.target_node_ids for a legacy task decision.
  (c) Returns [] for done / giveup (no task, no recipe_patch).
  (d) Returns [] when decision is None.
"""
from __future__ import annotations

import os
import tempfile
import pathlib

import pytest

from agent import _scan_workplace_structure, _derive_repo_layout, _decision_target_node_ids


# ---------------------------------------------------------------------------
# Helpers: lightweight PlannerDecision stubs (no src.envstate imports needed)
# ---------------------------------------------------------------------------

class _Task:
    def __init__(self, target_node_ids=()):
        self.target_node_ids = tuple(target_node_ids)


class _RecipeStep:
    def __init__(self, target_node_ids=()):
        self.target_node_ids = tuple(target_node_ids)


class _RecipePatch:
    def __init__(self, steps=()):
        self.steps = tuple(steps)


class _PlannerDecision:
    def __init__(self, action, task=None, recipe_patch=None, reason=""):
        self.action = action
        self.task = task
        self.recipe_patch = recipe_patch
        self.reason = reason


# ---------------------------------------------------------------------------
# BUG-1: _scan_workplace_structure
# ---------------------------------------------------------------------------

class TestScanWorkplaceStructure:
    """_scan_workplace_structure on a temp dir."""

    def _make_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """Create a minimal Python project layout."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='foo'\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_foo.py").write_text("def test_it(): pass\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").write_text("x = 1\n")
        # Add a .git dir that must be excluded
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        return tmp_path

    def test_non_empty_structure_from_real_dir(self, tmp_path):
        """Scanning a valid project returns a non-empty string."""
        self._make_repo(tmp_path)
        result = _scan_workplace_structure(str(tmp_path))
        assert result, "Expected non-empty structure from a populated temp dir"

    def test_derive_repo_layout_produces_non_empty_layout(self, tmp_path):
        """_derive_repo_layout on the scanned structure gives a non-empty tuple."""
        self._make_repo(tmp_path)
        structure = _scan_workplace_structure(str(tmp_path))
        layout = _derive_repo_layout(structure)
        assert len(layout) > 0, "Expected at least one entry in repo_layout"

    def test_pyproject_toml_in_layout(self, tmp_path):
        """pyproject.toml must appear somewhere in the layout."""
        self._make_repo(tmp_path)
        structure = _scan_workplace_structure(str(tmp_path))
        layout = _derive_repo_layout(structure)
        # The path may be relative-prefixed; check any entry ends with pyproject.toml
        assert any("pyproject.toml" in e for e in layout), (
            f"pyproject.toml missing from layout: {layout}"
        )

    def test_tests_dir_in_structure(self, tmp_path):
        """tests/ directory must appear in the raw structure string."""
        self._make_repo(tmp_path)
        structure = _scan_workplace_structure(str(tmp_path))
        assert "tests" in structure, "Expected 'tests' somewhere in the structure"

    def test_git_dir_excluded(self, tmp_path):
        """The .git directory must not appear in the output."""
        self._make_repo(tmp_path)
        structure = _scan_workplace_structure(str(tmp_path))
        lines = structure.splitlines()
        for line in lines:
            parts = pathlib.PurePosixPath(line.replace(os.sep, "/")).parts
            assert ".git" not in parts, (
                f"'.git' must be excluded but found in line: {line!r}"
            )

    def test_nonexistent_path_returns_empty_string(self):
        """A non-existent workplace returns an empty string (no exception)."""
        result = _scan_workplace_structure("/nonexistent/path/that/does/not/exist")
        assert result == ""

    def test_cap_is_respected(self, tmp_path):
        """The number of lines must not exceed the cap."""
        # Create more files than the cap
        (tmp_path / "src").mkdir()
        for i in range(50):
            (tmp_path / "src" / f"module_{i}.py").write_text(f"x={i}\n")
        cap = 30
        structure = _scan_workplace_structure(str(tmp_path), cap=cap)
        lines = [ln for ln in structure.splitlines() if ln]
        assert len(lines) <= cap, (
            f"Expected <= {cap} lines, got {len(lines)}"
        )

    def test_result_is_str(self, tmp_path):
        """_scan_workplace_structure always returns a str."""
        result = _scan_workplace_structure(str(tmp_path))
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# BUG-5: _decision_target_node_ids
# ---------------------------------------------------------------------------

class TestDecisionTargetNodeIds:
    """_decision_target_node_ids extracts targets for all decision types."""

    def test_task_decision_returns_task_targets(self):
        """A 'task' decision with target_node_ids returns them."""
        task = _Task(target_node_ids=["contract:A", "blocker:B"])
        decision = _PlannerDecision(action="task", task=task)
        result = _decision_target_node_ids(decision)
        assert result == ["contract:A", "blocker:B"]

    def test_task_decision_empty_targets_returns_empty_list(self):
        """A 'task' decision with no target_node_ids returns []."""
        task = _Task(target_node_ids=[])
        decision = _PlannerDecision(action="task", task=task)
        result = _decision_target_node_ids(decision)
        assert result == []

    def test_recipe_patch_returns_flattened_step_targets(self):
        """An apply_recipe_patch decision returns all step targets flattened."""
        step1 = _RecipeStep(target_node_ids=["contract:X", "contract:Y"])
        step2 = _RecipeStep(target_node_ids=["blocker:Z"])
        patch = _RecipePatch(steps=[step1, step2])
        decision = _PlannerDecision(action="apply_recipe_patch", recipe_patch=patch)
        result = _decision_target_node_ids(decision)
        assert result == ["contract:X", "contract:Y", "blocker:Z"]

    def test_recipe_patch_single_step(self):
        """A single-step recipe patch works correctly."""
        step = _RecipeStep(target_node_ids=["contract:A"])
        patch = _RecipePatch(steps=[step])
        decision = _PlannerDecision(action="apply_recipe_patch", recipe_patch=patch)
        result = _decision_target_node_ids(decision)
        assert result == ["contract:A"]

    def test_recipe_patch_empty_steps_returns_empty(self):
        """A recipe_patch with no steps returns []."""
        patch = _RecipePatch(steps=[])
        decision = _PlannerDecision(action="apply_recipe_patch", recipe_patch=patch)
        result = _decision_target_node_ids(decision)
        assert result == []

    def test_done_decision_returns_empty(self):
        """A 'done' decision (no task, no recipe_patch) returns []."""
        decision = _PlannerDecision(action="done", reason="all done")
        result = _decision_target_node_ids(decision)
        assert result == []

    def test_giveup_decision_returns_empty(self):
        """A 'giveup' decision returns []."""
        decision = _PlannerDecision(action="giveup", reason="stuck")
        result = _decision_target_node_ids(decision)
        assert result == []

    def test_none_returns_empty(self):
        """None input returns []."""
        result = _decision_target_node_ids(None)
        assert result == []

    def test_result_is_list(self):
        """The return type is always a list."""
        task = _Task(target_node_ids=["contract:A"])
        decision = _PlannerDecision(action="task", task=task)
        result = _decision_target_node_ids(decision)
        assert isinstance(result, list)

    def test_result_is_list_for_recipe(self):
        """The return type is a list even for recipe_patch."""
        step = _RecipeStep(target_node_ids=["contract:B"])
        patch = _RecipePatch(steps=[step])
        decision = _PlannerDecision(action="apply_recipe_patch", recipe_patch=patch)
        result = _decision_target_node_ids(decision)
        assert isinstance(result, list)
