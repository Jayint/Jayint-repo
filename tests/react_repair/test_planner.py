import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.react_repair.planner as planner_mod
from src.react_repair.planner import ReactPlanner
from src.react_repair.history import History


def _fake_llm(reply):
    return lambda *a, **k: (reply, {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw")


def test_plan_returns_edit(monkeypatch):
    monkeypatch.setattr(planner_mod, "complete_with_retry",
                        _fake_llm("Thought: add libpq\nEdit: insert after 1\n```bash\napt-get install -y libpq-dev\n```"))
    p = ReactPlanner(client=object(), model="m")
    thought, action, _ = p.plan(History(), "pip install psycopg2", "libpq.so.5 not found", graph=None)
    assert action.kind == "edit" and action.edit.verb == "insert" and "libpq-dev" in action.edit.content

def test_baseline_prompt_has_no_graph_context(monkeypatch):
    seen = {}
    def capture(client, model, messages, **k):
        seen["user"] = messages[-1]["content"]
        return "Action: ls", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw"
    monkeypatch.setattr(planner_mod, "complete_with_retry", capture)
    ReactPlanner(client=object(), model="m").plan(History(), "script", "obs", graph=None)
    assert "GRAPH CONTEXT" not in seen["user"]

def test_render_numbers_the_current_script(monkeypatch):
    # The current setup.sh is shown with a 1-based line-number gutter so Edit refs (and the build
    # failure's "line N") point at the same line.
    seen = {}
    def capture(client, model, messages, **k):
        seen["user"] = messages[-1]["content"]
        return "Action: ls", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw"
    monkeypatch.setattr(planner_mod, "complete_with_retry", capture)
    ReactPlanner(client=object(), model="m").plan(History(), "line-one\nline-two", "obs", graph=None)
    assert "1| line-one" in seen["user"] and "2| line-two" in seen["user"]

def test_system_prompt_has_all_sections():
    sp = planner_mod.SYSTEM_PROMPT
    for section in ("GOAL", "APPROACH", "INTEGRITY", "ENVIRONMENT (this run)", "TOOLS"):
        assert section in sp

def test_tools_are_action_and_edit_only():
    # edit-only: Action + Edit are offered; the whole-file Script rewrite is NOT advertised.
    sp = planner_mod.SYSTEM_PROMPT
    assert "Action" in sp and "Edit:" in sp and "insert after" in sp
    assert "Script:" not in sp

def test_build_system_prompt_injects_env_and_placeholder():
    filled = planner_mod.build_system_prompt(
        "  Base image : python:3.10-slim (Debian 12)\n  Working dir: /app")
    assert "python:3.10-slim" in filled and "/app" in filled
    assert "environment details unavailable" in planner_mod.build_system_prompt("")

def test_planner_bakes_env_info_into_system_prompt():
    p = ReactPlanner(client=object(), model="m", env_info="  Base image : python:3.11-slim")
    assert "python:3.11-slim" in p.system_prompt

def test_graph_context_injected_when_provided(monkeypatch):
    seen = {}
    def capture(client, model, messages, **k):
        seen["user"] = messages[-1]["content"]
        return "Action: ls", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw"
    monkeypatch.setattr(planner_mod, "complete_with_retry", capture)
    p = ReactPlanner(client=object(), model="m", graph_context=lambda g: "libpq: MISSING")
    p.plan(History(), "script", "obs", graph=object())
    assert "GRAPH CONTEXT" in seen["user"] and "libpq: MISSING" in seen["user"]
