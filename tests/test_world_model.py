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
