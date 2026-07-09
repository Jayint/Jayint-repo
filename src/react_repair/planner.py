"""The react arm's LLM planner (spec §4). ReAct: reads the current script + latest failure +
compressed history, returns ONE move (explore|patch). The `graph_context` slot is empty for
the baseline and populated for the graph-guided variant — the ONLY difference between the two
(spec §14). No arm-C imports; uses the shared `complete_with_retry`."""
from __future__ import annotations

from typing import Any, Callable

from src.envstate.llm_response import complete_with_tools
from src.react_repair.actions import (
    TOOLS_SCHEMA, action_from_tool_call, extract_reasoning, extract_thought, parse_action)
from src.react_repair.history_view import render_history

# GOAL + APPROACH + INTEGRITY are STATIC and ablation-invariant (identical for the no-graph and
# graph runs). The ENVIRONMENT section (injected per-run: base image / OS / repo dir / file tree)
# is the only part that varies — the graph variant additionally feeds certified state per-turn.
_GOAL_APPROACH_INTEGRITY = """\
GOAL
You are reproducing a Python repository's test environment by editing ONE build script (setup.sh),
re-run from a clean base image each turn. Set up what the repo's tests need to run:
  · the language runtime and the system libraries a build or link step needs
  · the Python packages, including test/dev dependencies
  · the services and configuration the tests require — prefer host-provided endpoints and the repo's
    own test config when present; start a service inside setup.sh only if the repo shows the tests
    expect a local one, and never spin up a heavyweight service on a guess
You are DONE when setup.sh runs with no error AND the test suite passes — the host runs both and
decides success; never claim it yourself.

APPROACH
The current setup.sh already installs the package closure resolved from the repo's own declarations —
treat it as a near-complete starting point, not a blank slate. The remaining failure is usually a
missing system library, a wrong install method, an unset config/env var, or a service — so read the
last run's error and the ENVIRONMENT block first; they usually name it.
Make the smallest change the evidence supports — the last run's output and the repo's own declared
setup (its manifests and test config). Preserve the existing script unless the evidence shows a line
is wrong; don't strip a working setup.sh to a stub, and don't add packages or services you can't tie
to evidence.

INTEGRITY
Set the environment up for real. Don't fake a pass — no stub/placeholder modules, no editing,
skipping, or deselecting tests, no error-suppressing flags. If a dependency or service genuinely
cannot be provided, leave the test failing rather than fabricate a way to pass."""

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


def _numbered(script: str) -> str:
    """The current setup.sh with a 1-based line-number gutter, aligned with the ERR-trap `lineno`
    the build failure reports — so `Edit: replace 40` targets exactly the line named as failed."""
    lines = (script or "").splitlines()
    if not lines:
        return "(empty)"
    w = len(str(len(lines)))
    return "\n".join(f"{i:>{w}}| {ln}" for i, ln in enumerate(lines, 1))


class ReactPlanner:
    def __init__(self, client: Any, model: str,
                 graph_context: "Callable[[Any], str] | None" = None,
                 log=None, env_info: str = ""):
        self.client = client
        self.model = model
        self.graph_context = graph_context
        self.log = log
        # Built ONCE per run — the ENVIRONMENT facts (base image/OS/repo dir/file tree) are
        # per-run constant, so the system message is stable across turns.
        self.system_prompt = build_system_prompt(env_info)

    def _render(self, history, script: str, observation: str, graph) -> str:
        parts = [
            ("CURRENT setup.sh (line numbers are for Edit refs and match the build failure's "
             "\"line N\" — the \"n| \" prefix is NOT part of the script):\n" + _numbered(script)),
            "LAST RUN OBSERVATION:\n" + (observation or ""),
            render_history(history.steps),
        ]
        if self.graph_context is not None:
            ctx = self.graph_context(graph) or ""
            if ctx.strip():
                parts.append("GRAPH CONTEXT (certified state):\n" + ctx)
        parts.append("Reason briefly, then call one tool — explore or edit.")
        return "\n\n".join(parts)

    def plan(self, history, script: str, observation: str, graph):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self._render(history, script, observation, graph)},
        ]
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
