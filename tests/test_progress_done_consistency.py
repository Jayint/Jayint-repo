# tests/test_progress_done_consistency.py
"""progress['tests'] is derived from done_flag (§4.3). apply_deterministic derives
progress *before* the Maintainer sets the structural done_flag, so in the terminal
cycle 'tests' would lag a step (done_flag True but progress.tests False). The
Maintainer keeps the derived 'tests' layer consistent when it flips done_flag.
"""
from types import SimpleNamespace

from src.envstate.maintainer import parse_v1_maintainer_reply
from src.envstate.orchestrator import run_v1
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import (
    initial_map, merge_map, CommandRecord, TaskReport, PlannerDecision, Task,
)

_COLLECT = "pytest --collect-only -q --disable-warnings"


def _map(**kw):
    base = initial_map(base_image="python:3.12", workdir="/app", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, **kw)


def _report(cmds):
    return TaskReport("g", "done",
                      tuple(CommandRecord(c, rc, out) for c, rc, out in cmds), "")


def test_done_flag_also_flips_progress_tests_parsed_reply():
    out = parse_v1_maintainer_reply(
        '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```',
        _map(), _report([(_COLLECT, 0, "collected 3 items")]),
    )
    assert out.done_flag is True
    assert out.progress["tests"] is True


def test_done_flag_also_flips_progress_tests_empty_output():
    out = parse_v1_maintainer_reply("", _map(), _report([(_COLLECT, 0, "collected 3 items")]))
    assert out.done_flag is True
    assert out.progress["tests"] is True


def test_progress_tests_untouched_when_not_done():
    # no collect-only => done_flag stays False => tests stays False;
    # a stray LLM-proposed progress is still ignored (narrowed contract intact).
    m = _map(progress={"base": True, "system": True, "runtime": True,
                       "deps": True, "build": True, "tests": False})
    out = parse_v1_maintainer_reply(
        '```json\n{"open_problems": [], "resolved": [], "notes": [], "progress": {"tests": true}}\n```',
        m, _report([("pip install flask", 0, "ok")]),
    )
    assert out.done_flag is False
    assert out.progress["tests"] is False


def test_run_v1_terminal_map_progress_tests_matches_done_flag():
    """The bug: final map had done_flag True but progress.tests False because the
    per-cycle fold ran before the Maintainer set done_flag."""
    class _Planner:
        def __init__(self): self.n = 0
        def decide(self, m):
            self.n += 1
            return (PlannerDecision(action="task", task=Task("g", "d", "tests", ()))
                    if self.n == 1 else PlannerDecision(action="done"))

    class _Build:
        def run(self, task, ex, ledger, step_offset=0):
            return TaskReport("g", "done", (CommandRecord(_COLLECT, 0, "collected 3 items"),), "ok")

    class _Maint:
        def update(self, m, report):
            return parse_v1_maintainer_reply("", m, report)

    probe = lambda: SimpleNamespace(installed=(), env={"python_version": "Python 3.12.1"})
    man = SimpleNamespace(build_system="pip", required=())
    final, reason = run_v1(_Planner(), _Build(), _Maint(), _map(), ActionLedger(),
                           lambda c: (True, ""), max_cycles=3, probe=probe, manifest=man)
    assert reason == "done_flag"
    assert final.done_flag is True
    assert final.progress["tests"] is True
