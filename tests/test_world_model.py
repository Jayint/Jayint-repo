# tests/test_world_model.py
"""Unit tests for src/envstate/world_model.py — frozen dataclasses and pure helpers.

Covers:
  - All seven frozen dataclasses can be instantiated and are immutable.
  - initial_map() produces a correct zero-state WorldModelMap.
  - merge_map() produces a new map with only the supplied fields replaced.
  - done_flag defaults to False.
  - JSON serialization helpers round-trip every dataclass losslessly.
  - WorldModelMap.progress is a plain dict (merge_map always makes a new one).
"""
from __future__ import annotations

import dataclasses
import json
import pytest

# ── import targets (will fail until world_model.py exists) ─────────────────
from src.envstate.world_model import (
    CommandRecord,
    Fact,
    OpenProblem,
    PlannerDecision,
    Task,
    TaskReport,
    WorldModelMap,
    initial_map,
    map_to_dict,
    map_from_dict,
    merge_map,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fact(name: str, detail: str = "") -> Fact:
    return Fact(name=name, detail=detail)


def _open_problem(sig: str = "ModuleNotFoundError: psycopg2") -> OpenProblem:
    return OpenProblem(
        signature=sig,
        interpretation="psycopg2 not installed",
        layer="deps",
    )


def _minimal_map() -> WorldModelMap:
    return initial_map(
        base_image="python:3.12-slim",
        workdir="/app",
        language="python 3.12",
        build_system="pip",
        repo_layout=("tests/", "src/", "requirements.txt"),
    )


# ---------------------------------------------------------------------------
# Task 1 tests — frozen dataclass immutability
# ---------------------------------------------------------------------------

class TestFrozenDataclasses:
    def test_fact_is_frozen(self):
        f = Fact(name="flask", detail="3.0.0")
        with pytest.raises(dataclasses.FrozenInstanceError):
            f.name = "django"  # type: ignore[misc]

    def test_fact_default_detail_empty(self):
        f = Fact(name="pytest")
        assert f.detail == ""

    def test_open_problem_is_frozen(self):
        op = OpenProblem(
            signature="ImportError: no module named x",
            interpretation="x not installed",
            layer="deps",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            op.layer = "runtime"  # type: ignore[misc]

    def test_open_problem_out_of_scope_defaults_false(self):
        op = _open_problem()
        assert op.out_of_scope is False

    def test_world_model_map_is_frozen(self):
        m = _minimal_map()
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.done_flag = True  # type: ignore[misc]

    def test_task_is_frozen(self):
        t = Task(
            goal="install flask",
            done_when="python -c 'import flask' exits 0",
            layer="deps",
            facts=("base_image=python:3.12-slim",),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            t.goal = "uninstall flask"  # type: ignore[misc]

    def test_task_facts_is_tuple(self):
        t = Task(
            goal="install flask",
            done_when="import flask works",
            layer="deps",
            facts=("base_image=python:3.12-slim", "workdir=/app"),
        )
        assert isinstance(t.facts, tuple)

    def test_planner_decision_is_frozen(self):
        d = PlannerDecision(action="done", reason="all layers green")
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.action = "giveup"  # type: ignore[misc]

    def test_planner_decision_task_defaults_none(self):
        d = PlannerDecision(action="done")
        assert d.task is None

    def test_planner_decision_reason_defaults_empty(self):
        d = PlannerDecision(action="task", task=Task(
            goal="g", done_when="d", layer="deps", facts=()
        ))
        assert d.reason == ""

    def test_command_record_is_frozen(self):
        cr = CommandRecord(cmd="pip install flask", rc=0, output="Successfully installed flask")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cr.rc = 1  # type: ignore[misc]

    def test_task_report_is_frozen(self):
        tr = TaskReport(
            task_goal="install flask",
            status="done",
            commands=(CommandRecord(cmd="pip install flask", rc=0, output="ok"),),
            learning="flask installed cleanly",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            tr.status = "blocked"  # type: ignore[misc]

    def test_task_report_commands_is_tuple(self):
        cr = CommandRecord(cmd="pip install flask", rc=0, output="ok")
        tr = TaskReport(
            task_goal="install flask",
            status="done",
            commands=(cr,),
            learning="done",
        )
        assert isinstance(tr.commands, tuple)


class TestInitialMap:
    def test_returns_world_model_map_instance(self):
        m = _minimal_map()
        assert isinstance(m, WorldModelMap)

    def test_base_image_and_workdir_stored(self):
        m = initial_map(
            base_image="python:3.12-slim",
            workdir="/app",
            language="python 3.12",
            build_system="pip",
            repo_layout=("tests/", "requirements.txt"),
        )
        assert m.base_image == "python:3.12-slim"
        assert m.workdir == "/app"

    def test_language_and_build_system_stored(self):
        m = initial_map(
            base_image="python:3.11-slim",
            workdir="/workspace",
            language="python 3.11",
            build_system="poetry",
            repo_layout=(),
        )
        assert m.language == "python 3.11"
        assert m.build_system == "poetry"

    def test_repo_layout_stored_as_tuple(self):
        m = initial_map(
            base_image="python:3.12-slim",
            workdir="/app",
            language="python 3.12",
            build_system="pip",
            repo_layout=("tests/", "src/", "pyproject.toml"),
        )
        assert m.repo_layout == ("tests/", "src/", "pyproject.toml")
        assert isinstance(m.repo_layout, tuple)

    def test_done_flag_defaults_false(self):
        m = _minimal_map()
        assert m.done_flag is False

    def test_installed_starts_empty(self):
        m = _minimal_map()
        assert m.installed == ()

    def test_open_problems_starts_empty(self):
        m = _minimal_map()
        assert m.open_problems == ()

    def test_notes_starts_empty(self):
        m = _minimal_map()
        assert m.notes == ()

    def test_required_defaults_to_empty_tuple(self):
        m = _minimal_map()
        assert m.required == ()

    def test_required_can_be_supplied(self):
        req = (_fact("flask"), _fact("pytest"))
        m = initial_map(
            base_image="python:3.12-slim",
            workdir="/app",
            language="python 3.12",
            build_system="pip",
            repo_layout=(),
            required=req,
        )
        assert m.required == req

    def test_progress_has_all_six_layers(self):
        m = _minimal_map()
        assert set(m.progress.keys()) == {"base", "system", "runtime", "deps", "build", "tests"}

    def test_progress_all_false_at_start(self):
        m = _minimal_map()
        assert all(v is False for v in m.progress.values())

    def test_progress_is_dict_not_frozen(self):
        # progress is a plain dict by contract — merge_map handles copy-on-write
        m = _minimal_map()
        assert isinstance(m.progress, dict)

    def test_two_calls_produce_independent_progress_dicts(self):
        m1 = _minimal_map()
        m2 = _minimal_map()
        # mutating the progress dict of m1 must not affect m2
        m1.progress["base"] = True
        assert m2.progress["base"] is False


class TestMergeMap:
    def test_returns_new_instance(self):
        m = _minimal_map()
        m2 = merge_map(m, done_flag=False)
        assert m2 is not m

    def test_done_flag_can_be_set_true(self):
        m = _minimal_map()
        m2 = merge_map(m, done_flag=True)
        assert m2.done_flag is True

    def test_original_done_flag_unchanged(self):
        m = _minimal_map()
        merge_map(m, done_flag=True)
        assert m.done_flag is False

    def test_installed_replaced(self):
        m = _minimal_map()
        facts = (_fact("flask", "3.0.0"), _fact("pytest", "8.0"))
        m2 = merge_map(m, installed=facts)
        assert m2.installed == facts

    def test_installed_original_unchanged(self):
        m = _minimal_map()
        merge_map(m, installed=(_fact("flask"),))
        assert m.installed == ()

    def test_open_problems_replaced(self):
        m = _minimal_map()
        ops = (_open_problem(),)
        m2 = merge_map(m, open_problems=ops)
        assert m2.open_problems == ops

    def test_notes_replaced(self):
        m = _minimal_map()
        notes = ("do not use psycopg2-binary",)
        m2 = merge_map(m, notes=notes)
        assert m2.notes == notes

    def test_required_replaced(self):
        m = _minimal_map()
        req = (_fact("flask"),)
        m2 = merge_map(m, required=req)
        assert m2.required == req

    def test_progress_replaced(self):
        m = _minimal_map()
        new_progress = {
            "base": True, "system": True, "runtime": True,
            "deps": False, "build": False, "tests": False,
        }
        m2 = merge_map(m, progress=new_progress)
        assert m2.progress["base"] is True
        assert m2.progress["deps"] is False

    def test_progress_is_independent_copy(self):
        m = _minimal_map()
        new_progress = {
            "base": True, "system": False, "runtime": False,
            "deps": False, "build": False, "tests": False,
        }
        m2 = merge_map(m, progress=new_progress)
        # mutating the dict we passed in must not affect m2
        new_progress["base"] = False
        assert m2.progress["base"] is True

    def test_unspecified_fields_copied_unchanged(self):
        original_required = (_fact("flask"),)
        m = initial_map(
            base_image="python:3.12-slim",
            workdir="/app",
            language="python 3.12",
            build_system="pip",
            repo_layout=("tests/",),
            required=original_required,
        )
        m2 = merge_map(m, done_flag=True)
        # base_image, workdir, language, build_system, repo_layout, required all unchanged
        assert m2.base_image == "python:3.12-slim"
        assert m2.workdir == "/app"
        assert m2.language == "python 3.12"
        assert m2.build_system == "pip"
        assert m2.repo_layout == ("tests/",)
        assert m2.required == original_required

    def test_none_kwargs_leave_fields_unchanged(self):
        ops = (_open_problem(),)
        m = merge_map(_minimal_map(), open_problems=ops)
        m2 = merge_map(m, done_flag=True)  # open_problems not supplied
        assert m2.open_problems == ops

    def test_chain_two_merges(self):
        m0 = _minimal_map()
        m1 = merge_map(m0, installed=(_fact("flask"),))
        m2 = merge_map(m1, installed=(_fact("flask"), _fact("pytest")))
        assert len(m2.installed) == 2
        assert len(m1.installed) == 1  # m1 unchanged

    def test_merge_map_result_is_still_frozen(self):
        m = _minimal_map()
        m2 = merge_map(m, done_flag=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            m2.done_flag = False  # type: ignore[misc]
