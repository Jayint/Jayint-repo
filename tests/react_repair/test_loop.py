import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.loop import run_react, RunResult, _observation, _emit_tokens
from src.react_repair.gate import TestOutcome
from src.react_repair.history import History
from src.react_repair.log import ReactLog
from src.react_repair.actions import Action, EditOp


def test_emit_tokens_prints_runlog_format(capsys):
    _emit_tokens({"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
    out = capsys.readouterr().out
    assert "[Tokens] Input: 120, Output: 30, Total: 150" in out

def test_emit_tokens_derives_total_when_absent(capsys):
    _emit_tokens({"input_tokens": 10, "output_tokens": 5})
    assert "Total: 15" in capsys.readouterr().out

def test_emit_tokens_noop_when_empty(capsys):
    _emit_tokens(None)
    _emit_tokens({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    assert capsys.readouterr().out == ""


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

def test_observation_renders_error_breakdown_in_test_phase():
    # Once the build is green the localization is per-CAUSE: a ranked histogram of what the tests
    # fail on (parsed from the traceback blocks), so the agent targets the dominant blocker instead
    # of the last-printed traceback.
    out = ("==================================== ERRORS ====================================\n"
           "___ ERROR collecting a/test_x.py ___\n"
           "E   ModuleNotFoundError: No module named 'pkg_resources'\n"
           "___ ERROR collecting b/test_y.py ___\n"
           "E   ModuleNotFoundError: No module named 'pkg_resources'\n"
           "=================================== FAILURES ===================================\n"
           "___ test_t ___\n"
           "c/test_z.py:2: AssertionError\n"
           "=== 3 passed, 2 errors, 1 failed in 1s ===\n")
    obs = _observation(RunResult(True), TestOutcome(False, 3, 6, output=out))
    assert "3/6 passed" in obs                                          # headline kept
    assert "Top failure causes" in obs
    assert "2 × ModuleNotFoundError" in obs and "pkg_resources" in obs  # dominant cause, counted
    assert "pytest output (tail)" in obs                                # raw tail still appended

def test_observation_falls_back_to_tail_when_no_failure_summary():
    # all-passed / unparseable output has no summary lines → the old plain body, no breakdown block.
    obs = _observation(RunResult(True), TestOutcome(True, 5, 5, output="5 passed in 0.1s"))
    assert "Top failure causes" not in obs and "5 passed in 0.1s" in obs

def test_observation_includes_failing_line_number():
    obs = _observation(RunResult(False, failing_command="pip install psycopg2",
                                 output="fatal error", lineno=40), None)
    assert "line 40" in obs and "pip install psycopg2" in obs


def test_added_lines_shows_meaningful_additions_order_free():
    from src.react_repair.loop import _added_lines
    s = _added_lines("pip install app\n",
                     "pip install app\napt-get install -y libpq-dev\npip install psycopg2\n")
    assert "+apt-get install -y libpq-dev" in s and "+pip install psycopg2" in s
    assert "pip install app" not in s               # unchanged line not shown

def test_added_lines_skips_blank_and_comment_lines():
    from src.react_repair.loop import _added_lines
    assert _added_lines("a\n", "a\n\n# a comment\nb\n") == "+b"

def test_added_lines_collapses_big_rewrite_to_a_count():
    from src.react_repair.loop import _added_lines
    assert _added_lines("a\nb\nc\n", "x\ny\nz\nw\n") == "rewrote +4/-3 lines"   # 4 added > cap 3

def test_added_lines_empty_when_no_change():
    from src.react_repair.loop import _added_lines
    assert _added_lines("a\nb\n", "a\nb\n") == ""


class _ScriptedPlanner:
    """Emits a fixed queue of moves; ignores the prompt."""
    def __init__(self, moves): self.moves = list(moves)
    def plan(self, history, script, observation, graph, fail_lineno=None, turn=None,
             max_turns=None, rejection=None):
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

def test_edit_op_applies_by_line_and_reaches_done():
    # An `edit` action mutates the script by line (insert) and rebuilds — same path as a patch.
    ins = Action("edit", edit=EditOp("insert", 1, 1, "apt-get install -y libpq-dev"))
    outcome, script, log = _run([ins], installed_needs=("libpq-dev",))
    assert outcome == "DONE" and "libpq-dev" in script and log.count("EDIT") == 1

def test_edit_out_of_range_is_invalid_and_never_regresses_seed():
    # An out-of-range edit is rejected (no rebuild) and the seed is retained (keep-best floor).
    bad = Action("edit", edit=EditOp("replace", 99, 99, "junk"))
    outcome, script, _ = _run([bad], tests_need=("magic",))
    assert outcome == "GIVEUP" and script == "pip install app\n"

def test_loop_passes_turn_budget_to_planner():
    # The loop feeds the live turn counter (1-based) + max to the planner every turn.
    seen = []
    class _P:
        def plan(self, history, script, observation, graph, fail_lineno=None, turn=None,
                 max_turns=None, rejection=None):
            seen.append((turn, max_turns)); return "t", Action("explore", command="ls"), {}
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    run_react(object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
              run_tests=run_tests, planner=_P(), history=History(), log=ReactLog(silent=True),
              max_steps=3, _initial_script="pip install app\n")
    assert seen == [(1, 3), (2, 3), (3, 3)]

def test_invalid_move_retried_in_place_not_recorded_when_corrected():
    # A tool misuse is a harness error, not a repair step: if the agent corrects it on retry, the
    # misuse must NOT appear in history and must NOT consume a turn.
    bad = Action("explore", command="pip install libpq-dev")     # not read-only → rejected
    good = Action("edit", edit=EditOp("insert", 1, 1, "apt-get install -y libpq-dev"))
    hist = History()
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    outcome, script, _ = run_react(
        object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
        run_tests=run_tests, planner=_ScriptedPlanner([bad, good]), history=hist,
        log=ReactLog(silent=True), max_steps=5, _initial_script="pip install app\n")
    from src.react_repair.history_view import render_history
    assert "invalid move" not in render_history(hist.steps)   # corrected in-place → not in history
    assert outcome == "DONE" and "libpq-dev" in script         # the retried edit applied

def test_invalid_move_exhausts_retries_then_records_one_invalid():
    # An agent that only emits misuse: after the retry cap, exactly ONE invalid step is recorded
    # (visible + traced), not silently dropped and not one-per-retry.
    bad = Action("explore", command="pip install x")            # always non-read-only
    hist = History()
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    run_react(object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
              run_tests=run_tests, planner=_ScriptedPlanner([bad] * 10), history=hist,
              log=ReactLog(silent=True), max_steps=1, _initial_script="pip install app\n")
    assert len([s for s in hist.steps if s.action_summary == "invalid"]) == 1

def test_retry_passes_rejection_hint_to_planner():
    seen = []
    class _P:
        def __init__(self): self.n = 0
        def plan(self, history, script, observation, graph, fail_lineno=None, turn=None,
                 max_turns=None, rejection=None):
            seen.append(rejection); self.n += 1
            if self.n == 1:
                return "t", Action("explore", command="pip install x"), {}   # misuse
            return "t", Action("edit", edit=EditOp("insert", 1, 1, "apt-get install -y libpq-dev")), {}
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    run_react(object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
              run_tests=run_tests, planner=_P(), history=History(), log=ReactLog(silent=True),
              max_steps=2, _initial_script="pip install app\n")
    assert seen[0] is None                                   # first call: no rejection
    assert seen[1] is not None and "edit()" in seen[1]       # retry carries the misuse hint

def test_non_readonly_explore_is_invalid_and_surfaces_edit_guidance():
    # `pip install` via explore is not read-only → rejected. The agent must be pointed at edit()
    # (not a dead stale reminder) AND actually SEE it (render the latest invalid step's body).
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    hist = History(); log = ReactLog(silent=True)
    run_react(object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
              run_tests=run_tests,
              planner=_ScriptedPlanner([Action("explore", command="pip install libpq-dev")]),
              history=hist, log=log, max_steps=1, _initial_script="pip install app\n")
    from src.react_repair.history_view import render_history
    view = render_history(hist.steps)
    assert "invalid move" in view.lower()
    assert "edit()" in view and "won't persist" in view

def test_gaming_edit_is_rejected_in_place_and_keeps_seed():
    # An edit that writes a pytest.ini narrowing collection (--ignore) is gaming, not repair: the host
    # gate rejects it exactly like a tool misuse (no rebuild), and keep-best floors at the seed.
    gaming = Action("edit", edit=EditOp("insert", 1, 1,
        "cat > pytest.ini <<'EOF'\n[pytest]\naddopts = --ignore=tests/integration\nEOF"))
    outcome, script, _ = _run([gaming], tests_need=("magic",))
    assert outcome == "GIVEUP" and script == "pip install app\n"    # never shipped the gaming edit

def test_gaming_patch_is_rejected_and_keeps_seed():
    gaming = Action("patch",
        new_script="pip install app\ncat > pytest.ini <<'EOF'\ntestpaths = tests/unit\nEOF\n")
    outcome, script, _ = _run([gaming], tests_need=("magic",))
    assert outcome == "GIVEUP" and script == "pip install app\n"

def test_gaming_edit_reprompts_with_narrowing_hint_then_legit_edit_reaches_done():
    # The gaming edit is bounced IN PLACE (no turn spent); the agent is re-prompted with a hint that
    # names the narrowing, and a subsequent real fix (editable install) reaches DONE.
    seen = []
    class _P:
        def __init__(self): self.n = 0
        def plan(self, history, script, observation, graph, fail_lineno=None, turn=None,
                 max_turns=None, rejection=None):
            seen.append(rejection); self.n += 1
            if self.n == 1:
                return "t", Action("edit", edit=EditOp("insert", 1, 1,
                    "cat > pytest.ini <<'EOF'\naddopts = --ignore=tests/slow\nEOF")), {}
            return "t", Action("edit", edit=EditOp("insert", 1, 1, "apt-get install -y libpq-dev")), {}
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    outcome, script, _ = run_react(
        object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
        run_tests=run_tests, planner=_P(), history=History(), log=ReactLog(silent=True),
        max_steps=2, _initial_script="pip install app\n")
    assert seen[0] is None                                          # first call: no rejection
    assert seen[1] is not None and "collect" in seen[1].lower()     # gaming hint names test collection
    assert outcome == "DONE" and "libpq-dev" in script             # the retried legit edit applied

def test_classify_action_rejects_narrowing_edit_directly():
    from src.react_repair.loop import _classify_action
    from src.react_repair.actions import Action, EditOp
    gaming = Action("edit", edit=EditOp("insert", 1, 1, "pytest --deselect tests/test_x.py::t"))
    kind, hint = _classify_action(gaming, "pip install app\n")
    assert kind == "invalid" and "collect" in hint.lower()
    legit = Action("edit", edit=EditOp("insert", 1, 1, "pip install redis"))
    assert _classify_action(legit, "pip install app\n")[0] == "edit"


def test_edit_summary_describes_the_op():
    from src.react_repair.loop import _edit_summary
    assert _edit_summary(EditOp("insert", 23, 23, "pip install hiredis")) == "insert@23 +pip install hiredis"
    assert _edit_summary(EditOp("replace", 40, 40, "pip install redis==8.0.1")) == "replace@40 pip install redis==8.0.1"
    assert _edit_summary(EditOp("delete", 55, 55, "")) == "delete@55"                 # deletes ARE captured
    assert _edit_summary(EditOp("insert", 5, 5, "a\nb\nc")) == "insert@5 +a (+2)"      # multi-line collapses
    assert _edit_summary(EditOp("replace", 2, 4, "x")) == "replace@2-4 x"             # range span

def test_edit_history_bracket_uses_op_summary():
    # the history records the edit BY ITS OP (verb@span + preview), not a whole-script diff.
    hist = History()
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    ins = Action("edit", edit=EditOp("insert", 1, 1, "apt-get install -y libpq-dev"))
    run_react(object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
              run_tests=run_tests, planner=_ScriptedPlanner([ins]), history=hist,
              log=ReactLog(silent=True), max_steps=10, _initial_script=box[0])
    assert hist.steps[1].action_summary.startswith("edit v1 (insert@1 ")
    assert "apt-get install -y libpq-dev" in hist.steps[1].action_summary

def test_unfixable_gives_up():
    outcome, _, _ = _run([], installed_needs=("libunobtainium",))
    assert outcome == "GIVEUP"

def test_no_gain_patches_run_to_giveup_not_early_stop():
    # Plateau REMOVED (it was a cost knob that false-stalled on real progress, e.g. Archipelago's
    # fix-chain, and cost lost rescues). SIX consecutive no-gain patches would have tripped the old
    # patience-5 PLATEAU; now the loop runs EVERY patch to max_steps and exits GIVEUP, never PLATEAU.
    builds = iter([RunResult(True)] * 7)                         # baseline + 6 patches all build green
    tests = iter([TestOutcome(False, 2, 5, "2")] * 7)           # never improves, never passes the gate
    moves = [Action("patch", new_script=f"pip install app\necho {i}\n") for i in range(6)]
    rlog = ReactLog(silent=True)
    outcome, _, _ = run_react(
        object(), reset=lambda: None, run_script=lambda s: next(builds),
        certify=lambda g: g, exec_readonly=lambda c: (0, ""), run_tests=lambda: next(tests),
        planner=_ScriptedPlanner(moves), history=History(), log=rlog,
        max_steps=12, _initial_script="pip install app\n")
    assert outcome == "GIVEUP"
    assert rlog.count("PLATEAU") == 0                           # no early stop
    assert rlog.count("PATCH") == 6                             # all six ran (old code stopped at 5)

def test_history_records_baseline_then_patch_outcome_with_score_in_bracket():
    # baseline can't install libpq-dev (BUILD FAILED); one patch adds it -> green 5/5 -> DONE.
    hist = History()
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    fix = Action("patch", new_script="pip install app\napt-get install -y libpq-dev\n")
    outcome, _, _ = run_react(
        object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
        run_tests=run_tests, planner=_ScriptedPlanner([fix]), history=hist,
        log=ReactLog(silent=True), max_steps=10, _initial_script=box[0])
    assert outcome == "DONE"
    # baseline is recorded first, with its verdict in the (never-truncated) bracket
    assert hist.steps[0].action_summary == "baseline → BUILD FAILED"
    # the patch records what it changed + its REAL outcome/score, all in the bracket (no placeholder)
    assert hist.steps[1].action_summary.startswith("patch v1 ")
    assert "+apt-get install -y libpq-dev" in hist.steps[1].action_summary   # the change is recorded
    assert "→ 5/5" in hist.steps[1].action_summary
    assert "(replaced build script)" not in hist.steps[1].observation_raw
    assert "BUILD OK" in hist.steps[1].observation_raw and "5/5" in hist.steps[1].observation_raw

def test_history_patch_that_regresses_records_build_failed_not_placeholder():
    hist = History()
    box = ["pip install app\n"]
    reset, run_script, certify, ro, run_tests = _adapters(("libpq-dev",), (), box)
    bad = Action("patch", new_script="pip install app\necho still-missing\n")   # never adds libpq-dev
    run_react(
        object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
        run_tests=run_tests, planner=_ScriptedPlanner([bad]), history=hist,
        log=ReactLog(silent=True), max_steps=3, _initial_script=box[0])
    assert hist.steps[1].action_summary.startswith("patch v1 ") and "→ BUILD FAILED" in hist.steps[1].action_summary
    assert "+echo still-missing" in hist.steps[1].action_summary
    assert "(replaced build script)" not in hist.steps[1].observation_raw

def test_regressing_patches_never_ship_over_seed():
    # keep-best/seed-floor: the seed builds green at 4/5 (below the 0.9 gate, so not DONE); two
    # patches each REGRESS (break the build). The loop runs to max_steps and must return the SEED —
    # the best-scoring script it ever saw — never the last (broken) patch.
    seed = "pip install app\n"
    builds = iter([RunResult(True),                       # baseline: seed builds
                   RunResult(False, "syntax", "err"),     # patch1: build fails
                   RunResult(False, "syntax", "err")])    # patch2: build fails
    tests = iter([TestOutcome(False, 4, 5, "4 passed, 1 failed")])   # only baseline runs tests
    outcome, script, _ = run_react(
        object(), reset=lambda: None, run_script=lambda s: next(builds),
        certify=lambda g: g, exec_readonly=lambda c: (0, ""), run_tests=lambda: next(tests),
        planner=_ScriptedPlanner([Action("patch", new_script="broken one\n"),
                                  Action("patch", new_script="broken two\n")]),
        history=History(), log=ReactLog(silent=True), max_steps=10, _initial_script=seed)
    assert outcome == "GIVEUP"
    assert script == seed                                 # NOT "broken two\n"

def test_giveup_returns_improved_script_over_a_later_regression():
    # keep-best keeps the highest-scoring attempt, not the seed and not a later regressor: baseline
    # 2/5 -> patch1 improves to 4/5 (new best, still below gate) -> patch2/patch3 break the build ->
    # GIVEUP at max_steps returns patch1's script.
    seed = "pip install app\n"
    builds = iter([RunResult(True), RunResult(True),
                   RunResult(False, "x", "e"), RunResult(False, "x", "e")])
    tests = iter([TestOutcome(False, 2, 5, "2"), TestOutcome(False, 4, 5, "4")])
    outcome, script, _ = run_react(
        object(), reset=lambda: None, run_script=lambda s: next(builds),
        certify=lambda g: g, exec_readonly=lambda c: (0, ""), run_tests=lambda: next(tests),
        planner=_ScriptedPlanner([Action("patch", new_script="pip install app\npip install extra\n"),
                                  Action("patch", new_script="broken\n"),
                                  Action("patch", new_script="also broken\n")]),
        history=History(), log=ReactLog(silent=True), max_steps=10, _initial_script=seed)
    assert outcome == "GIVEUP"
    assert "pip install extra" in script                  # patch1 (the best), not seed, not a regressor

def test_keep_best_prefers_more_collected_tests_when_passed_ties():
    # A fix that unblocks COLLECTION (executed 5 -> 8) but doesn't yet make tests PASS (passed stays
    # 0) is real progress and must be KEPT as best, not discarded because passed==0. Regression from
    # the M3 run: baserow's one good edit dropped collection errors 6->5 but a (built,passed)-only
    # key treated it as no-gain and reverted the final script to the seed.
    seed = "pip install app\n"
    builds = iter([RunResult(True), RunResult(True),                     # baseline + patch1 build green
                   RunResult(False, "x", "e"), RunResult(False, "x", "e")])  # patch2/3 regress
    tests = iter([TestOutcome(False, 0, 5, "5 collected, 0 passed"),     # baseline: 5 executed
                  TestOutcome(False, 0, 8, "8 collected, 0 passed")])    # patch1: MORE collected, still 0 pass
    outcome, script, _ = run_react(
        object(), reset=lambda: None, run_script=lambda s: next(builds),
        certify=lambda g: g, exec_readonly=lambda c: (0, ""), run_tests=lambda: next(tests),
        planner=_ScriptedPlanner([Action("patch", new_script="pip install app\npip install localpkg\n"),
                                  Action("patch", new_script="broken\n"),
                                  Action("patch", new_script="also broken\n")]),
        history=History(), log=ReactLog(silent=True), max_steps=10, _initial_script=seed)
    assert outcome == "GIVEUP"
    assert "pip install localpkg" in script               # patch1 kept (more collected), NOT the seed

def test_giveup_at_max_steps_returns_best_script_seed():
    # A build that never passes the gate + a planner that only EXPLORES (free turns, no patch) runs
    # to max_steps -> GIVEUP. The returned script must be the seed (best), never a corrupted one.
    outcome, script, _ = _run([Action("explore", command="ls") for _ in range(5)],
                              tests_need=("never",), initial="pip install app\n")
    assert outcome == "GIVEUP" and script == "pip install app\n"

def test_no_gain_patch_then_later_patch_reaches_done():
    # initial 2/5 (fail) -> patch1 no gain (loop keeps going, no early stop) -> patch2 gains to 5/5 -> DONE.
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
