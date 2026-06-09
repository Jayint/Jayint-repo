"""src/envstate/build_agent.py — v1 BuildAgent (mini-ReAct loop per Task).

See spec §4 (build agent loop) and §6 (fixed stuck guard).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, List, Tuple

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.llm_response import complete_with_retry
from src.envstate.world_model import CommandRecord, Task, TaskReport

# ---------------------------------------------------------------------------
# Module-level constants (spec §8)
# ---------------------------------------------------------------------------

LOCAL_BUDGET: int = 8          # shell actions per task before forced "blocked"
MAX_EMPTY_RESPONSES: int = 2   # re-prompts allowed for unparseable LLM output

# ---------------------------------------------------------------------------
# v0 compatibility symbols — inlined from worker.py (deleted in Task 37).
# fullstate_worker.py, agent.py, and tests now import these from here.
# ---------------------------------------------------------------------------

DEFAULT_MAX_ACTIONS: int = 6
MAX_EMPTY_PLANNER_RESPONSES: int = 2

_DEP_PIN_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile")


@dataclass(frozen=True)
class WorkerReport:
    """v0 worker result (kept for fullstate_worker.py and its tests)."""
    task_id: str
    status: str  # "complete" | "blocked" | "interrupted"
    summary: str
    commands_attempted: Tuple[str, ...] = ()
    observed_blockers: Tuple[str, ...] = ()


def interruption_decision(
    recent_window: List[Tuple[bool, str]], action: str
) -> bool:
    """Shared repeated-identical-failure guard (design §3.5/I1).

    Parameterised by a fixed rolling window of the last N observations so the
    firing semantics are identical across all arms (A/B/C).
    Only the repeated-failure check lives here; budget and pin-edit checks stay
    in should_interrupt so Arm B's should_interrupt call remains bit-identical.
    """
    failures = [obs for ok, obs in recent_window if not ok]
    if len(failures) >= 2 and failures[-1].strip() == failures[-2].strip():
        return True
    return False

# ---------------------------------------------------------------------------
# Action parsing — ported verbatim from worker.py (_extract_worker_action /
# _is_worker_finished).  Kept inline to avoid circular imports once worker.py
# is deleted (spec §6 deletion note).
# ---------------------------------------------------------------------------

_ACTION_RE = re.compile(r"^\s*Action:\s*(.+?)\s*$", re.MULTILINE)
# Matches "Action: ```lang\n<body>\n```" — fenced block spanning multiple lines.
_ACTION_FENCED_RE = re.compile(
    r"^\s*Action:\s*```[a-zA-Z]*\n(.*?)```", re.MULTILINE | re.DOTALL
)
_FINAL_RE = re.compile(
    r"^\s*Final Answer:\s*Success\b", re.IGNORECASE | re.MULTILINE
)
_TOOLCALL_CMD_RE = re.compile(
    r'<parameter\s+name="command"\s*>(.*?)</parameter>', re.DOTALL
)

# Prefix emitted by Sandbox.execute when a command is rejected before running
# (agent.py:194-197, sandbox.py:700-707, sandbox.py:823-825).
_PREFLIGHT_REJECTION_PREFIX = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION"


def _extract_worker_action(content: str) -> str:
    """Extract Action line from LLM content (mirrors worker.py verbatim).

    Handles three formats:
    1. Plain: Action: <command>
    2. Fenced: Action: ```lang\\n<command>\\n```
    3. XML tool-call: <parameter name="command">...</parameter>
    """
    # Try fenced block first (superset of plain — avoids capturing only the
    # fence header on multi-line fences).
    fenced_match = _ACTION_FENCED_RE.search(content or "")
    if fenced_match:
        return fenced_match.group(1).strip().splitlines()[0].strip()

    match = _ACTION_RE.search(content or "")
    if match:
        action = match.group(1).strip()
        action = re.sub(r"^```[a-zA-Z]*\n?", "", action)
        action = re.sub(r"\n?```$", "", action).strip()
        return action.splitlines()[0].strip() if action else ""
    tc_match = _TOOLCALL_CMD_RE.search(content or "")
    if tc_match:
        action = tc_match.group(1).strip()
        action = re.sub(r"^```[a-zA-Z]*\n?", "", action)
        action = re.sub(r"\n?```$", "", action).strip()
        return action
    return ""


def _is_worker_finished(content: str) -> bool:
    """Return True when the LLM emits Final Answer: Success."""
    return bool(_FINAL_RE.search(content or ""))


# ---------------------------------------------------------------------------
# Fixed stuck guard (spec §6)
# ---------------------------------------------------------------------------

def _is_stuck(
    history: list[CommandRecord],
    action: str,
    is_preflight_rejection: bool,
) -> bool:
    """Fixed interruption guard from spec §6.

    Returns True only when ALL of:
      (a) The last two MUTATING commands (non-rejection, rc != 0) have identical
          output.
      (b) At least one self-correction attempt was already made (i.e. ≥2
          mutating failures have been seen).

    Ignores preflight rejections entirely — is_preflight_rejection=True means
    the command was never executed, so it must NOT increment the stuck counter.
    Preflight rejection records in history (identified by their output prefix)
    are also excluded from the real-failure list.
    """
    if is_preflight_rejection:
        return False
    # Collect only real execution failures (rc != 0, not preflight rejections).
    real_failures = [
        r
        for r in history
        if r.rc != 0
        and not r.output.startswith(_PREFLIGHT_REJECTION_PREFIX)
    ]
    if len(real_failures) < 2:
        return False
    return real_failures[-1].output.strip() == real_failures[-2].output.strip()


# ---------------------------------------------------------------------------
# System prompt (layered RCA from fullstate_worker.py, simplified for v1)
# ---------------------------------------------------------------------------

BUILD_AGENT_SYSTEM_PROMPT = """\
You are the v1 Build Agent for DockerAgent environment setup.

Your job is to accomplish ONE scoped task by issuing shell commands inside
the container.  You have a task goal, a done-when criterion, and a set of
relevant facts about the environment.

## Layered Root-Cause Analysis

Before each action, identify which layer needs attention and justify your
next command from the given facts:

  1. base image       — OS / distribution / architecture constraints
  2. system packages  — apt/yum/apk native libraries and headers
  3. runtime          — Python version, interpreter, pip/virtualenv toolchain
  4. deps             — project Python/language packages
  5. build            — compilation, linking, editable installs
  6. tests            — test runner availability, collection correctness

Work from the bottom of the stack upward.  Do not paper over a symptom one
layer above its cause.

## Response format

Respond each turn with exactly:
Thought: <identify root-cause layer, cite given facts>
Action: <a single shell command>

When the task's done_when criterion is met, respond with:
Thought: <why the task criterion is satisfied>
Final Answer: Success

IMPORTANT: emit the command ONLY as a plain line starting with "Action: "
followed by one shell command.  Do NOT use tool-call or XML formats.

You do not certify environment facts.  Report "Final Answer: Success" only
when you have verified the task's done_when criterion with a real command.
"""


# ---------------------------------------------------------------------------
# BuildAgent
# ---------------------------------------------------------------------------

class BuildAgent:
    """Mini-ReAct loop for one Task.

    Injected dependencies (for testability without Docker):
      client       — OpenAI-compatible LLM client
      model        — model slug (str)
      synthesizer  — Synthesizer instance (for classify_mutation /
                     command_mutates_environment)
      container_id — str identifier forwarded to ActionEvent
      on_usage     — optional callback called with usage dict after each LLM
                     completion; keys: input_tokens, output_tokens, total_tokens
      log_path     — optional path for structured LLM exchange logs
    """

    def __init__(
        self,
        client: Any,
        model: str,
        synthesizer: Any,
        container_id: str = "unknown",
        on_usage: Callable[[dict], None] | None = None,
        log_path: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.synthesizer = synthesizer
        self.container_id = container_id
        self.on_usage = on_usage
        self.log_path = log_path

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        task: Task,
        sandbox_execute: Callable[[str], tuple[bool, str]],
        ledger: ActionLedger,
        step_offset: int = 0,
    ) -> TaskReport:
        """Mini-ReAct loop capped at LOCAL_BUDGET shell actions.

        Returns TaskReport(status='done') when the LLM emits
        "Final Answer: Success" for the task's done_when criterion.
        Returns TaskReport(status='blocked') on budget exhaustion or
        the stuck guard firing.
        """
        history: list[CommandRecord] = []
        messages: list[dict] = [
            {"role": "system", "content": BUILD_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_task_message(task)},
        ]
        env_revision = step_offset
        empty_responses = 0
        steps_executed = 0

        for _step in range(LOCAL_BUDGET):
            text, usage, raw_response = complete_with_retry(
                self.client,
                self.model,
                messages,
                temperature=0,
                stop=["Observation:"],
            )
            if self.on_usage:
                self.on_usage(usage)
            log_llm_exchange("build_agent", raw_response, parsed={"step": _step})

            action = _extract_worker_action(text)
            finished = _is_worker_finished(text)

            if finished:
                return TaskReport(
                    task_goal=task.goal,
                    status="done",
                    commands=tuple(history),
                    learning=f"Task criterion met: {task.done_when}",
                )

            # Guard: empty / unparseable response
            if not action.strip():
                empty_responses += 1
                if empty_responses >= MAX_EMPTY_RESPONSES:
                    return TaskReport(
                        task_goal=task.goal,
                        status="blocked",
                        commands=tuple(history),
                        learning="LLM returned too many unparseable responses",
                    )
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response did not contain a parseable "
                            "Action line. Respond with 'Action: <command>' or "
                            "'Final Answer: Success'."
                        ),
                    },
                ]
                continue
            empty_responses = 0   # reset on real action

            # Execute
            success, output = sandbox_execute(action)
            is_preflight = output.startswith(_PREFLIGHT_REJECTION_PREFIX)
            rc = 0 if success else 1
            record = CommandRecord(cmd=action, rc=rc, output=output[:2000])

            # Stuck guard (before appending to history so the guard sees
            # the record from the previous cycle, not the current one)
            if _is_stuck(history, action, is_preflight):
                history.append(record)
                return TaskReport(
                    task_goal=task.goal,
                    status="blocked",
                    commands=tuple(history),
                    learning=f"Stuck guard fired (budget exhausted): identical failure twice on '{action}'",
                )

            history.append(record)
            steps_executed += 1

            # Append ActionEvent to ledger (step incremented BEFORE appending
            # so each event gets a distinct, monotonically increasing step number)
            self._append_ledger_event(
                action=action,
                success=success,
                output=output,
                step=step_offset + steps_executed,
                env_revision=env_revision,
                ledger=ledger,
                is_preflight=is_preflight,
            )
            if success and not is_preflight:
                if self.synthesizer.command_mutates_environment(action):
                    env_revision += 1

            # Append to LLM conversation
            observation_prefix = "ok" if success else "FAILED"
            messages = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"Observation: [{observation_prefix}]\n{output[:1500]}",
                },
            ]

        # Budget exhausted
        return TaskReport(
            task_goal=task.goal,
            status="blocked",
            commands=tuple(history),
            learning=f"Ran out of local budget ({LOCAL_BUDGET} steps)",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_task_message(self, task: Task) -> str:
        facts_text = "\n".join(f"- {f}" for f in task.facts) if task.facts else "- (none)"
        return (
            f"Task goal: {task.goal}\n"
            f"Done when: {task.done_when}\n"
            f"Layer: {task.layer}\n"
            f"Relevant facts:\n{facts_text}"
        )

    def _append_ledger_event(
        self,
        action: str,
        success: bool,
        output: str,
        step: int,
        env_revision: int,
        ledger: ActionLedger,
        is_preflight: bool,
    ) -> None:
        """Append one ActionEvent to the ledger (mirrors agent.py:2004 pattern)."""
        if is_preflight:
            # Rejected before execution — record but mark as non-mutating.
            mutation_class = None
            rev_after = env_revision
        elif success and self.synthesizer.command_mutates_environment(action):
            mutation_class = self.synthesizer.classify_mutation(action)
            rev_after = env_revision + 1
        else:
            mutation_class = None
            rev_after = env_revision

        event = ActionEvent(
            step=step,
            task_id=action[:40],
            cmd=action,
            rc=0 if success else 1,
            stdout_path=None,
            stderr_path=None,
            env_revision_before=env_revision,
            env_revision_after=rev_after,
            mutation_class=mutation_class,
            container_id=self.container_id,
            summary=output[:200],
        )
        ledger.append(event)


# Re-export for test convenience (tests import CommandRecord from here)
from src.envstate.world_model import CommandRecord as CommandRecord  # noqa: F401
