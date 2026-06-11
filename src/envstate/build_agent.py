"""src/envstate/build_agent.py — v1 BuildAgent (mini-ReAct loop per Task).

See spec §4 (build agent loop) and §6 (fixed stuck guard).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

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
# Command-composition validation (Phase 5a — pre-submission self-check)
# ---------------------------------------------------------------------------

# Tokens that indicate a shell operator separating two sub-commands.
_CHAIN_OPS_RE = re.compile(r"\s*(?:&&|;|\|\|)\s*")

# Keywords that identify a MUTATION sub-command (setup / install / build / edit).
_MUTATION_KEYWORDS: tuple[str, ...] = (
    "pip install",
    "pip uninstall",
    "apt-get install",
    "apt-get remove",
    "apt install",
    "apt remove",
    "yum install",
    "dnf install",
    "apk add",
    "brew install",
    "npm install",
    "npm ci",
    "yarn install",
    "poetry install",
    "pipenv install",
    "make",
    "cmake",
    "cargo build",
    "go build",
    "mvn install",
    "gradle build",
    "python setup.py",
    "pip install -e",
    "pip install -r",
)

# Keywords that identify a PROBE/VERIFICATION sub-command (read-only checks).
_PROBE_KEYWORDS: tuple[str, ...] = (
    "python -c",
    "python3 -c",
    "python -m pytest",
    "python3 -m pytest",
    "pytest",
    "py.test",
    "pip show",
    "pip list",
    "pip check",
    "ls ",
    "ls\t",
    "cat ",
    "find ",
    "which ",
    "dpkg -l",
    "dpkg -s",
    "rpm -q",
    "import ",
)

# Shell operators that introduce an output pipe to a filter command.
_PIPE_FILTER_RE = re.compile(r"\|\s*(?:grep|head|tail|sed|awk)\b")

# Commands whose OUTPUT we care about filtering (setup / test runners).
_SETUP_TEST_PREFIXES: tuple[str, ...] = (
    "pip ",
    "pip3 ",
    "apt-get ",
    "apt ",
    "yum ",
    "dnf ",
    "apk ",
    "brew ",
    "npm ",
    "yarn ",
    "poetry ",
    "pipenv ",
    "make",
    "cmake",
    "pytest",
    "py.test",
    "python -m pytest",
    "python3 -m pytest",
    "python setup.py",
)

# Navigation tokens that are NOT mutations (safe to chain with mutations).
_NAV_ONLY_KEYWORDS: tuple[str, ...] = ("cd ",)


def _is_mutation(fragment: str) -> bool:
    """Return True when *fragment* looks like a setup/install/build mutation."""
    stripped = fragment.strip()
    if any(stripped.startswith(nav) for nav in _NAV_ONLY_KEYWORDS):
        return False
    return any(stripped.startswith(kw) or kw in stripped for kw in _MUTATION_KEYWORDS)


def _is_probe(fragment: str) -> bool:
    """Return True when *fragment* looks like a verification/probe/read command."""
    stripped = fragment.strip()
    return any(stripped.startswith(kw) or kw in stripped for kw in _PROBE_KEYWORDS)


def _is_setup_or_test_command(cmd: str) -> bool:
    """Return True when *cmd* starts with a setup or test-runner prefix."""
    stripped = cmd.strip()
    return any(stripped.startswith(p) for p in _SETUP_TEST_PREFIXES)


def validate_command_composition(cmd: str) -> Optional[str]:
    """Check *cmd* against the two sandbox composition rules.

    Returns a short corrective message string if a rule is violated, else None.

    Rule 1 — ONE mutation per Action: never combine a mutation (pip install,
    apt-get install, make, …) with a verification/probe/read (python -c "import …",
    pytest, pip show, ls, cat, …) in a single chained command.

    Rule 2 — Never pipe setup/test output through grep/head/tail/sed/awk.
    """
    if not cmd or not cmd.strip():
        return None

    # Rule 2: piping setup/test output through a filter
    if _PIPE_FILTER_RE.search(cmd) and _is_setup_or_test_command(cmd):
        return (
            "Command composition rule violated: do not pipe setup or test output through "
            "grep/head/tail/sed/awk. Instead redirect to a file "
            "(e.g. `<cmd> > /tmp/out.log 2>&1`) and read it in a SEPARATE read-only "
            "Action (`cat /tmp/out.log`)."
        )

    # Rule 1: mutation + probe chained via && / ; / ||
    # Split on chain operators and examine each fragment.
    fragments = _CHAIN_OPS_RE.split(cmd)
    if len(fragments) < 2:
        return None  # single command — nothing to chain-check

    mutations = [f for f in fragments if _is_mutation(f)]
    probes = [f for f in fragments if _is_probe(f)]

    if mutations and probes:
        return (
            "Command composition rule violated: do not combine a setup mutation "
            f"(e.g. `{mutations[0].strip()[:60]}`) with a verification/probe "
            f"(e.g. `{probes[0].strip()[:60]}`) in one Action. "
            "Run each as a SEPARATE Action so each state change is confirmed independently.\n"
            "BAD:  pip install requests && python -c 'import requests'\n"
            "GOOD: Action 1: pip install requests\n"
            "      Action 2: python -c 'import requests'"
        )

    return None


# ---------------------------------------------------------------------------
# System prompt (Repo2Run-style env-config methodology, scoped to one Planner task)
# ---------------------------------------------------------------------------

BUILD_AGENT_SYSTEM_PROMPT = f"""\
You are an expert skilled in environment configuration for Python repositories,
working inside a Docker container. You can inspect the repository's files and
structure — `requirements.txt`, `setup.py`, `setup.cfg`, `pyproject.toml`,
`Pipfile`, `poetry.lock`, and the like — and use the project's own build system
(pip / poetry / setuptools) and dependency tools to install the third-party
libraries the project needs.

You do NOT set up the whole repository on your own. A Planner has analyzed the
environment and handed you ONE scoped task for this turn. You are given:
  Task goal      — the single sub-goal to accomplish
  Done when      — the concrete, command-checkable criterion that means it is done
  Layer          — the stack layer this task targets (base | system | runtime | deps | build | tests)
  Relevant facts — facts the Planner already established, so you do not re-discover them

Accomplish the goal, verify the "Done when" criterion with a real command, then stop.

## How to work
- Prefer the project's declared configuration over guessing: read the manifest for
  the task's Layer and install via the build system the repo actually uses
  (`poetry install`, `pip install -e .`, `pip install -r requirements.txt`, ...).
- When a command fails, read its output and fix the root cause before moving on —
  a missing system library/header (`apt-get install ...`), a wrong interpreter, a
  missing package. Do not paper over a failure one layer above its cause.
- Confirm progress with a real check (an import, `pip show`, `--version`, or the
  task's own done-when command). Trust commands, not assumptions.
- You have up to {LOCAL_BUDGET} commands for this task. Be economical: accomplish the
  goal in as few turns as possible, chaining confident, related steps into ONE `&&`
  line, and spend a separate turn only when you must see a command's result first.

## Command composition rules (IMPORTANT — violations are rejected before execution)

These two rules are enforced by the sandbox. Violating them wastes a turn.

### Rule 1 — ONE mutation per Action; never combine mutation + verification
A "mutation" changes the environment: `pip install`, `apt-get install`, `make`,
file edits, etc.
A "verification/probe" checks a result: `python -c "import X"`, `pytest`,
`pip show`, `ls`, `cat`, etc.

Never combine them in one chained command. Run each as a SEPARATE Action so each
state change is confirmed independently.

BAD:   pip install requests && python -c 'import requests'
GOOD:  Action 1: pip install requests
       Action 2: python -c 'import requests'

BAD:   apt-get install -y libpq-dev && pip show psycopg2
GOOD:  Action 1: apt-get install -y libpq-dev
       Action 2: pip show psycopg2

Note: chaining `cd /app && pip install -e .` is fine — `cd` is navigation, not a
mutation.

### Rule 2 — Never pipe setup/test output through grep/head/tail/sed/awk
Setup and test commands must not pipe their output through filtering tools.
If you need to inspect long output, redirect it to a file and read it separately.

BAD:   pytest 2>&1 | grep -i error
BAD:   pip install -r requirements.txt | tail -20
GOOD:  pytest > /tmp/out.log 2>&1
       (then in a separate Action) cat /tmp/out.log

Note: read-only pipelines are fine (e.g. `ls -la | grep foo`, `cat file | grep X`).

## Response format
Each turn, respond with exactly:
Thought: <reasoning — cite the goal, the relevant facts, and the last observation>
Action: <a single shell command>

- Keep the command on ONE line. Chain steps with `&&`. Do NOT use multi-line
  commands, backslash continuations, or here-docs (<<) — they cause parsing errors.
- Emit exactly one Action per turn. After it runs you will see:
  Observation: [ok|FAILED] <output>

When the task's "Done when" criterion is verified by a real command, respond with:
Thought: <why the criterion is now satisfied>
Final Answer: Success

## Rules
- Stay within your task. You do not plan the overall setup (the Planner does) and
  you do not certify environment facts (the host re-probes after you) — just
  accomplish the goal and report success.
- Do not make extensive changes to files in the repository; make only appropriate
  and necessary changes, and only when there is an actual error.
- Passing tests by modifying or deleting test functions is NOT allowed — make the
  existing tests run by fixing the environment, not the tests.
- Do not open interactive sessions (`poetry shell`, a bare `python` REPL); run only
  non-interactive commands.
- Report "Final Answer: Success" only after a real command has verified "Done when".
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

            # Pre-submission self-check: reject composition rule violations
            # before sending to the sandbox, feeding the corrective message back
            # as the observation so the model can fix its proposal immediately
            # without consuming a sandbox round-trip.
            composition_error = validate_command_composition(action)
            if composition_error:
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            f"Observation: [FAILED — self-check, command NOT executed]\n"
                            f"{composition_error}"
                        ),
                    },
                ]
                continue

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
