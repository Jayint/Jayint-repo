# src/envstate/planner.py
"""EnvState v1 — Planner role.

Reads the WorldModelMap once per cycle and emits a PlannerDecision:
  action="task"   → a fully-populated Task for the BuildAgent
  action="done"   → goal achieved (secondary stop; done_flag is the primary)
  action="giveup" → no viable path found

The Planner NEVER runs shell commands.

NAMING NOTE: src/planner.py (root-level) is the Arm-0 bare-ReAct planner.
This module is src/envstate/planner.py — entirely separate.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.jsonutil import extract_json_object
from src.envstate.llm_response import complete_with_retry
from src.envstate.world_model import (
    OpenProblem,
    PlannerDecision,
    Task,
    WorldModelMap,
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are the Planner for DockerAgent environment setup (v1).

Your only job is to read the current WorldModelMap and decide what to do next.
You NEVER run shell commands and NEVER write to the map directly.

## Fixed goal
Make `pytest --collect-only -q --disable-warnings` exit 0 from the repo root.
For Poetry projects use `poetry run pytest --collect-only -q --disable-warnings`.

## Stack layers (attack in order unless blocked)
  base → system → runtime → deps → build → tests

## Your output
Emit exactly one JSON object (inside a ```json fenced block) with these fields:

For a new task:
```json
{
  "action": "task",
  "goal": "<one concrete sub-goal, e.g. install project deps from pyproject>",
  "done_when": "<checkable criterion, e.g. poetry install exits 0 and python -c 'import edsl' works>",
  "layer": "<one of: base | system | runtime | deps | build | tests>",
  "facts": ["<relevant fact from the map the agent needs>"]
}
```

When the environment is ready (collect-only succeeded or done_flag is True):
```json
{"action": "done", "reason": "<brief explanation>"}
```

When no viable path remains (all options exhausted):
```json
{"action": "giveup", "reason": "<brief explanation>"}
```

## Sequencing rules
- Attack the lowest unmet layer first.
- An open_problem with out_of_scope=True must be skipped entirely — do not emit
  a task targeting it.  If an open_problem is runtime-only (e.g. swift, cuda)
  and does not block pytest collection, mark it out_of_scope by noting this in
  the reason field of a task targeting a different layer.
- If the last task was blocked on a layer, try a different approach or mark the
  problem out_of_scope before moving on.
- Emit "giveup" only when every layer has been tried and no viable path exists.

## Forbidden
- Do not run shell commands (never run, no shell, no execute).
- Do not emit more than one JSON object.
- Do not invent facts not present in the map.
"""

# ---------------------------------------------------------------------------
# Planning view renderer
# ---------------------------------------------------------------------------

_LAYER_ORDER = ("base", "system", "runtime", "deps", "build", "tests")


def render_planning_view(
    world_map: WorldModelMap,
    budget: dict[str, Any],
) -> str:
    """Compact projection of WorldModelMap for the Planner prompt."""
    lines: list[str] = []
    lines.append("# WorldModelMap")
    lines.append(f"base_image: {world_map.base_image}")
    lines.append(f"workdir: {world_map.workdir}")
    lines.append(f"language: {world_map.language}")
    lines.append(f"build_system: {world_map.build_system}")
    lines.append(f"done_flag: {world_map.done_flag}")

    lines.append("")
    lines.append("## repo_layout")
    for entry in world_map.repo_layout:
        lines.append(f"  {entry}")

    lines.append("")
    lines.append("## progress")
    for layer in _LAYER_ORDER:
        status = world_map.progress.get(layer, False)
        tick = "✓" if status else "✗"
        lines.append(f"  {layer}: {tick}")

    if world_map.required:
        lines.append("")
        lines.append("## required (declared, not yet verified)")
        for fact in world_map.required:
            lines.append(f"  - {fact.name}  {fact.detail}".rstrip())

    if world_map.installed:
        lines.append("")
        lines.append("## installed (confirmed)")
        for fact in world_map.installed:
            lines.append(f"  - {fact.name}  {fact.detail}".rstrip())

    if world_map.open_problems:
        lines.append("")
        lines.append("## open_problems")
        for op in world_map.open_problems:
            oos = " [out_of_scope]" if op.out_of_scope else ""
            lines.append(
                f"  - [{op.layer}]{oos} {op.signature}: {op.interpretation}"
            )

    if world_map.notes:
        lines.append("")
        lines.append("## notes")
        for note in world_map.notes:
            lines.append(f"  - {note}")

    lines.append("")
    lines.append(f"## budget\n  cycles_remaining: {budget.get('cycles_remaining')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON → PlannerDecision parser
# ---------------------------------------------------------------------------

_VALID_ACTIONS = frozenset({"task", "done", "giveup"})


def parse_planner_decision(text: Optional[str]) -> Optional[PlannerDecision]:
    """Extract and validate a PlannerDecision from raw LLM text.

    Returns None when:
    - text is empty / None
    - no JSON object found
    - action key is missing or has an unknown value
    - action="task" but goal / done_when / layer is missing
    """
    obj = extract_json_object(text)
    if obj is None:
        return None

    action = obj.get("action")
    if action not in _VALID_ACTIONS:
        return None

    reason = obj.get("reason", "")

    if action == "task":
        goal = obj.get("goal")
        done_when = obj.get("done_when")
        layer = obj.get("layer")
        if not goal or not done_when or not layer:
            return None
        raw_facts = obj.get("facts") or []
        facts: tuple[str, ...] = tuple(str(f) for f in raw_facts)
        task = Task(goal=goal, done_when=done_when, layer=layer, facts=facts)
        return PlannerDecision(action="task", task=task, reason=reason)

    # done or giveup
    return PlannerDecision(action=action, task=None, reason=reason)


# ---------------------------------------------------------------------------
# Planner class
# ---------------------------------------------------------------------------

class Planner:
    """Reads the WorldModelMap once per cycle and emits a PlannerDecision.

    One LLM call per cycle (via complete_with_retry).  Never runs shell
    commands.  Owns global sequencing and done/giveup termination.

    The orchestrator is responsible for hard-stopping on done_flag; the Planner
    does NOT special-case it internally (no override guard).  The map view
    surfaces done_flag=True to the LLM so a well-behaved model will return
    'done' naturally.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        on_usage: Callable[[dict], None] | None = None,
        log_path: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.on_usage = on_usage
        self.log_path = log_path
        self.last_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._cycle: int = 0

    def decide(
        self,
        current_map: WorldModelMap,
    ) -> PlannerDecision:
        """Single LLM call per cycle.

        Reads the map and emits a PlannerDecision with action in
        {'task', 'done', 'giveup'}.  Reuses complete_with_retry for
        retry-on-empty / retry-on-unparseable.

        Returns a giveup PlannerDecision if all retry attempts fail to
        yield a parseable response (safe fallback).

        NOTE: The done_flag override guard is intentionally absent here.
        The orchestrator hard-stops before calling decide() when done_flag
        is True.  The rendered view exposes done_flag so the LLM returns
        'done' naturally.  Duplicating the check here would hide orchestrator
        bugs and make this method harder to test independently.
        """
        self._cycle += 1
        budget = {"cycles_remaining": max(0, 12 - self._cycle)}
        view = render_planning_view(current_map, budget=budget)

        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": view},
        ]

        text, usage, response = complete_with_retry(
            self.client,
            self.model,
            messages,
            accept=lambda t: parse_planner_decision(t) is not None,
            retry_nudge=(
                "Your previous response did not contain a valid PlannerDecision. "
                "Emit exactly one JSON object with action in "
                "['task', 'done', 'giveup'] and all required fields."
            ),
            temperature=0,
        )

        self.last_usage = usage
        if self.on_usage:
            self.on_usage(usage)
        log_llm_exchange("planner", response, parsed=text[:200] if text else None,
                         log_path=self.log_path)

        decision = parse_planner_decision(text)
        if decision is None:
            # All retry attempts exhausted without a parseable response.
            return PlannerDecision(
                action="giveup",
                reason="planner returned empty or unparseable response after retries",
            )
        return decision
