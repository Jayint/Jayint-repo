"""Scenarios: (initial_script, FakeSandbox, ScriptedPlanner, expected_outcome)."""
from __future__ import annotations

from src.react_repair.actions import Action
from src.eval.react_repair_eval.fake_sandbox import FakeSandbox

_INIT = "pip install app\n"


class ScriptedPlanner:
    """Deterministic: maps a substring of (observation + rendered history) to a move. The probe
    output lands in HISTORY, not the observation, so it must search both. Skips an explore it
    already ran and a patch identical to the current script, so each rule fires at most once."""
    def __init__(self, rules): self.rules = rules      # list[(needle, Action)]
    def plan(self, history, script, observation, graph):
        haystack = (observation or "") + "\n" + history.render()
        for needle, move in self.rules:
            if needle not in haystack:
                continue
            if move.kind == "patch" and move.new_script == script:
                continue                                # already applied
            if move.kind == "explore" and f"explore: {move.command}" in haystack:
                continue                                # already ran this probe
            return "t", move, {}
        return "t", Action("invalid"), {}


def scenario_green():
    return _INIT, FakeSandbox(), ScriptedPlanner([]), "DONE"

def scenario_build_fail_then_patch():
    fix = Action("patch", new_script=_INIT + "apt-get install -y libpq-dev\n")
    return (_INIT, FakeSandbox(install_tokens=("libpq-dev",)),
            ScriptedPlanner([("not found", fix)]), "DONE")

def scenario_tests_fail_then_patch():
    fix = Action("patch", new_script=_INIT + "pip install pytest_mock\n")
    return (_INIT, FakeSandbox(test_tokens=("pytest_mock",)),
            ScriptedPlanner([("ModuleNotFoundError", fix)]), "DONE")

def scenario_explore_then_patch():
    fix = Action("patch", new_script=_INIT + "apt-get install -y libpq-dev\n")
    return (_INIT, FakeSandbox(install_tokens=("libpq-dev",), probes={"cat": "needs libpq"}),
            ScriptedPlanner([("not found", Action("explore", command="cat setup.py")),
                             ("needs libpq", fix)]), "DONE")   # explore output (in history) routes to the fix

def scenario_unfixable_giveup():
    return (_INIT, FakeSandbox(install_tokens=("libunobtainium",)),
            ScriptedPlanner([]), "GIVEUP")
