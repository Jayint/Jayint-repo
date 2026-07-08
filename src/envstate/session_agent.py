"""SessionAgent — the arm-C production agent (spec 2026-07-08 §5.3).

Same contract as the eval's ScriptedSolver (``next_action`` → probe|patch), but backed by a
real LLM. The critical difference from ``V3BuildAgent.propose``: the conversation is rebuilt
from the SESSION each turn (``session.render_for_agent()`` — the compounding memory), so the
agent sees every prior patch it tried and what the clean replay did, instead of a cold
per-patch reset. Output is still one typed PatchProposal or one read-only probe — the agent
never mutates the container; the host applies the gated patch and certifies."""
from __future__ import annotations

from typing import Any, Callable

from python_deps.depgraph.patch import parse_patch_proposal, PatchParseError, PatchProposal
from python_deps.depgraph.patch_gate import is_read_only
from src.envstate.diagnostics import log_llm_exchange
from src.envstate.jsonutil import extract_json_object
from src.envstate.llm_response import complete_with_retry
from src.envstate.repair_scope import PATCH_SCHEMA_HINT
from src.envstate.v3_build_agent import _extract_worker_action, _truncate_output

SESSION_SYSTEM_PROMPT = """\
You are debugging ONE failing environment obligation for a Python repo, across a SUSTAINED
session. You are shown every patch you already tried and exactly what the clean replay did
next — reason over that history; do not repeat a patch that did not help.
You may run READ-ONLY diagnostics (pkg-config, apt-cache, ldconfig, pip show, ls, cat).
You may NOT install/modify/delete — the host applies your fix, not you.
Each turn respond with ONE of:
  Action: <one read-only shell command>          (you will get an Observation next turn)
  Final Patch: followed by exactly one fenced ```json PatchProposal object
The typed patch is the ONLY accepted change — never claim success in prose."""


class SessionAgent:
    def __init__(self, client: Any, model: str,
                 on_usage: Callable[[dict], None] | None = None,
                 known_evidence_ids: frozenset[str] = frozenset()):
        self.client = client
        self.model = model
        self.on_usage = on_usage
        self.known_evidence_ids = known_evidence_ids

    def _render(self, session, failure) -> str:
        parts = [
            f"Failing obligation: {failure.failing_node}",
            f"Failed command: {failure.failing_command}",
            f"Failure output:\n{_truncate_output(failure.output or '')}",
            "Session so far (your prior patches and what the clean replay did):",
            session.render_for_agent(),
        ]
        if self.known_evidence_ids:
            parts.append("Cite evidence by id (available: "
                         + ", ".join(sorted(self.known_evidence_ids)) + ").")
        parts.append(PATCH_SCHEMA_HINT)
        return "\n\n".join(parts)

    def next_action(self, session, failure, log):
        messages = [
            {"role": "system", "content": SESSION_SYSTEM_PROMPT},
            {"role": "user", "content": self._render(session, failure)},
        ]
        text, usage, raw = complete_with_retry(
            self.client, self.model, messages, temperature=0, stop=["Observation:"])
        if self.on_usage:
            self.on_usage(usage)
        log_llm_exchange("session_agent", raw, parsed={}, messages=messages)

        obj = extract_json_object(text)
        if obj is not None:
            try:
                proposal = parse_patch_proposal(obj)
                if not proposal.is_empty():
                    return ("patch", proposal, failure.failing_cap)
            except PatchParseError:
                pass
        action = _extract_worker_action(text)
        if action.strip() and is_read_only(action):
            return ("probe", action, failure.failing_cap)
        # Neither a valid patch nor a read-only action: an empty patch the gate accepts as a
        # no-op → made_progress False → the session's stall rule ends it honestly.
        return ("patch", PatchProposal(), failure.failing_cap)
