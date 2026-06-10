# tests/test_maintainer_narrowed.py
from types import SimpleNamespace
from src.envstate.maintainer import parse_v1_maintainer_reply
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
    report = _report(cmds=(CommandRecord("pytest --collect-only", 0, "ok"),))
    out = parse_v1_maintainer_reply("", m, report)
    assert out.done_flag is True
