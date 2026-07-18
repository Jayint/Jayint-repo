"""The react arm's LLM planner (spec §4). ReAct: reads the current script + latest failure +
compressed history, returns ONE move (explore|patch). The `graph_context` slot is empty for
the baseline and populated for the graph-guided variant — the ONLY difference between the two
(spec §14). No arm-C imports; uses the shared `complete_with_retry`."""
from __future__ import annotations

import os
from typing import Any, Callable

from src.llm import complete_with_tools
from src.agent.actions import (
    TOOLS_SCHEMA, action_from_tool_call, extract_reasoning, extract_thought, parse_action)
from src.agent.history_view import render_history
from src.agent.message_view import build_messages

# GOAL + APPROACH + INTEGRITY are STATIC and ablation-invariant (identical for the no-graph and
# graph runs). The ENVIRONMENT section (injected per-run: base image / OS / repo dir / file tree)
# is the only part that varies — the graph variant additionally feeds certified state per-turn.
_GOAL_APPROACH_INTEGRITY = """\
GOAL
You are reproducing a repository's test environment by editing ONE build script (setup.sh), re-run
from a clean base image each turn. Set up what the repo's tests need to run: the language runtime and
the system libraries a build or link step needs, the project's packages (including test/dev
dependencies), and the services and configuration the tests require. For services and config, prefer
host-provided endpoints and the repo's own test config when present; start a service inside setup.sh
only if the repo shows the tests expect a local one, and never spin up a heavyweight service on a guess.
You are DONE when setup.sh runs with no error AND the test suite passes — the host runs both and
decides success; never claim it yourself.

APPROACH
The current setup.sh already installs the package closure resolved from the repo's own declarations —
treat it as a near-complete starting point, not a blank slate. The remaining failure is usually a
missing system library, a wrong install method, an unset config/env var, or a service; the last run's
error and the ENVIRONMENT block usually name it, so read them first. Repair is an EDIT loop: keep
editing setup.sh until the build is clean and the tests pass. The edit is the only thing that carries
over — setup.sh re-runs from a clean base each turn, so a package you install inside an explore is
gone next turn; put the fix in setup.sh with edit(). Explore only when you genuinely can't yet name
the change; once the error and the files show you the fix, stop exploring and make it. Exploring turn
after turn with no edit is a failure, not diligence — you have a limited, counted number of turns
(shown each turn), so as the budget runs low commit your best edit rather than investigate.
The build halts at the first failing command (set -e): the error names that command and its line,
tagged ← BUILD HALTED HERE in the script below. Everything above that line already succeeded — don't
re-install it; everything below never ran, so don't touch a line the failure hasn't reached yet. Make
the smallest change the evidence supports — the last run's output and the repo's own declared setup
(its manifests and test config) — and preserve the existing script unless the evidence shows a line is
wrong; don't strip a working setup.sh to a stub, and don't add packages or services you can't tie to
evidence.

INTEGRITY
Set the environment up for real. Don't fake a pass — no stub/placeholder modules, no error-suppressing
flags, and never shrink what the test suite COLLECTS. Concretely: don't edit, skip, delete, or move
test files, and don't add a pytest.ini / pyproject / setup.cfg / tox.ini / conftest that narrows
collection (testpaths, --ignore, --ignore-glob, --deselect, -k, norecursedirs, collect_ignore).
Excluding a test you can't get to run is still deselecting it — leave it failing. The host enforces
this: an edit that reduces the collected test set is rejected, so it can't help you — fix the real
cause instead. Install THIS repo's own package from the local checkout (`pip install -e .`, plus its
declared extras), never from a package index — a published copy shadows the source under test, so the
suite would pass against code that isn't in this repo. That is a fake pass, and the host rejects it.
If a dependency or service genuinely cannot be provided, leave the test failing rather
than fabricate a way to pass."""

_TOOLS = """\
TOOLS — each turn, reason briefly, then call EXACTLY ONE tool:
  explore — investigate, read-only.
    When: the error and the ENVIRONMENT block don't already tell you what you need — the body of a
          specific config file, WHERE a local package lives, or a runtime probe (ldconfig / pip show).
          Don't explore to re-derive the package closure — the seed already encodes it.
    Call:  explore(command=<one read-only shell command>)   (ls, cat, find, pip show, ldconfig —
           never install or modify). Its output comes back to you next turn.
  edit    — change setup.sh by line number. This is how you repair the build.
    When: you know the change to make — add a missing dependency, repin a version, or remove/
          replace the line the failure points to. Don't keep exploring once you can act.
    Call:  edit(verb, start, end, content). verb = replace | insert | delete; start/end are the
           line numbers shown in CURRENT setup.sh (the same ones the build failure names); content
           is the shell line(s) to add for replace/insert (omit for delete)."""


def build_system_prompt(env_info: str = "") -> str:
    """Assemble the full system prompt: static GOAL/APPROACH/INTEGRITY, the per-run ENVIRONMENT
    facts, then the TOOLS. `env_info` is the injected environment block (base image/OS/repo dir/
    file tree); empty → a placeholder so the prompt is still well-formed."""
    env = (env_info or "").rstrip() or "  (environment details unavailable this run)"
    return f"{_GOAL_APPROACH_INTEGRITY}\n\nENVIRONMENT (this run)\n{env}\n\n{_TOOLS}"


SYSTEM_PROMPT = build_system_prompt()          # env-less form (no facts gathered / tests)

# When this few turns remain, the closing line switches from a neutral counter to an urgency nudge —
# a visible countdown pushes a paralysis-prone agent to commit rather than explore the budget away.
_LOW_BUDGET_TURNS = int(os.getenv("REACT_LOW_BUDGET_TURNS", "5"))


def _closing_line(turn: "int | None", max_turns: "int | None") -> str:
    """The last thing the agent reads before it acts (recency spot). Augmented with the live turn
    budget so the finiteness is salient; escalates to an edit-now nudge once the budget is low. With
    no turn info it degrades to the plain instruction (backward compatible)."""
    if not turn or not max_turns:
        return "Reason briefly, then call one tool — explore or edit."
    left = max_turns - turn
    if left <= _LOW_BUDGET_TURNS:
        return (f"Turn {turn}/{max_turns} — the turn budget is almost gone. You have enough: commit "
                f"your best edit now, don't spend the rest exploring. Then call one tool.")
    return f"Turn {turn}/{max_turns} ({left} left). Reason briefly, then call one tool — explore or edit."

# Tag appended to the gutter line the build halted on, so the ERR-trap localization is visible
# WHERE the agent edits (the numbered script) — not only buried in the observation header. Matches
# the "← BUILD HALTED HERE" reference in the APPROACH prompt so the marker is self-explaining.
_HALT_MARKER = "  ← BUILD HALTED HERE (set -e stopped the script)"


def _numbered(script: str, fail_lineno: int | None = None) -> str:
    """The current setup.sh with a 1-based line-number gutter, aligned with the ERR-trap `lineno`
    the build failure reports — so `Edit: replace 40` targets exactly the line named as failed.
    When `fail_lineno` names the line the build halted on, that line is tagged with `_HALT_MARKER`
    so the localization is anchored in the script the agent edits. An out-of-range/None line is
    simply left untagged (no crash), so a stale or missing lineno degrades gracefully."""
    lines = (script or "").splitlines()
    if not lines:
        return "(empty)"
    w = len(str(len(lines)))
    rows = []
    for i, ln in enumerate(lines, 1):
        row = f"{i:>{w}}| {ln}"
        if fail_lineno is not None and i == fail_lineno:
            row += _HALT_MARKER
        rows.append(row)
    return "\n".join(rows)


class ReactPlanner:
    def __init__(self, client: Any, model: str,
                 graph_context: "Callable[[Any, Any, Any, Any], str] | None" = None,
                 log=None, env_info: str = ""):
        self.client = client
        self.model = model
        self.graph_context = graph_context
        self.log = log
        # Built ONCE per run — the ENVIRONMENT facts (base image/OS/repo dir/file tree) are
        # per-run constant, so the system message is stable across turns.
        self.system_prompt = build_system_prompt(env_info)

    def _render(self, history, script: str, observation: str, graph,
                fail_lineno: int | None = None,
                turn: int | None = None, max_turns: int | None = None,
                rejection: str | None = None,
                result=None, causes=None, prev_states=None) -> str:
        parts = [
            ("CURRENT setup.sh (line numbers are for Edit refs and match the build failure's "
             "\"line N\" — the \"n| \" prefix is NOT part of the script):\n"
             + _numbered(script, fail_lineno)),
            "LAST RUN OBSERVATION:\n" + (observation or ""),
            render_history(history.steps),
        ]
        # Reuse _graph_text — do NOT re-implement the call here. Before this change `_render`
        # called self.graph_context(graph) directly, which is why widening _graph_text alone
        # would have silently left the blob prompt style on the old 1-arg seam.
        ctx = self._graph_text(graph, result, causes, prev_states)
        if ctx:
            parts.append("GRAPH CONTEXT (certified state):\n" + ctx)
        if rejection:                          # same-turn retry after a tool misuse (high salience)
            parts.append("YOUR LAST TOOL CALL WAS REJECTED — fix it and try again: " + rejection)
        parts.append(_closing_line(turn, max_turns))
        return "\n\n".join(parts)

    def _graph_text(self, graph, result=None, causes=None, prev_states=None) -> "str | None":
        """The certified-state block for the graph variant (None for the baseline / empty context)."""
        if self.graph_context is None:
            return None
        ctx = self.graph_context(graph, result, causes or [], prev_states or {}) or ""
        return ctx if ctx.strip() else None

    def _messages(self, history, script, observation, graph, fail_lineno, turn, max_turns, rejection,
                  rejected=None, result=None, causes=None, prev_states=None):
        """The LLM message list for the active prompt style (lever REACT_PROMPT_STYLE, read per-call
        so a VM run can flip it via env). `messages` (DEFAULT): the growing assistant/user conversation
        (message_view) — the model's own thought+action as real turns, the current observation as the
        last user message, recency-tiered detail. `blob` (opt-in): stable system + ONE re-rendered user
        blob (render_history). The graph axis stays a single field in both, so the ablation is clean.

        `rejected` (the structured call the host refused + why) is used only by the agentic render,
        which puts the refusal in the rejected call's own tool-result slot; the blob path keeps the
        plain-string `rejection` footer."""
        if os.getenv("REACT_PROMPT_STYLE", "messages").lower() != "blob":
            return build_messages(
                history.steps, system_prompt=self.system_prompt,
                numbered_script=_numbered(script, fail_lineno),
                closing_line=_closing_line(turn, max_turns),
                graph_context_text=self._graph_text(graph, result, causes, prev_states),
                rejection=rejection, rejected=rejected)
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user",
             "content": self._render(history, script, observation, graph, fail_lineno,
                                     turn, max_turns, rejection, result, causes, prev_states)},
        ]

    def plan(self, history, script: str, observation: str, graph, fail_lineno: int | None = None,
             turn: int | None = None, max_turns: int | None = None, rejection: str | None = None,
             rejected: "dict | None" = None,
             result=None, causes=None, prev_states=None):
        messages = self._messages(history, script, observation, graph, fail_lineno,
                                  turn, max_turns, rejection, rejected,
                                  result, causes, prev_states)
        # Native tool-calling is PRIMARY: tool_choice="required" forces exactly one explore/edit
        # call, and structured JSON args mean no markdown/backtick/`Action:`-label drift. The text
        # path (parse_action on the message content) is a FALLBACK for a provider/turn that returns
        # no tool call, so the arm still works model-agnostically.
        calls, content, usage, _raw = complete_with_tools(
            self.client, self.model, messages, tools=TOOLS_SCHEMA, tool_choice="required", temperature=0)
        if calls:
            name, args = calls[0]
            action = action_from_tool_call(name, args)
            thought = extract_reasoning(content)
        else:
            action = parse_action(content)
            thought = extract_thought(content)
        if self.log is not None:
            self.log.d("PLAN", f"thought={thought[:60]!r} action={action.kind}")
            e = action.edit
            self.log.trace("plan", observation=observation, prompt=messages, reply_raw=content,
                           tool_calls=[{"name": n, "arguments": a} for n, a in calls],
                           thought=thought,
                           action={"kind": action.kind, "command": action.command,
                                   "new_script": action.new_script,
                                   "edit": ({"verb": e.verb, "start": e.start, "end": e.end,
                                             "content": e.content} if e else None)})
        return thought, action, usage
