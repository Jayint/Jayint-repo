import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.agent.prompt as planner_mod
from src.agent.prompt import ReactPlanner
from src.agent.history import History

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


def _capture_all():
    """Patch complete_with_tools capturing the FULL message list (for the prompt-style lever)."""
    seen = {}
    def fn(client, model, messages, **k):
        seen["messages"] = messages
        return [("explore", '{"command":"ls"}')], "", dict(_USAGE), "resp"
    return seen, fn


def _one_step_history():
    from src.agent.history import History
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `pip install x` (line 1):\nboom\n")
    return h


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
    got = {}

    def _ctx(graph, result, causes, prev_states):
        got.update(result=result, causes=causes, prev_states=prev_states)
        return "libpq: MISSING"

    p = ReactPlanner(client=object(), model="m", graph_context=_ctx)
    p.plan(History(), "script", "obs", graph=object(),
          result=None, causes=[], prev_states={})
    assert "GRAPH CONTEXT" in seen["user"] and "libpq: MISSING" in seen["user"]
    assert got == {"result": None, "causes": [], "prev_states": {}}


def test_graph_context_is_absent_for_the_baseline(monkeypatch):
    """G0/G1 ablation invariant: graph_context=None -> no GRAPH CONTEXT block at all."""
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m", graph_context=None).plan(
        History(), "script", "obs", graph=object())
    assert "GRAPH CONTEXT" not in seen["user"]


# --- trace -----------------------------------------------------------------
def test_plan_traces_the_edit_op(monkeypatch):
    from src.agent.log import ReactLog
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
    for section in ("ROLE", "GOAL", "READING A FAILURE", "RULES", "ENVIRONMENT (this run)", "TOOLS"):
        assert section in sp

def test_tools_section_names_explore_and_edit():
    sp = planner_mod.SYSTEM_PROMPT
    assert "explore" in sp and "edit" in sp and "Script:" not in sp

def test_integrity_forbids_narrowing_the_collected_test_set():
    # The podman-compose gaming (agent wrote a pytest.ini --ignore to drop erroring tests, after its
    # own CoT flagged "faking a pass" then relabeled it "config") must be named explicitly, and the
    # prompt must state the host ENFORCES it — the ask alone didn't hold.
    sp = planner_mod.SYSTEM_PROMPT
    assert "collect" in sp.lower()                        # names WHAT must not shrink (pytest collection)
    assert "--ignore" in sp and "testpaths" in sp         # names the concrete collection-narrowing mechanisms
    assert "reject" in sp.lower()                         # host rejects an edit that shrinks the set

def test_render_injects_rejection_hint(monkeypatch):
    # On a same-turn retry after a tool misuse, the rejection reason is injected into the prompt.
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(
        History(), "s", "o", graph=None, rejection="explore is READ-ONLY, use edit() instead")
    assert "REJECTED" in seen["user"] and "use edit()" in seen["user"]

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
    assert "Turns are counted" in planner_mod.SYSTEM_PROMPT

def test_system_prompt_directs_edit_until_pass_and_explore_is_ephemeral():
    # The agent over-explored (azure: 30 explores / 0 edits). The prompt must state the goal is a
    # passing suite, that installs done inside explore don't persist (edit setup.sh instead), and
    # that over-exploration wastes the budget.
    sp = planner_mod.SYSTEM_PROMPT
    flat = " ".join(sp.split())                                 # whitespace-normalized: line-wraps don't split phrases
    assert "pytest pass rate" in sp                             # goal: iterate to a passing suite
    assert "nothing you install here persists" in sp           # explore mutations don't persist
    assert "don't explore the budget away" in flat             # over-exploration is discouraged

def test_system_prompt_orients_seed_is_prebuilt_closure_and_limits_explore():
    # The seed already encodes the graph-resolved package closure, so the agent should treat it as a
    # near-complete starting point rather than re-deriving the package list (the M3 over-exploration).
    sp = planner_mod.SYSTEM_PROMPT
    flat = " ".join(sp.split())                                          # whitespace-normalized: line-wraps don't split phrases
    assert "already installs the repo's declared package closure" in flat   # seed = resolved closure
    assert "near-complete" in sp                                          # a starting point, not a blank slate

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
    from src.agent.prompt import _numbered, _HALT_MARKER
    lines = _numbered("aa\nbb\ncc", fail_lineno=2).splitlines()
    assert _HALT_MARKER in lines[1] and "bb" in lines[1]     # the failing line is tagged where it's edited
    assert _HALT_MARKER not in lines[0] and _HALT_MARKER not in lines[2]

def test_numbered_no_marker_without_fail_lineno():
    from src.agent.prompt import _numbered, _HALT_MARKER
    assert _HALT_MARKER not in _numbered("aa\nbb", fail_lineno=None)

def test_numbered_ignores_out_of_range_fail_lineno():
    from src.agent.prompt import _numbered, _HALT_MARKER
    out = _numbered("aa\nbb", fail_lineno=99)                 # stale/OOB line: don't crash, don't tag
    assert _HALT_MARKER not in out and "1| aa" in out

def test_plan_marks_failing_line_in_rendered_user_message(monkeypatch):
    from src.agent.prompt import _HALT_MARKER
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(
        History(), "aa\nbb\ncc", "BUILD FAILED at `x` (line 2)", graph=None, fail_lineno=2)
    marked = [ln for ln in seen["user"].splitlines() if _HALT_MARKER in ln]
    assert len(marked) == 1 and "bb" in marked[0]


# --- prompt-style lever (growing message list = default; blob = opt-in) ---
def test_prompt_style_defaults_to_growing_message_list(monkeypatch):
    # DEFAULT (env unset): the model's own moves become real assistant turns; the live obs is last.
    from src.agent.history import History
    monkeypatch.delenv("REACT_PROMPT_STYLE", raising=False)
    h = History()                                              # needs an ACTION for an assistant turn
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `x` (line 1):\nboom\n")
    h.record(1, "add it", "edit v1 (insert@1 +pip install foo) → BUILD FAILED",
             "BUILD FAILED at `x` (line 1):\nstill boom\n",
             action={"kind": "edit", "verb": "insert", "start": 1, "end": 1, "content": "pip install foo"})
    seen, fn = _capture_all(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(h, "line-one", "o", graph=None)
    roles = [m["role"] for m in seen["messages"]]
    assert roles[0] == "system" and "assistant" in roles       # growing list, not a 2-message blob
    assert "1| line-one" in seen["messages"][-1]["content"]     # numbered script on the live user turn
    assert "LAST RUN OBSERVATION" not in "\n".join(m["content"] for m in seen["messages"])

def test_prompt_style_blob_opt_in_is_two_messages(monkeypatch):
    # REACT_PROMPT_STYLE=blob → the legacy single re-rendered user blob (no assistant turns).
    monkeypatch.setenv("REACT_PROMPT_STYLE", "blob")
    seen, fn = _capture_all(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m").plan(_one_step_history(), "s", "o", graph=None)
    assert [m["role"] for m in seen["messages"]] == ["system", "user"]

def test_prompt_style_default_still_forces_tool_choice(monkeypatch):
    monkeypatch.delenv("REACT_PROMPT_STYLE", raising=False)
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)   # _capture grabs kwargs
    ReactPlanner(client=object(), model="m").plan(_one_step_history(), "s", "o", graph=None)
    assert seen["kwargs"].get("tool_choice") == "required"


# --- set -e halt semantics in the prompt ----------------------------------
def test_system_prompt_explains_set_e_halt_semantics():
    sp = planner_mod.SYSTEM_PROMPT
    assert "stops at the first failing command" in sp        # fail-fast is stated
    assert "never ran" in sp                                 # lines below the halt are unexecuted
    assert "BUILD HALTED HERE" in " ".join(sp.split())       # ties the prose to the inline script marker (wrap-robust)


def test_integrity_forbids_installing_the_project_from_an_index():
    # Live false green: the agent installed the repo's OWN package from PyPI (297/297 green) instead
    # of the checkout, so the suite tested code that is not in the repo. The host enforces it now.
    sp = planner_mod.SYSTEM_PROMPT
    assert "pip install -e ." in sp                    # names the correct install
    assert "package index" in sp                       # names what's forbidden
    assert "fakes a pass" in sp.lower()                # says WHY (a published copy fakes a pass against absent code)
