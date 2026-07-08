"""SessionAgent: parses a fenced patch, turns a read-only Action into a probe, and shows
the agent its full session history (compounding memory)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate.repair_session import RepairSession, Step  # noqa: E402
from src.envstate.repair_types import ReplayResult  # noqa: E402
from src.envstate.session_agent import SessionAgent  # noqa: E402


class _L:
    def d(self, *a, **k):
        pass


def test_patch_parsed_and_history_in_prompt(monkeypatch):
    import src.envstate.session_agent as sa
    captured = {}

    def fake_complete(client, model, messages, **k):
        captured["messages"] = messages
        return ('```json\n{"patch":{"add_requirements":[{"id":"syslib:ffi","type":"SystemLib",'
                '"name":"ffi","layer":"system","check_command":"ldconfig -p | grep -q libffi",'
                '"evidence_ref":"ev.1"}]}}\n```', {}, "raw")

    monkeypatch.setattr(sa, "complete_with_retry", fake_complete)
    monkeypatch.setattr(sa, "log_llm_exchange", lambda *a, **k: None)

    s = RepairSession("pkg:cryptography", "ffi")
    s.steps.append(Step("patch", "add:['syslib:x']", cap="x",
                        replay=ReplayResult(False, "pkg:cryptography", "ffi", "c", "")))
    agent = SessionAgent(client=object(), model="m")
    kind, patch, cap = agent.next_action(
        s, ReplayResult(False, "pkg:cryptography", "ffi", "c", ""), log=_L())
    assert kind == "patch" and patch.add_requirements[0].id == "syslib:ffi"
    # the prior patch (memory) is in the prompt the agent was given
    assert "syslib:x" in "".join(m["content"] for m in captured["messages"])


def test_read_only_action_becomes_probe(monkeypatch):
    import src.envstate.session_agent as sa
    monkeypatch.setattr(sa, "complete_with_retry",
                        lambda *a, **k: ("Action: ldconfig -p | grep libpq", {}, "raw"))
    monkeypatch.setattr(sa, "log_llm_exchange", lambda *a, **k: None)
    agent = SessionAgent(client=object(), model="m")
    kind, cmd, cap = agent.next_action(
        RepairSession("pkg:p", "libpq"), ReplayResult(False, "pkg:p", "libpq", "c", ""), log=_L())
    assert kind == "probe" and "ldconfig" in cmd
