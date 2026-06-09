from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.llm_response import response_text

# A worker executor runs one action and returns (success, observation).
WorkerExecutor = Callable[[str], Tuple[bool, str]]

DEFAULT_MAX_ACTIONS = 6
DEP_PIN_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile")
# Allow one re-prompt for an unparseable planner response before giving up, so a
# reasoning model that drops the Action line once does not spin the action budget.
MAX_EMPTY_PLANNER_RESPONSES = 2


@dataclass(frozen=True)
class WorkerReport:
    task_id: str
    status: str  # "complete" | "blocked" | "interrupted"
    summary: str
    commands_attempted: Tuple[str, ...] = ()
    observed_blockers: Tuple[str, ...] = ()


def _looks_like_pin_edit(action: str) -> bool:
    normalized = action.lower()
    mutating_verb = any(v in normalized for v in ("sed -i", " > ", " >> ", "tee ", "rm ", "mv "))
    touches_dep_file = any(f in normalized for f in DEP_PIN_FILES)
    return mutating_verb and touches_dep_file


def interruption_decision(recent_window: List[Tuple[bool, str]], action: str) -> bool:
    """Shared repeated-identical-failure guard (design §3.5/I1).

    Parameterised by a fixed rolling window of the last N observations so the
    firing semantics are identical across all arms (A/B/C).  Arms B/C call this
    from should_interrupt with observations[-3:]; Arm A calls it with a rolling
    last-3 window over its single contiguous history.

    Only the repeated-failure check lives here; budget and pin-edit checks stay
    in should_interrupt so Arm B's should_interrupt call remains bit-identical.
    """
    failures = [obs for ok, obs in recent_window if not ok]
    if len(failures) >= 2 and failures[-1].strip() == failures[-2].strip():
        return True
    return False


def should_interrupt(
    task_spec: dict[str, Any],
    observations: List[Tuple[bool, str]],
    action: str,
    actions_used: int,
) -> bool:
    """Host-enforced interruption policy (design §14)."""
    max_actions = task_spec.get("max_actions", DEFAULT_MAX_ACTIONS)
    if actions_used >= max_actions:
        return True
    if _looks_like_pin_edit(action):
        return True
    # repeated identical failure signature — delegate to shared guard
    if interruption_decision(observations[-3:], action):
        return True
    return False


class Worker:
    """Bounded ReAct execution inside one TaskSpec.

    `planner` is any object with next_action(task_brief, recent_observations)
    -> (action:str, is_finished:bool). In production this wraps the shared LLM
    client with a worker-scoped prompt; in tests it is a fake.
    """

    def __init__(self, planner, max_actions: int = DEFAULT_MAX_ACTIONS, workdir: Optional[str] = None):
        self.planner = planner
        self.max_actions = max_actions
        self.workdir = workdir

    def run_task(self, task_spec: dict[str, Any], executor: WorkerExecutor) -> WorkerReport:
        task_id = task_spec.get("task_id", "task")
        brief = build_task_brief(task_spec)
        if self.workdir:
            brief = (
                f"Working directory: {self.workdir} — the repository is checked out here;"
                f" cd here for repo files.\n\n" + brief
            )
        # Reset per-task ReAct history so each task starts fresh and receives its own brief.
        # Safe for fake planners that lack reset(): the guard is a no-op for them.
        reset = getattr(self.planner, "reset", None)
        if callable(reset):
            reset()
        observations: List[Tuple[bool, str]] = []
        commands: List[str] = []
        blockers: List[str] = []
        empty_responses = 0
        max_actions = task_spec.get("max_actions", self.max_actions)

        while True:
            if len(commands) >= max_actions:
                return WorkerReport(task_id, "blocked", "action budget exhausted",
                                    tuple(commands), tuple(blockers))
            action, is_finished = self.planner.next_action(brief, observations[-3:])
            if is_finished:
                # Uniform completion: the worker signals done and does NOT execute a
                # trailing action — the Supervisor decides what happens next.
                return WorkerReport(task_id, "complete", "worker signaled completion",
                                    tuple(commands), tuple(blockers))
            # An empty/unparseable action (e.g. a reasoning model that dropped the
            # Action line) must NOT be run as a shell command — that would burn the
            # budget on no-op successes. Re-prompt once, then bail out as blocked.
            if action is None or not action.strip():
                empty_responses += 1
                note = "worker planner returned no parseable action"
                blockers.append(note)
                if empty_responses >= MAX_EMPTY_PLANNER_RESPONSES:
                    return WorkerReport(task_id, "blocked", note,
                                        tuple(commands), tuple(blockers))
                continue
            empty_responses = 0
            # Check interruption BEFORE executing a constraint-violating action.
            if should_interrupt(task_spec, observations, action, actions_used=len(commands)):
                return WorkerReport(task_id, "interrupted",
                                    f"interruption policy fired on: {action}",
                                    tuple(commands), tuple(blockers))
            success, observation = executor(action)
            commands.append(action)
            observations.append((success, observation))
            if not success:
                blockers.append(observation.strip().splitlines()[-1] if observation.strip() else "unknown failure")


def build_task_brief(task_spec: dict[str, Any]) -> str:
    """Narrow brief for the worker (design §9 worker input)."""
    parts = [
        f"Task: {task_spec.get('goal', '')}",
        "Relevant facts:\n" + "\n".join(f"- {s}" for s in task_spec.get("relevant_state", [])),
        "Constraints:\n" + "\n".join(f"- {s}" for s in task_spec.get("constraints", [])),
        "Allowed actions:\n" + "\n".join(f"- {s}" for s in task_spec.get("allowed_actions", [])),
        "Success criteria:\n" + "\n".join(f"- {s}" for s in task_spec.get("success_criteria", [])),
        "Stop conditions:\n" + "\n".join(f"- {s}" for s in task_spec.get("stop_conditions", [])),
    ]
    return "\n\n".join(parts)


WORKER_SYSTEM_PROMPT = """You are the ReAct build Worker for DockerAgent.

You execute ONE bounded setup task by issuing shell commands inside the container.
Work only within the task's goal, constraints, and allowed actions. Do local trial
and error, but never edit dependency pin files, never change the task's scope, and
never claim the whole environment is done.

Respond each turn with exactly:
Thought: <your reasoning>
Action: <a single shell command>

When the task's success criteria are met, instead respond with:
Thought: <why the task is complete>
Final Answer: Success

When the task's goal is verification, run the repository's Repo2Run collection
command ("pytest --collect-only -q --disable-warnings", or
"poetry run pytest --collect-only -q --disable-warnings" for Poetry projects) from
the repository root. A successful collection is the proof the environment works.

You do not certify environment facts; the host verifies them with probes.
"""

_ACTION_RE = re.compile(r"^\s*Action:\s*(.+?)\s*$", re.MULTILINE)
_FINAL_RE = re.compile(r"^\s*Final Answer:\s*(Success|Failure)\b", re.IGNORECASE | re.MULTILINE)
_TOOLCALL_CMD_RE = re.compile(r'<parameter\s+name="command"\s*>(.*?)</parameter>', re.DOTALL)


def _extract_worker_action(content: str) -> str:
    # Primary path: plain "Action: <cmd>" line (Arm B — must remain byte-identical)
    match = _ACTION_RE.search(content or "")
    if match:
        action = match.group(1).strip()
        action = re.sub(r"^```[a-zA-Z]*\n?", "", action)
        action = re.sub(r"\n?```$", "", action).strip()
        return action.splitlines()[0].strip() if action else ""

    # Fallback: XML/tool-call format emitted by MiniMax and similar models
    tc_match = _TOOLCALL_CMD_RE.search(content or "")
    if tc_match:
        action = tc_match.group(1).strip()
        action = re.sub(r"^```[a-zA-Z]*\n?", "", action)
        action = re.sub(r"\n?```$", "", action).strip()
        return action

    return ""


def _is_worker_finished(content: str) -> bool:
    return bool(_FINAL_RE.search(content or ""))


class LlmWorkerPlanner:
    """Adapter exposing next_action(task_brief, recent_observations) -> (action, is_finished)
    over the shared OpenAI-compatible client. Maintains its own short ReAct history.
    """

    def __init__(self, client, model, on_usage=None):
        self.client = client
        self.model = model
        self.on_usage = on_usage
        self.history: List[dict] = []

    def reset(self) -> None:
        """Clear per-task ReAct history so the next task starts fresh.

        Called by Worker.run_task() at the boundary between tasks.  Within a
        single task the planner still accumulates its own action/observation
        turns as before — only the cross-task boundary is reset here.
        """
        self.history = []

    def next_action(self, task_brief: str, recent_observations: List[Tuple[bool, str]]):
        if not self.history:
            self.history.append({"role": "user", "content": task_brief})
        elif recent_observations:
            _ok, observation = recent_observations[-1]
            self.history.append({"role": "user", "content": f"Observation: {observation}"})
        messages = [{"role": "system", "content": WORKER_SYSTEM_PROMPT}] + self.history
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
        log_llm_exchange("worker", response, parsed={"action": action, "is_finished": is_finished})
        if is_finished:
            return "", True
        return action, False
