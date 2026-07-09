import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.react_repair.planner as planner_mod
from src.react_repair.planner import ReactPlanner
from src.react_repair.history import History

_USAGE = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}


def _fake_tools(calls, content="reasoning"):
    """Patch complete_with_tools to return the given (name, args) calls + message content."""
    return lambda *a, **k: (calls, content, dict(_USAGE), "resp")


def _capture():
    """Patch complete_with_tools capturing the user prompt + call kwargs; returns (seen, fn)."""
    seen = {}
    def fn(client, model, messages, **k):
        seen["user"] = messages[-1]["content"]
        seen["kwargs"] = k
        return [("explore", '{"command":"ls"}')], "", dict(_USAGE), "resp"
    return seen, fn


# --- tool-calling primary path --------------------------------------------
def test_plan_returns_edit_from_tool_call(monkeypatch):
    monkeypatch.setattr(planner_mod, "complete_with_tools",
        _fake_tools([("edit", '{"verb":"insert","start":1,"content":"apt-get install -y libpq-dev"}')]))
    p = ReactPlanner(client=object(), model="m")
    thought, action, _ = p.plan(History(), "pip install psycopg2", "libpq.so.5 not found", graph=None)
    assert action.kind == "edit" and action.edit.verb == "insert" and "libpq-dev" in action.edit.content

def test_plan_returns_explore_from_tool_call(monkeypatch):
    monkeypatch.setattr(planner_mod, "complete_with_tools", _fake_tools([("explore", '{"command":"ls /app"}')]))
    _, action, _ = ReactPlanner(client=object(), model="m").plan(History(), "s", "o", graph=None)
    assert action.kind == "explore" and action.command == "ls /app"

def test_plan_forces_tool_choice_required_with_schema(monkeypatch):
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(History(), "s", "o", graph=None)
    assert seen["kwargs"].get("tool_choice") == "required"
    names = {t["function"]["name"] for t in seen["kwargs"]["tools"]}
    assert names == {"explore", "edit"}

def test_reasoning_captured_from_think_block(monkeypatch):
    monkeypatch.setattr(planner_mod, "complete_with_tools",
        _fake_tools([("explore", '{"command":"ls"}')], content="<think>need the file list</think>"))
    thought, _, _ = ReactPlanner(client=object(), model="m").plan(History(), "s", "o", graph=None)
    assert thought == "need the file list"


# --- text fallback when no tool call --------------------------------------
def test_plan_falls_back_to_text_parse_when_no_tool_call(monkeypatch):
    # a provider/turn that returns no tool call, only text — the planner falls back to parse_action.
    monkeypatch.setattr(planner_mod, "complete_with_tools",
        _fake_tools([], content="Thought: look\nAction: ls /app"))
    _, action, _ = ReactPlanner(client=object(), model="m").plan(History(), "s", "o", graph=None)
    assert action.kind == "explore" and action.command == "ls /app"


# --- prompt assembly -------------------------------------------------------
def test_baseline_prompt_has_no_graph_context(monkeypatch):
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(History(), "script", "obs", graph=None)
    assert "GRAPH CONTEXT" not in seen["user"]

def test_render_numbers_the_current_script(monkeypatch):
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(History(), "line-one\nline-two", "obs", graph=None)
    assert "1| line-one" in seen["user"] and "2| line-two" in seen["user"]

def test_graph_context_injected_when_provided(monkeypatch):
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    p = ReactPlanner(client=object(), model="m", graph_context=lambda g: "libpq: MISSING")
    p.plan(History(), "script", "obs", graph=object())
    assert "GRAPH CONTEXT" in seen["user"] and "libpq: MISSING" in seen["user"]


# --- trace -----------------------------------------------------------------
def test_plan_traces_the_edit_op(monkeypatch):
    from src.react_repair.log import ReactLog
    monkeypatch.setattr(planner_mod, "complete_with_tools",
        _fake_tools([("edit", '{"verb":"insert","start":54,"content":"redis-server --daemonize yes"}')]))
    log = ReactLog(silent=True)
    ReactPlanner(client=object(), model="m", log=log).plan(History(), "s", "obs", graph=None)
    plan_rec = [r for r in log.records if r["phase"] == "plan"][0]
    edit = plan_rec["action"]["edit"]
    assert edit["verb"] == "insert" and edit["start"] == 54 and "redis-server" in edit["content"]


# --- system prompt ---------------------------------------------------------
def test_system_prompt_has_all_sections():
    sp = planner_mod.SYSTEM_PROMPT
    for section in ("GOAL", "APPROACH", "INTEGRITY", "ENVIRONMENT (this run)", "TOOLS"):
        assert section in sp

def test_tools_section_names_explore_and_edit():
    sp = planner_mod.SYSTEM_PROMPT
    assert "explore" in sp and "edit" in sp and "Script:" not in sp

# --- turn budget (drive agency: finite turns to reach green) ---------------
def test_render_shows_turn_budget_counter(monkeypatch):
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(History(), "s", "o", graph=None, turn=12, max_turns=30)
    assert "Turn 12/30" in seen["user"] and "18 left" in seen["user"]

def test_render_escalates_when_turn_budget_low(monkeypatch):
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(History(), "s", "o", graph=None, turn=28, max_turns=30)
    assert "commit your best edit now" in seen["user"].lower()   # urgency when budget is low

def test_render_omits_turn_counter_without_turn(monkeypatch):
    # backward compatible: no turn info → the plain closing instruction, no counter.
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(History(), "s", "o", graph=None)
    assert "Turn " not in seen["user"] and "call one tool" in seen["user"]

def test_system_prompt_frames_the_turn_budget():
    assert "limited, counted number of turns" in planner_mod.SYSTEM_PROMPT

def test_system_prompt_directs_edit_until_pass_and_explore_is_ephemeral():
    # The agent over-explored (azure: 30 explores / 0 edits). The prompt must state that repair is an
    # EDIT loop to green, and that installs done inside explore don't persist (edit setup.sh instead).
    sp = planner_mod.SYSTEM_PROMPT
    assert "until the build is clean and the tests pass" in sp   # iterate-to-green imperative
    assert "gone next turn" in sp                                # explore mutations don't persist
    assert "failure, not diligence" in sp                        # over-exploration is discouraged

def test_system_prompt_orients_seed_is_prebuilt_closure_and_limits_explore():
    # The seed already encodes the graph-resolved package closure, so the agent should read the
    # error/ENVIRONMENT first and NOT explore to re-derive the package list (the M3 over-exploration).
    sp = planner_mod.SYSTEM_PROMPT
    assert "already installs the package closure" in sp        # seed = near-complete starting point
    assert "re-derive the package closure" in sp               # explore is not for rediscovering packages

def test_build_system_prompt_injects_env_and_placeholder():
    filled = planner_mod.build_system_prompt(
        "  Base image : python:3.10-slim (Debian 12)\n  Working dir: /app")
    assert "python:3.10-slim" in filled and "/app" in filled
    assert "environment details unavailable" in planner_mod.build_system_prompt("")

def test_planner_bakes_env_info_into_system_prompt():
    p = ReactPlanner(client=object(), model="m", env_info="  Base image : python:3.11-slim")
    assert "python:3.11-slim" in p.system_prompt


# --- failure-line anchoring in the numbered script ------------------------
def test_numbered_marks_the_failing_line():
    from src.react_repair.planner import _numbered, _HALT_MARKER
    lines = _numbered("aa\nbb\ncc", fail_lineno=2).splitlines()
    assert _HALT_MARKER in lines[1] and "bb" in lines[1]     # the failing line is tagged where it's edited
    assert _HALT_MARKER not in lines[0] and _HALT_MARKER not in lines[2]

def test_numbered_no_marker_without_fail_lineno():
    from src.react_repair.planner import _numbered, _HALT_MARKER
    assert _HALT_MARKER not in _numbered("aa\nbb", fail_lineno=None)

def test_numbered_ignores_out_of_range_fail_lineno():
    from src.react_repair.planner import _numbered, _HALT_MARKER
    out = _numbered("aa\nbb", fail_lineno=99)                 # stale/OOB line: don't crash, don't tag
    assert _HALT_MARKER not in out and "1| aa" in out

def test_plan_marks_failing_line_in_rendered_user_message(monkeypatch):
    from src.react_repair.planner import _HALT_MARKER
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(
        History(), "aa\nbb\ncc", "BUILD FAILED at `x` (line 2)", graph=None, fail_lineno=2)
    marked = [ln for ln in seen["user"].splitlines() if _HALT_MARKER in ln]
    assert len(marked) == 1 and "bb" in marked[0]


# --- set -e halt semantics in the prompt ----------------------------------
def test_system_prompt_explains_set_e_halt_semantics():
    sp = planner_mod.SYSTEM_PROMPT
    assert "halts at the first failing command" in sp        # fail-fast is stated
    assert "never ran" in sp                                 # lines below the halt are unexecuted
    assert "BUILD HALTED HERE" in sp                         # ties the prose to the inline script marker
