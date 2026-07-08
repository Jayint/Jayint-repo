import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.loop import run_react, RunResult, _observation
from src.react_repair.gate import TestOutcome
from src.react_repair.history import History
from src.react_repair.log import ReactLog
from src.react_repair.actions import Action


def test_observation_bounds_huge_test_output_keeping_tail():
    big = "EARLY_FAILURE\n" + ("x" * 50000) + "\n3 failed, 100 passed in 9s"
    obs = _observation(RunResult(True), TestOutcome(False, 100, 103, output=big))
    assert len(obs) < 12000                       # not the full 50k+ dumped into the prompt
    assert obs.rstrip().endswith("3 failed, 100 passed in 9s")   # diagnostic tail preserved

def test_observation_bounds_huge_build_failure_keeping_tail():
    big = ("y" * 40000) + "\nERROR: could not build wheel"
    obs = _observation(RunResult(False, failing_command="pip install foo", output=big), None)
    assert len(obs) < 12000
    assert obs.rstrip().endswith("ERROR: could not build wheel")

def test_observation_small_output_untouched():
    obs = _observation(RunResult(True), TestOutcome(True, 5, 5, output="5 passed in 0.1s"))
    assert "5 passed in 0.1s" in obs and "truncated" not in obs


class _ScriptedPlanner:
    """Emits a fixed queue of moves; ignores the prompt."""
    def __init__(self, moves): self.moves = list(moves)
    def plan(self, history, script, observation, graph):
        return "t", (self.moves.pop(0) if self.moves else Action("invalid")), {}


def _adapters(installed_needs, tests_need, script_box):
    """A FakeSandbox: build ok once `script` contains every token in `installed_needs`;
    tests pass once it also contains every token in `tests_need`."""
    def reset(): pass
    def run_script(script):
        script_box[0] = script
        missing = [t for t in installed_needs if t not in script]
        if missing:
            return RunResult(False, f"install {missing[0]}", f"{missing[0]}: not found")
        return RunResult(True)
    def certify(graph): return graph
    def exec_readonly(cmd): return (0, "probe-output")
    def run_tests():
        s = script_box[0]
        if all(t in s for t in tests_need):
            return TestOutcome(True, passed=5, executed=5, output="5 passed")
        return TestOutcome(False, passed=0, executed=1, output="ModuleNotFoundError: pytest_mock")
    return reset, run_script, certify, exec_readonly, run_tests


def _run(moves, installed_needs=(), tests_need=(), initial="pip install app\n"):
    box = [initial]
    reset, run_script, certify, ro, run_tests = _adapters(installed_needs, tests_need, box)
    log = ReactLog(silent=True)
    outcome, script, _ = run_react(
        object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
        run_tests=run_tests, planner=_ScriptedPlanner(moves), history=History(), log=log,
        max_steps=10, _initial_script=initial)
    return outcome, script, log


def test_green_first_pass_is_done():
    outcome, _, log = _run([], tests_need=())
    assert outcome == "DONE" and log.count("TEST_GATE") >= 1

def test_build_failure_then_patch_reaches_done():
    fix = Action("patch", new_script="pip install app\napt-get install -y libpq-dev\n")
    outcome, script, _ = _run([fix], installed_needs=("libpq-dev",))
    assert outcome == "DONE" and "libpq-dev" in script

def test_tests_fail_then_patch_reaches_done():
    fix = Action("patch", new_script="pip install app\npip install pytest_mock\n")
    outcome, script, _ = _run([fix], tests_need=("pytest_mock",))
    assert outcome == "DONE" and "pytest_mock" in script

def test_explore_is_a_free_turn_no_rerun_needed():
    fix = Action("patch", new_script="pip install app\napt-get install -y libpq-dev\n")
    outcome, _, log = _run([Action("explore", command="cat setup.py"), fix],
                           installed_needs=("libpq-dev",))
    assert outcome == "DONE" and log.count("EXPLORE") == 1

def test_unfixable_gives_up():
    outcome, _, _ = _run([], installed_needs=("libunobtainium",))
    assert outcome == "GIVEUP"

def test_plateau_stops_when_repairs_stop_helping():
    # Patches that never add the needed token -> pass count never rises -> PLATEAU (not a
    # 30-step thrash). Default patience is 2, so two no-gain patches trip it.
    p1 = Action("patch", new_script="pip install app\necho a\n")
    p2 = Action("patch", new_script="pip install app\necho b\n")
    p3 = Action("patch", new_script="pip install app\necho c\n")
    outcome, _, log = _run([p1, p2, p3], tests_need=("magic",))
    assert outcome == "PLATEAU" and log.count("PLATEAU") == 1

def test_plateau_tolerates_one_no_gain_then_reaches_done():
    # initial 2/5 (fail) -> patch1 no gain (stall 1, tolerated) -> patch2 gains to 5/5 -> DONE.
    outcomes = iter([
        TestOutcome(False, 2, 5, "2 passed, 3 failed"),   # initial build
        TestOutcome(False, 2, 5, "2 passed, 3 failed"),   # after patch1 (no gain)
        TestOutcome(True, 5, 5, "5 passed"),               # after patch2 (progress + ok)
    ])
    planner = _ScriptedPlanner([Action("patch", new_script="a\n"),
                                Action("patch", new_script="b\n")])
    outcome, _, _ = run_react(
        object(), reset=lambda: None, run_script=lambda s: RunResult(True),
        certify=lambda g: g, exec_readonly=lambda c: (0, ""),
        run_tests=lambda: next(outcomes), planner=planner, history=History(),
        log=ReactLog(silent=True), max_steps=10, _initial_script="x\n")
    assert outcome == "DONE"
