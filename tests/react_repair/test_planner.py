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


def test_plan_returns_patch(monkeypatch):
    monkeypatch.setattr(planner_mod, "complete_with_retry",
                        _fake_llm("Thought: add libpq\nScript:\n```bash\napt-get install -y libpq-dev\n```"))
    p = ReactPlanner(client=object(), model="m")
    thought, action, _ = p.plan(History(), "pip install psycopg2", "libpq.so.5 not found", graph=None)
    assert action.kind == "patch" and "libpq-dev" in action.new_script

def test_baseline_prompt_has_no_graph_context(monkeypatch):
    seen = {}
    def capture(client, model, messages, **k):
        seen["user"] = messages[-1]["content"]
        return "Action: ls", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw"
    monkeypatch.setattr(planner_mod, "complete_with_retry", capture)
    ReactPlanner(client=object(), model="m").plan(History(), "script", "obs", graph=None)
    assert "GRAPH CONTEXT" not in seen["user"]

def test_system_prompt_has_preserve_and_investigate_directives():
    # The two GENERAL behavioral fixes from the repair-regression forensics: don't strip the seed's
    # closure ("preserve and extend"), and investigate the repo instead of guessing from one error.
    # Kept as principles — deliberately NOT a repo-specific manifest/monorepo checklist (avoid overfit).
    sp = planner_mod.SYSTEM_PROMPT.lower()
    assert "do not rewrite from scratch" in sp and "keep its working install lines" in sp
    assert "investigate the repo itself" in sp

def test_graph_context_injected_when_provided(monkeypatch):
    seen = {}
    def capture(client, model, messages, **k):
        seen["user"] = messages[-1]["content"]
        return "Action: ls", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw"
    monkeypatch.setattr(planner_mod, "complete_with_retry", capture)
    p = ReactPlanner(client=object(), model="m", graph_context=lambda g: "libpq: MISSING")
    p.plan(History(), "script", "obs", graph=object())
    assert "GRAPH CONTEXT" in seen["user"] and "libpq: MISSING" in seen["user"]
