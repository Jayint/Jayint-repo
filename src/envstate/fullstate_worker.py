"""fullstate_worker.py — Arm A / Arm C shared machinery.

Provides:
  FULLSTATE_WORKER_SYSTEM_PROMPT  — layered RCA prompt (§3.4)
  render_fullstate_view(snapshot, recent_observations) -> str  (§3.3)
  class FullStateWorkerPlanner  — mirrors LlmWorkerPlanner (§3.9)
  run_fullstate_loop(...)  — single-task-free ReAct loop (§3.2, §3.5)

NOTE: interruption_decision is defined in worker.py (NOT here) to avoid a
circular import — fullstate_worker imports _extract_worker_action /
_is_worker_finished / WorkerReport / MAX_EMPTY_PLANNER_RESPONSES from worker.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.llm_response import response_text
from src.envstate.types import EnvStateSnapshot, Source
from src.envstate.worker import (
    MAX_EMPTY_PLANNER_RESPONSES,
    WorkerReport,
    _extract_worker_action,
    _is_worker_finished,
)

_REPO_LAYOUT_CAP = 120

# ---------------------------------------------------------------------------
# Synthetic single-task spec used by Arm A (§3.2)
# ---------------------------------------------------------------------------

FULLSTATE_TASK_SPEC = {
    "task_id": "fullstate",
    "phase": "Whole-Environment Root-Cause Configuration",
    "goal": (
        "Configure the repository environment end-to-end: install all dependencies "
        "and make the test suite runnable, diagnosing the root cause of each failure "
        "across the full stack."
    ),
    "relevant_state": [],
    "constraints": [],
    "allowed_actions": [],
    "success_criteria": ["The project's test command runs and the suite is runnable."],
    "stop_conditions": [],
}

# ---------------------------------------------------------------------------
# System prompt (§3.4)
# ---------------------------------------------------------------------------

FULLSTATE_WORKER_SYSTEM_PROMPT = """You are the ReAct FullState Worker for DockerAgent environment setup.

Your job is to configure the entire repository environment end-to-end by issuing
shell commands inside the container.  You have full visibility of the certified
world model (host-probed EnvState snapshot) and must diagnose root causes across
the full stack before acting.

## Layered Root-Cause Analysis (RCA) approach

Before each action, identify which layer needs attention and justify your next
command from the certified state:

  1. base image       — OS / distribution / architecture constraints
  2. system packages  — apt/yum/apk native libraries and headers
  3. runtime          — Python version, interpreter, pip/virtualenv toolchain
  4. deps             — project Python/language packages (requirements, pyproject, setup.py)
  5. build            — compilation, linking, editable installs, extension modules
  6. tests            — test runner availability, test-command correctness, fixture data;
                        the environment is complete when
                        "pytest --collect-only -q --disable-warnings" succeeds from
                        the repo root (for Poetry: "poetry run pytest --collect-only
                        -q --disable-warnings"). Run it to verify BEFORE responding
                        with Final Answer: Success.

Work from the bottom of the stack upward.  Do not paper over a symptom one layer
above its cause (e.g. do not patch a Python import error when the root cause is a
missing system library at the system layer).

## Trust rules

- A requirement is TRUE only when tagged **CERTIFIED-PROBE** (source=PROBE at the
  current env_revision in the snapshot).
- Do NOT re-install or re-check facts already tagged CERTIFIED-PROBE with
  status PRESENT — they are proven present at the current revision.
- Focus your effort on requirements tagged REQUIRED, MISSING, or UNKNOWN, and on
  open failures listed in the snapshot.
- Facts tagged hypothesis(...) are unverified — treat them as leads, not truths.

## Response format

Respond each turn with exactly:
Thought: <identify the root-cause layer, cite certified vs hypothesis facts>
Action: <a single shell command>

When the environment is fully configured and the test suite is runnable, respond:
Thought: <why the environment is complete>
Final Answer: Success

IMPORTANT: emit the command ONLY as a plain text line starting with "Action: "
followed by one shell command.  Do NOT use tool-call, function-call, or
XML/<invoke> formats — they will not be parsed.

You do not certify environment facts; the host verifies them with probes after
each action.  Never claim a requirement is PRESENT or MISSING in your Thought.
"""

# ---------------------------------------------------------------------------
# Renderer (§3.3)
# ---------------------------------------------------------------------------


def render_fullstate_view(
    snapshot: EnvStateSnapshot,
    recent_observations: List[Tuple[bool, str]],
) -> str:
    """Render ALL EnvStateSnapshot fields as a structured text view for the
    FullStateWorkerPlanner (§3.3).

    Tags each requirement CERTIFIED-PROBE (source==Source.PROBE) or
    hypothesis(<source>) so the worker knows which facts to trust.
    """
    lines: List[str] = []

    # Header
    lines.append(
        f"# EnvState (revision {snapshot.revision}, container {snapshot.container_id})"
    )

    # Base facts
    b = snapshot.base
    lines.append(
        f"Base: image={b.image} distro={b.distro} arch={b.arch} "
        f"python={b.python} workdir={b.workdir}"
    )

    # Repository layout
    if snapshot.repo_structure:
        struct_lines = snapshot.repo_structure.splitlines()
        truncated = len(struct_lines) > _REPO_LAYOUT_CAP
        shown = struct_lines[:_REPO_LAYOUT_CAP]
        lines.append("")
        header = f"## Repository Layout (workdir={b.workdir})"
        if truncated:
            header += (
                f"  [truncated to {_REPO_LAYOUT_CAP} of {len(struct_lines)} lines]"
            )
        lines.append(header)
        lines.extend(shown)

    # Requirements
    if snapshot.requirements:
        lines.append("")
        lines.append("## Requirements")
        for req in snapshot.requirements:
            if req.source == Source.PROBE:
                trust = "CERTIFIED-PROBE"
            else:
                trust = f"hypothesis({req.source})"
            parts = [f"- [{req.status}] {req.id} ({req.kind}) via {trust}"]
            if req.specifier:
                parts.append(f"specifier={req.specifier}")
            if req.required_by:
                parts.append(f"required_by={list(req.required_by)}")
            if req.evidence:
                ev = req.evidence
                parts.append(f"evidence(cmd={ev.probe_cmd!r} rc={ev.rc})")
            lines.append(" ".join(parts))

    # Provider facts
    if snapshot.provider_facts:
        lines.append("")
        lines.append("## Provider Facts")
        for pf in snapshot.provider_facts:
            lines.append(
                f"- {pf.provider} provides {list(pf.provides)} via {pf.source}"
            )

    # Open failures
    if snapshot.open_failures:
        lines.append("")
        lines.append("## Open Failures")
        for fail in snapshot.open_failures:
            line = (
                f"- {fail.signature} "
                f"(rev {fail.first_seen_revision}->{fail.last_seen_revision})"
            )
            if fail.hypothesis:
                line += f": {fail.hypothesis}"
            if fail.already_tried:
                line += f" | already_tried={list(fail.already_tried)}"
            lines.append(line)

    # Stale evidence
    if snapshot.stale_evidence:
        lines.append("")
        lines.append("## Stale Evidence (re-verify if needed)")
        for req in snapshot.stale_evidence:
            lines.append(f"- {req.id} [{req.status}] (was {req.source})")

    # Plan notes
    if snapshot.plan_notes:
        lines.append("")
        lines.append("## Plan Notes")
        for note in snapshot.plan_notes:
            lines.append(f"- {note}")

    # Last ≤3 observations
    last3 = list(recent_observations)[-3:]
    if last3:
        lines.append("")
        lines.append("## Recent Observations")
        for ok, obs in last3:
            status = "ok" if ok else "FAIL"
            lines.append(f"- [{status}] {obs}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FullStateWorkerPlanner (§3.9 / §3.8)
# ---------------------------------------------------------------------------


class FullStateWorkerPlanner:
    """Mirrors LlmWorkerPlanner but builds its user message from
    render_fullstate_view(self.get_snapshot(), recent_observations) PLUS the
    task_brief.  Used by both Arm A (no Supervisor) and Arm C (with Supervisor,
    worker-planner swapped via §3.8).

    Takes an optional get_snapshot callable so the latest certified snapshot is
    injected fresh at every next_action call (env_snapshot is threaded onto self
    by the observer between steps).
    """

    def __init__(
        self,
        client: Any,
        model: str,
        get_snapshot: Optional[Callable[[], EnvStateSnapshot]] = None,
        on_usage: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.client = client
        self.model = model
        self.get_snapshot = get_snapshot
        self.on_usage = on_usage
        self.history: List[dict] = []

    def reset(self) -> None:
        """Clear per-task ReAct history (called by Worker.run_task at task boundaries)."""
        self.history = []

    def next_action(
        self,
        task_brief: str,
        recent_observations: List[Tuple[bool, str]],
    ) -> Tuple[str, bool]:
        """Build the user message from render_fullstate_view + task_brief, call
        the LLM, and return (action, is_finished)."""
        snapshot = self.get_snapshot() if self.get_snapshot is not None else None

        if snapshot is not None:
            context = render_fullstate_view(snapshot, recent_observations)
            user_content = f"{context}\n\n---\nTask: {task_brief}"
        else:
            user_content = task_brief

        if not self.history:
            self.history.append({"role": "user", "content": user_content})
        elif recent_observations:
            _ok, observation = recent_observations[-1]
            self.history.append({"role": "user", "content": f"Observation: {observation}"})

        messages = [{"role": "system", "content": FULLSTATE_WORKER_SYSTEM_PROMPT}] + self.history
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0, stop=["Observation:"]
        )
        content = response_text(response)
        if self.on_usage is not None:
            usage_obj = getattr(response, "usage", None)
            self.on_usage({
                "input_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
            })
        self.history.append({"role": "assistant", "content": content})
        action = _extract_worker_action(content)
        is_finished = _is_worker_finished(content)
        log_llm_exchange(
            "fullstate_worker", response,
            parsed={"action": action, "is_finished": is_finished},
        )
        if is_finished:
            return "", True
        return action, False


# ---------------------------------------------------------------------------
# run_fullstate_loop (§3.2, §3.5, §3.9)
# ---------------------------------------------------------------------------


def run_fullstate_loop(
    planner: FullStateWorkerPlanner,
    get_snapshot: Callable[[], EnvStateSnapshot],
    step_fn: Callable[[str], Tuple[bool, str]],
    global_action_budget: int,
    interruption_decision: Callable[[List[Tuple[bool, str]], str], bool],
) -> WorkerReport:
    """Single planner-less ReAct loop (Arm A).

    - No per-task sub-cap; the global_action_budget is the only ceiling.
    - Reuses _extract_worker_action / _is_worker_finished from worker.py.
    - Empty-action guard mirrors Worker.run_task logic (§3.9).
    - Calls interruption_decision with a rolling last-3 observation window (§3.5/I1).
    - Returns a WorkerReport with task_id="fullstate".
    """
    task_id = "fullstate"
    observations: List[Tuple[bool, str]] = []
    commands: List[str] = []
    blockers: List[str] = []
    empty_responses = 0
    actions_executed = 0

    while True:
        if actions_executed >= global_action_budget:
            return WorkerReport(
                task_id, "blocked", "global action budget exhausted",
                tuple(commands), tuple(blockers),
            )

        brief = "Configure the repository environment end-to-end."
        action, is_finished = planner.next_action(brief, observations[-3:])

        if is_finished:
            return WorkerReport(
                task_id, "complete", "worker signaled completion",
                tuple(commands), tuple(blockers),
            )

        # Empty-action guard (mirrors Worker.run_task, §3.9)
        if action is None or not action.strip():
            empty_responses += 1
            note = "worker planner returned no parseable action"
            blockers.append(note)
            if empty_responses >= MAX_EMPTY_PLANNER_RESPONSES:
                return WorkerReport(
                    task_id, "blocked", note,
                    tuple(commands), tuple(blockers),
                )
            continue
        empty_responses = 0

        # Shared interruption policy with rolling last-3 window (§3.5/I1)
        if interruption_decision(observations[-3:], action):
            return WorkerReport(
                task_id, "interrupted",
                f"interruption policy fired on: {action}",
                tuple(commands), tuple(blockers),
            )

        success, observation = step_fn(action)
        actions_executed += 1
        commands.append(action)
        observations.append((success, observation))
        if not success:
            blockers.append(
                observation.strip().splitlines()[-1] if observation.strip() else "unknown failure"
            )
