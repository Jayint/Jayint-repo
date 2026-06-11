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

PLANNER_SYSTEM_PROMPT = """You are an expert in software environment setup. You are the Planner in an
automated system that prepares a repository's Docker environment so its tests
can run. You reason about state; you do not execute. A separate BuildAgent runs
the single task you emit, and a Maintainer folds the result back into a shared
Environment State Map that you read on the next cycle.

## The Environment State Map is your evidence
You are given the current state of the whole environment as an Environment State Map:
- Host-certified facts (ground truth): base image, interpreter/runtime (`env`),
  declared `required` packages, confirmed `installed` packages, system tools and
  libraries, and per-layer `progress`.
- `open_problems`: failures observed so far — each with a signature, an
  interpretation, and the stack layer at which it surfaced.
- `notes`: durable cautions carried from earlier cycles.
Do not invent facts that are not in the map.

## How to plan: mechanism-grounded reasoning, not trial-and-error
Treat the stack as a causal model — each layer rests on the ones beneath it:
  base -> system -> runtime -> deps -> build -> tests
A failure that surfaces at one layer usually has its root cause at or below it.
Read the map as a whole — the `required` vs `installed` gap, the system facts,
and the interpretation of each open problem — and diagnose the single deepest
unmet cause that is currently blocking progress. Do NOT propose speculative
installs to see if they help. Emit the one task that removes that root cause,
and cite, in `facts`, the specific map evidence that justifies it.

Your fixed objective: make `pytest --collect-only -q --disable-warnings` exit 0
from the repo root. For Poetry projects use
`poetry run pytest --collect-only -q --disable-warnings`.

## Output
Emit exactly one JSON object inside a ```json fenced block — nothing else:

```json
{
  "action": "task",
  "goal": "<the single sub-goal that removes the diagnosed root cause>",
  "done_when": "<a command-checkable success criterion>",
  "layer": "<base | system | runtime | deps | build | tests — the layer of the root cause>",
  "facts": ["<the map evidence that justifies this task>"]
}
```

When no viable path remains:

```json
{"action": "giveup", "reason": "<the open problems that remain and why no path resolves them>"}
```

You never run shell commands.
"""

# ---------------------------------------------------------------------------
# Planning view renderer
# ---------------------------------------------------------------------------

_LAYER_ORDER = ("base", "system", "runtime", "deps", "build", "tests")


def render_planning_view(
    world_map: WorldModelMap,
    budget: dict[str, Any],
) -> str:
    """Compact projection of the state map for the Planner prompt.

    The header label ("Environment State Map") is the only place this name is
    shown to the model and must match the vocabulary of PLANNER_SYSTEM_PROMPT.
    """
    lines: list[str] = []
    lines.append("# Environment State Map")
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

# The Planner may only emit `task` or `giveup`. There is no self-declared
# `done`: the structural done_flag (a real `pytest --collect-only` exit 0) is
# the sole success stop, which closes the unverified-exit leak.
_VALID_ACTIONS = frozenset({"task", "giveup"})


def parse_planner_decision(text: Optional[str]) -> Optional[PlannerDecision]:
    """Extract and validate a PlannerDecision from raw LLM text.

    Returns None when:
    - text is empty / None
    - no JSON object found
    - action key is missing or has an unknown value (a self-declared "done" is
      rejected here — it is not a valid action)
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

    # giveup
    return PlannerDecision(action=action, task=None, reason=reason)


# ---------------------------------------------------------------------------
# Planner class
# ---------------------------------------------------------------------------

class Planner:
    """Reads the Environment State Map once per cycle and emits a PlannerDecision.

    One LLM call per cycle (via complete_with_retry).  Never runs shell
    commands.  Emits only `task` or `giveup`.

    The orchestrator hard-stops on the structural done_flag; the Planner has no
    self-declared `done` action, so the only successful stop is a real
    `pytest --collect-only` exit 0 (closes the unverified-exit leak).
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
        {'task', 'giveup'}.  Reuses complete_with_retry for
        retry-on-empty / retry-on-unparseable.

        Returns a giveup PlannerDecision if all retry attempts fail to
        yield a parseable response (safe fallback).

        NOTE: There is no done_flag override guard here.  The orchestrator
        hard-stops before calling decide() when done_flag is True, and the
        Planner cannot self-declare done — so a successful stop is always
        backed by a real collect-only exit 0.
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
                "['task', 'giveup'] and all required fields."
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
