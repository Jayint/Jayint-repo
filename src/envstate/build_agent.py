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
from src.envstate.world_model import CommandRecord, RecipePatch, Task, TaskReport

# ---------------------------------------------------------------------------
# Module-level constants (spec §8)
# ---------------------------------------------------------------------------

LOCAL_BUDGET: int = 8          # shell actions per task before forced "blocked"
MAX_EMPTY_RESPONSES: int = 2   # re-prompts allowed for unparseable LLM output

# Recipe-level budget constants (Task 18)
LOCAL_BUDGET_BASE: int = 2         # minimum steps in any recipe sub-loop
LOCAL_BUDGET_PER_STEP: int = 2     # additional steps budget per recipe step
RECIPE_BUDGET_CAP: int = 16        # hard ceiling on recipe total budget


def recipe_budget(n: int) -> int:
    """Compute total shell-action budget for a recipe with *n* steps.

    Budget scales linearly with the number of steps, capped at RECIPE_BUDGET_CAP.
    Examples (BASE=2, PER_STEP=2, CAP=16):
      recipe_budget(1)  = 4
      recipe_budget(5)  = 12
      recipe_budget(50) = 16
    """
    return min(LOCAL_BUDGET_BASE + LOCAL_BUDGET_PER_STEP * n, RECIPE_BUDGET_CAP)

_OUTPUT_HEAD, _OUTPUT_TAIL = 1500, 800   # tail keeps the pytest summary; head keeps tracebacks
_OUTPUT_LIMIT = _OUTPUT_HEAD + _OUTPUT_TAIL


def _truncate_output(output: str) -> str:
    """Keep the head (tracebacks/setup) AND the tail (pytest pass summary).
    Replaces a naive head-only truncation that discarded the '=== N passed ===' line."""
    if len(output) <= _OUTPUT_LIMIT:
        return output
    return (
        output[:_OUTPUT_HEAD].rstrip()
        + "\n...[output truncated]...\n"
        + output[-_OUTPUT_TAIL:].lstrip()
    )

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

# A heredoc operator (`<< DELIM` / `<<-DELIM`, optionally quoted). The leading-letter
# anchor on the delimiter means arithmetic shifts ($((1 << 4))) never match. Mirrors
# synthesis._RE_HEREDOC_OP (kept local to avoid importing synthesis in this hot path).
_RE_HEREDOC_OP = re.compile(r"<<-?\s*['\"]?[A-Za-z_]\w*")


def _is_terminated_heredoc(body: str) -> bool:
    """True when *body* is a MULTI-LINE command that opens a heredoc and carries a
    body+terminator (i.e. contains a newline after a `<< DELIM` operator). Used to
    preserve the full multi-line action instead of truncating to line 1. A single-line
    `<< DELIM` opener (no newline) is NOT terminated and stays truncated."""
    return bool(body) and "\n" in body and bool(_RE_HEREDOC_OP.search(body))


def _reconstruct_plain_heredoc(content: str, action_start: int) -> str:
    """Rebuild a full plain (unfenced) heredoc command from the raw LLM *content*.

    _ACTION_RE is line-bounded (no DOTALL), so for an `Action: cat > f << 'EOF'`
    followed by a body+terminator it captures only the opener line. This recovers the
    body and terminator from *content* (starting at *action_start*), cutting at the
    matching delimiter line so trailing Thought/Observation text is never swallowed.
    Returns the single opener line unchanged when no terminator follows."""
    tail = re.sub(r"^\s*Action:[ \t]*", "", content[action_start:])
    op = _RE_HEREDOC_OP.search(tail.splitlines()[0] if tail else "")
    if not op:
        return tail.splitlines()[0] if tail else ""
    delim_match = re.search(r"([A-Za-z_]\w*)\s*$", op.group(0))
    if not delim_match:
        # Operator matched but no trailing delimiter word (unusual spacing/quoting):
        # fall back to the opener line rather than raising AttributeError.
        return tail.splitlines()[0] if tail else ""
    delim = delim_match.group(1)
    lines = tail.splitlines()
    kept = [lines[0]]
    found = False
    for line in lines[1:]:
        kept.append(line)
        if line.strip() == delim:
            found = True
            break
    if not found:
        # No terminator: keep the opener alone (single line) so it stays unterminated
        # and synthesis._is_unterminated_heredoc drops it (pre-A1 behavior). Returning
        # the multi-line body leaks an unterminated heredoc into the recipe (Bug 2)
        # and swallows trailing Thought/Observation/Action text.
        return lines[0]
    return "\n".join(kept)


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
        body = fenced_match.group(1).strip()
        if _is_terminated_heredoc(body):
            return body
        return body.splitlines()[0].strip()

    match = _ACTION_RE.search(content or "")
    if match:
        action = match.group(1).strip()
        action = re.sub(r"^```[a-zA-Z]*\n?", "", action)
        action = re.sub(r"\n?```$", "", action).strip()
        if not action:
            return ""
        # _ACTION_RE is line-bounded (no DOTALL): a multi-line heredoc is captured as
        # its opener only. If the opener carries a `<< DELIM`, recover the body +
        # terminator from the raw content so the recorded+executed command actually
        # writes the file (parity with the fenced branch). Non-heredoc actions are
        # untouched and still truncate to line 1.
        if "\n" not in action and _RE_HEREDOC_OP.search(action):
            full = _reconstruct_plain_heredoc(content or "", match.start())
            if _is_terminated_heredoc(full):
                return full
        if _is_terminated_heredoc(action):
            return action
        return action.splitlines()[0].strip()
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

# Recipe-level execution semantics (Task 18 — appended to system prompt in run_recipe).
# Explains how the LLM should interpret the full numbered recipe seeded in the user message.
BUILD_AGENT_RECIPE_PROMPT = """\

## Recipe execution mode
You have been given a complete ordered recipe. Your job is to execute each
numbered step's command in sequence. Repair only local errors within the
current step (do not redesign the recipe or reorder steps), and emit
'Final Answer: Success' once the *current* step's command has exited 0.
Do NOT attempt later steps until you have confirmed the current step succeeded.

If a step cannot be repaired (the same failure repeats), stop and clearly
report the blocked step — do not attempt later steps.
"""


V3_PROPOSE_SYSTEM_PROMPT = """\
You are diagnosing ONE failing environment obligation inside a Docker container.
You may run READ-ONLY diagnostics (pkg-config, apt-cache, ldconfig, pip show, ls, cat).
You may NOT install/modify/delete — the host applies your fix, not you.
Each turn respond with ONE of:
  Action: <one read-only shell command>      (you will get an Observation:)
  Final Patch: followed by exactly one fenced ```json PatchProposal object
The patch is the ONLY accepted output — never claim success in prose."""


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
        check: str | None = None,
        budget: int = LOCAL_BUDGET,
    ) -> TaskReport:
        """Mini-ReAct loop capped at ``budget`` LLM turns (default LOCAL_BUDGET).

        Each loop iteration is one LLM call; composition rejections and
        empty-response re-prompts ``continue`` without executing a shell command,
        so the budget counts LLM turns, not shell actions (``steps_executed``
        tracks actual shell calls separately).

        Returns TaskReport(status='done') when the LLM emits
        "Final Answer: Success" for the task's done_when criterion (or, in
        host-check mode, when ``check`` passes). Returns TaskReport(status=
        'blocked') on budget exhaustion or the stuck guard firing.
        """
        history: list[CommandRecord] = []
        messages: list[dict] = [
            {"role": "system", "content": BUILD_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_task_message(task)},
        ]
        env_revision = step_offset
        empty_responses = 0
        steps_executed = 0

        for _step in range(budget):
            # Scheduler mode: the host check is the only done-signal. Probe it at
            # the top of each iteration, before any LLM call, and finalize the
            # instant it passes.
            if check is not None:
                ok, _out = sandbox_execute(check)
                if ok:
                    return TaskReport(
                        task_goal=task.goal,
                        status="done",
                        commands=tuple(history),
                        learning=f"host check satisfied: {check}",
                    )

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

            # When a host check is active it is the only done-signal: the LLM's
            # self-declaration ("Final Answer: Success") MUST NOT finalize
            # (anti-hollow-success). Only the top-of-loop host check finalizes.
            if finished and check is None:
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
            # Heredoc bodies legitimately contain `;`, `&&`, `import `, `cat ` etc.;
            # composition rules govern SHELL command chaining, not heredoc text. Skip the
            # self-check for a terminated heredoc to avoid a false Rule 1/Rule 2 rejection.
            composition_error = (
                None if _is_terminated_heredoc(action)
                else validate_command_composition(action)
            )
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
            record = CommandRecord(cmd=action, rc=rc, output=_truncate_output(output))

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
                if self.synthesizer and self.synthesizer.command_mutates_environment(action):
                    env_revision += 1

            # Append to LLM conversation
            observation_prefix = "ok" if success else "FAILED"
            messages = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"Observation: [{observation_prefix}]\n{_truncate_output(output)}",
                },
            ]

        # Budget exhausted
        return TaskReport(
            task_goal=task.goal,
            status="blocked",
            commands=tuple(history),
            learning=f"Ran out of local budget ({budget} steps)",
        )

    # ------------------------------------------------------------------
    # Recipe execution (Task 18)
    # ------------------------------------------------------------------

    def run_recipe(
        self,
        recipe: RecipePatch,
        sandbox_execute: Callable[[str], tuple[bool, str]],
        ledger: ActionLedger,
        step_offset: int = 0,
    ) -> TaskReport:
        """Execute all steps of *recipe* as a single unified mini-ReAct loop.

        Differences from the naive per-step delegation:

        - Seeds the LLM with the **full numbered recipe** in the initial user
          message so the agent sees the complete plan from the start.
        - Uses :func:`recipe_budget` to compute one total budget cap shared
          across all steps (``recipe_budget(n) ≤ RECIPE_BUDGET_CAP``), rather
          than the per-step ``LOCAL_BUDGET``.
        - ``Final Answer: Success`` signals that the *current* step is done;
          the loop then advances to the next step and continues.
        - The stuck guard resets between steps (local-repair only, not global).
        - All :class:`CommandRecord` s accumulated across every step are merged
          into the single returned :class:`TaskReport`.

        :param recipe:          Ordered sequence of :class:`RecipeStep` s to execute.
        :param sandbox_execute: Callable ``(cmd) -> (ok, output)`` for shell execution.
        :param ledger:          Shared :class:`ActionLedger` for the current cycle.
        :param step_offset:     Step counter offset for ledger alignment.
        :returns:               :class:`TaskReport` with ``status='done'`` if all steps
                                complete, else ``status='blocked'`` with the first
                                failure reason in ``learning``.
        """
        n_steps = len(recipe.steps)

        # Early-exit: nothing to do for an empty recipe (avoids IndexError on
        # recipe.steps[current_step_idx] in the empty-response guard and stuck guard
        # below when the tuple is empty).
        if n_steps == 0:
            return TaskReport(
                task_goal="Recipe (0 steps)",
                status="done",
                commands=(),
                learning="Empty recipe — nothing to execute",
                completed_steps=0,
            )

        total_budget = recipe_budget(n_steps)
        all_commands: list[CommandRecord] = []

        # Seed the initial user message with the full numbered recipe so the LLM
        # always has the complete ordered plan in view.
        recipe_lines = "\n".join(
            f"{i + 1}. [{s.kind}] {s.command}"
            for i, s in enumerate(recipe.steps)
        )
        initial_user_content = (
            f"Execute the following recipe ({n_steps} step{'s' if n_steps != 1 else ''}):\n"
            f"{recipe_lines}\n\n"
            f"Start with step 1. "
            f"Emit 'Final Answer: Success' when the *current* step's command exits 0; "
            f"I will then prompt you to continue with the next step."
        )

        messages: list[dict] = [
            {"role": "system", "content": BUILD_AGENT_SYSTEM_PROMPT + BUILD_AGENT_RECIPE_PROMPT},
            {"role": "user", "content": initial_user_content},
        ]

        current_step_idx: int = 0
        step_history: list[CommandRecord] = []  # stuck guard resets per step
        env_revision: int = step_offset
        steps_executed: int = 0
        empty_responses: int = 0

        for _budget_iter in range(total_budget):
            text, usage, raw_response = complete_with_retry(
                self.client,
                self.model,
                messages,
                temperature=0,
                stop=["Observation:"],
            )
            if self.on_usage:
                self.on_usage(usage)
            log_llm_exchange(
                "build_agent_recipe", raw_response, parsed={"budget_iter": _budget_iter}
            )

            action = _extract_worker_action(text)
            finished = _is_worker_finished(text)

            if finished:
                # Current step done — advance to the next one.
                current_step_idx += 1
                step_history = []  # reset stuck guard for the incoming step
                if current_step_idx >= n_steps:
                    # All steps complete.
                    return TaskReport(
                        task_goal=f"Recipe ({n_steps} steps)",
                        status="done",
                        commands=tuple(all_commands),
                        learning=f"All {n_steps} recipe steps completed successfully",
                        completed_steps=n_steps,
                    )
                next_step = recipe.steps[current_step_idx]
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            f"Step {current_step_idx} of {n_steps} complete. "
                            f"Now execute step {current_step_idx + 1}: {next_step.command}"
                        ),
                    },
                ]
                empty_responses = 0
                continue

            # Guard: empty / unparseable response
            if not action.strip():
                empty_responses += 1
                if empty_responses >= MAX_EMPTY_RESPONSES:
                    step = recipe.steps[current_step_idx]
                    return TaskReport(
                        task_goal=f"Recipe ({n_steps} steps)",
                        status="blocked",
                        commands=tuple(all_commands),
                        learning=(
                            f"Recipe blocked at step {current_step_idx + 1}/{n_steps} "
                            f"(id={step.id}): LLM returned too many unparseable responses"
                        ),
                        completed_steps=current_step_idx,
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
            empty_responses = 0  # reset on real action

            # Pre-submission self-check: reject composition rule violations.
            # Heredoc bodies legitimately contain `;`, `&&`, `import `, `cat ` etc.;
            # composition rules govern SHELL command chaining, not heredoc text. Skip the
            # self-check for a terminated heredoc to avoid a false Rule 1/Rule 2 rejection.
            composition_error = (
                None if _is_terminated_heredoc(action)
                else validate_command_composition(action)
            )
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
            record = CommandRecord(cmd=action, rc=rc, output=_truncate_output(output))

            # Stuck guard checks per-step history (reset between steps).
            if _is_stuck(step_history, action, is_preflight):
                all_commands.append(record)
                step = recipe.steps[current_step_idx]
                return TaskReport(
                    task_goal=f"Recipe ({n_steps} steps)",
                    status="blocked",
                    commands=tuple(all_commands),
                    learning=(
                        f"Recipe blocked at step {current_step_idx + 1}/{n_steps} "
                        f"(id={step.id}): stuck guard fired on '{action}'"
                    ),
                    completed_steps=current_step_idx,
                )

            step_history.append(record)
            all_commands.append(record)
            steps_executed += 1

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
                if self.synthesizer and self.synthesizer.command_mutates_environment(action):
                    env_revision += 1

            observation_prefix = "ok" if success else "FAILED"
            messages = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"Observation: [{observation_prefix}]\n{_truncate_output(output)}",
                },
            ]

        # Budget exhausted before all steps completed.
        return TaskReport(
            task_goal=f"Recipe ({n_steps} steps)",
            status="blocked",
            commands=tuple(all_commands),
            learning=(
                f"Recipe ran out of total budget ({total_budget} actions for {n_steps} steps)"
            ),
            completed_steps=current_step_idx,
        )

    # ------------------------------------------------------------------
    # v3 typed-patch path (inv #6)
    # ------------------------------------------------------------------

    def propose(self, scope, exec_readonly, *, max_diag_turns: int = 4, rejection_errors=()):
        """v3 typed-patch path (inv #6): read-only ReAct -> one PatchProposal, or None."""
        from src.envstate.repair_scope import render_repair_scope
        from src.envstate.jsonutil import extract_json_object
        from python_deps.depgraph.patch import parse_patch_proposal, PatchParseError

        user = render_repair_scope(scope)
        if rejection_errors:
            user += ("\n\nYour previous patch was REJECTED by the gate:\n"
                     + "\n".join(f"- {e}" for e in rejection_errors)
                     + "\nFix these and re-emit ONE corrected fenced ```json patch.")
        messages = [
            {"role": "system", "content": V3_PROPOSE_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

        def _parse(text):
            obj = extract_json_object(text)
            if obj is None:
                return None, "no JSON object found"
            try:
                return parse_patch_proposal(obj), None
            except PatchParseError as exc:
                return None, "; ".join(exc.errors)

        retried = False
        for _turn in range(max_diag_turns + 1):
            text, usage, raw = complete_with_retry(
                self.client, self.model, messages, temperature=0, stop=["Observation:"])
            if self.on_usage:
                self.on_usage(usage)
            log_llm_exchange("build_agent_propose", raw, parsed={"turn": _turn})

            if "Final Patch" in text or extract_json_object(text) is not None:
                proposal, err = _parse(text)
                if proposal is not None and not proposal.is_empty():
                    return proposal
                if retried:
                    return None
                retried = True
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        f"That was not a valid PatchProposal ({err}). Re-emit EXACTLY one "
                        f"fenced ```json object matching the schema. No prose after it.")}]
                continue

            action = _extract_worker_action(text)
            if not action.strip():
                if retried:
                    return None
                retried = True
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content":
                        "Emit either 'Action: <read-only cmd>' or a Final Patch fenced ```json object."}]
                continue
            rc, out = exec_readonly(action)
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content":
                    f"Observation: [{'ok' if rc == 0 else 'FAILED'}]\n{_truncate_output(out)}"}]
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_task_message(self, task: Task) -> str:
        facts_text = "\n".join(f"- {f}" for f in task.facts) if task.facts else "- (none)"
        parts = [
            f"Task goal: {task.goal}",
            f"Done when: {task.done_when}",
            f"Layer: {task.layer}",
            f"Relevant facts:\n{facts_text}",
        ]
        if task.target_node_ids:
            parts.append("Target graph nodes:\n" + "\n".join(f"- {nid}" for nid in task.target_node_ids))
        if task.transition_proposal is not None:
            tp = task.transition_proposal
            cmds = ", ".join(tp.command_templates) if tp.command_templates else "(choose an appropriate command)"
            parts.append(
                f"Proposed transition: {tp.kind} -> {tp.target}\n"
                f"  intent: {tp.intent}\n  candidate commands: {cmds}"
            )
        return "\n".join(parts)

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
        elif success and self.synthesizer and self.synthesizer.command_mutates_environment(action):
            mutation_class = self.synthesizer.classify_mutation(action)
            rev_after = env_revision + 1
        else:
            mutation_class = None
            rev_after = env_revision

        from src.envstate.ledger import make_action_event

        event = make_action_event(
            step=step,
            cmd=action,
            success=success,
            stdout=_truncate_output(output),
            env_revision_before=env_revision,
            env_revision_after=rev_after,
            mutation_class=mutation_class,
            container_id=self.container_id,
        )
        ledger.append(event)


# Re-export for test convenience (tests import CommandRecord from here)
from src.envstate.world_model import CommandRecord as CommandRecord  # noqa: F401
