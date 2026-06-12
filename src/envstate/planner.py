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

Your fixed objective: execute the full test suite with a bare interpreter
(`python -m pytest -q` from the repo root) and get the MAJORITY of tests
passing. The only acceptable remaining failures are NON-ENVIRONMENT failures:
pre-existing source bugs, test-logic errors, or external/network flakiness.
Any `ImportError`, `ModuleNotFoundError`, collection error, setup error, or
missing-service failure means the environment is NOT done — keep fixing it.
Do NOT use venv wrappers such as `poetry run` — the grader uses the bare
system interpreter.

## Phase 2 — Venv / interpreter remediation

The grader always invokes `python -m pytest` using the bare system interpreter,
never through a venv wrapper such as `poetry run`, `pipenv run`, `hatch run`,
`pdm run`, or a sourced virtualenv activate script.  A `poetry run pytest …
8 passed` result does NOT count — it is not sufficient to satisfy the gate.

For Poetry / Pipenv / Hatch / PDM projects the dependencies must be importable
by the plain system interpreter:
- Emit a precursor task that disables virtualenv creation BEFORE installing:
  `poetry config virtualenvs.create false` (or set the env var
  `POETRY_VIRTUALENVS_CREATE=false`), then run `poetry install`.
- For Pipenv: `PIPENV_VENV_IN_PROJECT=true pipenv install --system` or
  `pip install -r <(pipenv lock -r)`.
- Verify with the bare system interpreter only: `python -m pytest -q`.
  A wrapper-based pass does not satisfy the gate; do not use it as verification.

## Phase 3 — Runtime-service heuristic and compiled-binary build step

### Runtime services
When a known service client library (redis, psycopg2, pymongo, mysqlclient,
celery, kombu, pika, elasticsearch, etc.) appears in `required` or `installed`,
hypothesize that the test suite needs a LIVE running server — not just the
client library installed.  A successful `import redis` or `pip show redis` does
NOT prove the runtime layer is satisfied; the actual server daemon must be
running and reachable on the host/port expected by the tests.

Emit a runtime-layer task to start or provision the service in the container
before running tests.  Example: start `redis-server` in the background before
`python -m pytest`.  Do NOT treat `import X` succeeding as proof the runtime
layer is satisfied.

### Compiled binaries (C / C++ / Go / Rust / Makefile repos)
When `repo_layout` contains a `Makefile`, `CMakeLists.txt`, `configure` script,
or source files with extensions `.c`, `.cpp`, `.go`, `.rs`, the testable
artifact is a compiled binary, not just installed Python packages.  Propose the
build step (`make`, `cmake --build`, `go build`, `cargo build`, etc.) BEFORE
expecting tests to pass.

## Phase 4 — done_when discipline and anti-fabrication rules

### done_when must be the real acceptance command
`done_when` MUST be a bare `python -m pytest -q` execution that actually runs
the suite with the majority of tests passing (non-environment failures
tolerated) — not a weaker proxy such as:
- `pip show X` / `pip list` / `pip install … exit 0`
- `python -c "import X"` alone
- any venv-wrapper invocation

### Never fabricate tests or game the gate
- NEVER create or write new test files (e.g. `test_zero.py`, `test_smoke.py`)
  to manufacture a passing run.
- NEVER use `--ignore`, `--ignore-glob`, or `--deselect` to hide a pre-existing
  failing or broken test in order to reach exit 0.
- If, after inventorying the repo, there are NO genuine pre-existing test files
  (no `test_*.py` / `*_test.py` files), OR every test-named file fails to
  import as valid Python, emit `giveup` with a reason that states there is no
  real test suite (`no_real_test_suite`).  Do NOT fabricate tests or game the
  gate.

## Output
Emit exactly one JSON object inside a ```json fenced block — nothing else:

```json
{
  "action": "task",
  "goal": "<the single sub-goal that removes the diagnosed root cause>",
  "done_when": "<a bare python -m pytest -q execution: suite runs, majority of tests pass (non-env failures ok), not a proxy>",
  "layer": "<base | system | runtime | deps | build | tests — the layer of the root cause>",
  "facts": ["<the map evidence that justifies this task>"]
}
```

When no viable path remains (including no real test suite):

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
# `done`: the structural done_flag (a real `python -m pytest -q` with >=1 passed)
# is the sole success stop, which closes the unverified-exit leak.
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
    self-declared `done` action, so the only successful stop is a real test
    execution pass (>=1 passed, bare `python -m pytest -q`, no venv wrapper)
    that closes the unverified-exit leak.
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
        backed by a real test execution pass (>=1 passed, bare interpreter).
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
