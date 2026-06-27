import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import inspect  # noqa: E402
import src.envstate.build_agent as ba  # noqa: E402
from src.envstate.world_model import Task  # noqa: E402
from src.envstate.ledger import ActionLedger  # noqa: E402


def _task(check="true"):
    return Task(goal="make check pass", done_when=check, layer="system", facts=())


def _agent():
    # client=None is fine: complete_with_retry is monkeypatched in every test that reaches the LLM.
    return ba.BuildAgent(client=None, model="test-model", synthesizer=None)


def test_check_passes_immediately_returns_done_without_llm(monkeypatch):
    calls = {"n": 0}

    def _fake(client, model, messages, **kw):
        calls["n"] += 1
        return ("Action: echo hi", {}, {})

    monkeypatch.setattr(ba, "complete_with_retry", _fake)
    report = _agent().run(_task("true"), lambda cmd: (True, ""), ActionLedger(), check="true")
    assert report.status == "done"
    assert calls["n"] == 0   # host check short-circuited before any LLM call


def test_llm_success_ignored_when_check_active(monkeypatch):
    # LLM claims success but the host check never passes → must NOT finalize (anti-hollow-success)
    def _fake(client, model, messages, **kw):
        return ("Final Answer: Success", {}, {})

    monkeypatch.setattr(ba, "complete_with_retry", _fake)
    report = _agent().run(_task("false"), lambda cmd: (False, ""), ActionLedger(), check="false")
    assert report.status == "blocked"


def test_check_none_preserves_llm_finalize(monkeypatch):
    # legacy path: with check=None the LLM's Final Answer still finalizes
    def _fake(client, model, messages, **kw):
        return ("Final Answer: Success", {}, {})

    monkeypatch.setattr(ba, "complete_with_retry", _fake)
    report = _agent().run(_task("x"), lambda cmd: (True, ""), ActionLedger(), check=None)
    assert report.status == "done"


def test_run_is_graph_blind(monkeypatch):
    # §7 experiments-not-facts: the agent path cannot write graph state — it takes no graph.
    params = inspect.signature(ba.BuildAgent.run).parameters
    assert not any("graph" in p.lower() for p in params)


def test_run_respects_explicit_budget(monkeypatch):
    """budget=2 caps the shell-action loop at 2 LLM steps before returning blocked."""
    calls = {"n": 0}

    def _fake(client, model, messages, *a, **k):
        calls["n"] += 1
        return f"Action: echo step-{calls['n']}", {"total_tokens": 1}, None

    monkeypatch.setattr(ba, "complete_with_retry", _fake)

    report = _agent().run(
        _task("false"), lambda cmd: (False, "boom"),
        ActionLedger(), check="false", budget=2,
    )
    assert report.status == "blocked"
    assert calls["n"] == 2          # exactly 2 LLM steps, not LOCAL_BUDGET (8)
