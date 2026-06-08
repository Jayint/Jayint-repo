from __future__ import annotations
from typing import Any, Optional

from src.envstate.jsonutil import extract_json_object
from src.envstate.ledger import ActionLedger
from src.envstate.llm_response import response_text
from src.envstate.types import EnvStateSnapshot, Source

SETUP_PHASES = (
    "Repository Analysis",
    "Language Dependency Installation",
    "Native/System Dependency Resolution",
    "Environment Configuration",
    "Verification",
    "Synthesis Readiness",
)

SUPERVISOR_SYSTEM_PROMPT = """You are the Supervisor Planner for DockerAgent environment setup.

Your job is to configure the repository environment by assigning bounded tasks to
a ReAct build worker. You do not execute shell commands. You do not update
EnvState. You do not certify that dependencies are present.

The source of truth is the provided EnvState snapshot. Facts with source=PROBE and
the current env_revision are trusted. Facts from LLM_GUESS, MEMORY, STATIC_SCAN, or
stale revisions are hypotheses only.

Choose the next task based on the current setup phase, EnvState, open failures,
worker history, and budget.

Setup phases (in order): Repository Analysis; Language Dependency Installation;
Native/System Dependency Resolution; Environment Configuration; Verification;
Synthesis Readiness.

Forbidden: do not claim a requirement is PRESENT or MISSING; do not edit EnvState;
do not emit shell commands as the top-level output; do not ask the worker to solve
the entire environment at once; do not treat a checklist or worker report as proof.

Emit exactly one TaskSpec JSON object inside a ```json fenced block, with keys:
task_id, phase, goal, relevant_state, constraints, allowed_actions,
success_criteria, stop_conditions, suggested_tactics.
"""


def render_planning_view(
    snapshot: EnvStateSnapshot, ledger: ActionLedger, budget: dict[str, Any]
) -> str:
    """Compact projection of EnvState for the Supervisor (design §3 RenderedPlanningView)."""
    lines = [f"# EnvState (revision {snapshot.revision}, container {snapshot.container_id})"]
    lines.append(f"Base: image={snapshot.base.image} python={snapshot.base.python} "
                 f"distro={snapshot.base.distro} arch={snapshot.base.arch}")
    lines.append("")
    lines.append("## Requirements")
    for req in snapshot.requirements:
        trust = "PROBE" if req.source == Source.PROBE else f"hypothesis({req.source})"
        lines.append(f"- [{req.status}] {req.id} ({req.kind}) via {trust}"
                     + (f" requires {list(req.required_by)}" if req.required_by else ""))
    if snapshot.open_failures:
        lines.append("")
        lines.append("## Open Failures")
        for fail in snapshot.open_failures:
            lines.append(f"- {fail.signature} (rev {fail.first_seen_revision}->{fail.last_seen_revision})"
                         + (f": {fail.hypothesis}" if fail.hypothesis else ""))
    if snapshot.plan_notes:
        lines.append("")
        lines.append("## Plan Notes")
        for note in snapshot.plan_notes:
            lines.append(f"- {note}")
    recent = ledger.events()[-5:]
    if recent:
        lines.append("")
        lines.append("## Recent Actions")
        for event in recent:
            lines.append(f"- step {event.step}: `{event.cmd}` rc={event.rc} -> {event.summary[:80]}")
    lines.append("")
    lines.append(f"## Budget\n- steps_remaining: {budget.get('steps_remaining')}")
    return "\n".join(lines)


def parse_task_spec(content: Optional[str]) -> Optional[dict[str, Any]]:
    parsed = extract_json_object(content)
    return parsed if parsed and parsed.get("task_id") else None


class Supervisor:
    def __init__(self, client, model):
        self.client = client
        self.model = model

    def next_task(self, snapshot, ledger, budget):
        view = render_planning_view(snapshot, ledger, budget)
        messages = [
            {"role": "system", "content": SUPERVISOR_SYSTEM_PROMPT},
            {"role": "user", "content": view},
        ]
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0
        )
        content = response_text(response)
        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        return parse_task_spec(content), usage
