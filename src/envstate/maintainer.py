from __future__ import annotations
import json
from typing import Any, Optional

from src.envstate.acl import apply_llm_proposal
from src.envstate.jsonutil import extract_json_object
from src.envstate.ledger import ActionEvent
from src.envstate.serde import snapshot_to_dict
from src.envstate.types import EnvStateSnapshot

try:  # reuse the existing residual-span extractor
    from src.memory_manager import select_failure_lines
except Exception:  # pragma: no cover - fallback if signature drifts
    def select_failure_lines(observation, max_lines=48):
        lines = [ln for ln in (observation or "").splitlines() if ln.strip()]
        return "\n".join(lines[-max_lines:])


MAINTAINER_SYSTEM_PROMPT = """You are the State Maintainer for DockerAgent environment setup.

You interpret ONE command observation and propose structured updates to the
environment-state map. You are an interpreter, not an authority.

You MAY propose:
- candidate_requirements with status REQUIRED or UNKNOWN only
- open_failure_updates (a signature + a hypothesis)
- diagnose_requests (provider lookups, e.g. which apt package provides a tool)
- probe_requests (host probes the orchestrator should run to certify truth)
- plan_notes (durable cautions)

You MUST NOT emit:
- status=PRESENT or status=MISSING
- any Evidence object
- final Dockerfile lines
- authoritative task completion

Only host probes may certify PRESENT/MISSING with Evidence. If you believe a
capability is present or missing, emit a probe_request so the host can verify it.
When you emit a probe_request, include a `requirement_id` matching the candidate
requirement it should certify (e.g. "tool:pg_config"), so the host's certified
PRESENT/MISSING updates that same requirement.

Return exactly one JSON object inside a ```json fenced block.
"""


def build_maintainer_input(
    previous_env_state_view: dict[str, Any],
    task_spec: dict[str, Any],
    action_event: ActionEvent,
    full_log: str,
) -> dict[str, Any]:
    return {
        "previous_env_state_view": previous_env_state_view,
        "task_spec": task_spec,
        "action_event": {
            "cmd": action_event.cmd,
            "rc": action_event.rc,
            "env_revision_before": action_event.env_revision_before,
            "env_revision_after": action_event.env_revision_after,
        },
        "residual_spans": select_failure_lines(full_log),
    }


def parse_maintainer_proposal(content: Optional[str]) -> dict[str, Any]:
    return extract_json_object(content) or {}


class Maintainer:
    def __init__(self, client, model):
        self.client = client
        self.model = model

    def interpret(
        self,
        snapshot: EnvStateSnapshot,
        task_spec: dict[str, Any],
        action_event: ActionEvent,
        full_log: str,
    ):
        # The maintainer interprets each observation WITH the current map in view
        # (design §10 requires previous_env_state_view), so it can reconcile new
        # evidence against existing hypotheses instead of starting blind.
        payload = build_maintainer_input(
            snapshot_to_dict(snapshot), task_spec, action_event, full_log
        )
        messages = [
            {"role": "system", "content": MAINTAINER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ]
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0
        )
        content = response.choices[0].message.content or ""
        usage_obj = getattr(response, "usage", None)
        usage = {
            "input_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
        }
        proposal = parse_maintainer_proposal(content)
        updated, rejected = apply_llm_proposal(snapshot, proposal)
        return updated, proposal, rejected, usage
