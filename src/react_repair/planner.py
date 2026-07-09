"""The react arm's LLM planner (spec §4). ReAct: reads the current script + latest failure +
compressed history, returns ONE move (explore|patch). The `graph_context` slot is empty for
the baseline and populated for the graph-guided variant — the ONLY difference between the two
(spec §14). No arm-C imports; uses the shared `complete_with_retry`."""
from __future__ import annotations

from typing import Any, Callable

from src.envstate.llm_response import complete_with_retry
from src.react_repair.actions import extract_thought, parse_action
from src.react_repair.history_view import render_history

SYSTEM_PROMPT = """\
You are configuring a Python repo's environment by editing ONE build script (setup.sh) until
it runs green and the repo's tests pass. Each turn you see the current script, what happened
when it last ran, and your history. Respond with a Thought and exactly ONE of:
  Action: <one read-only shell command>     (investigate; you get its output next turn)
  Script: followed by one fenced ```bash block with the COMPLETE new setup.sh
Rules: read-only commands only for Action (ls, cat, ldconfig, pip show, apt-cache — never install/modify).
The ONLY way to change the build is to emit a new Script. Do not claim success; the host runs the tests.
Integrity: set up the REAL environment — install genuine packages from the package index and add
the real system libraries/tools a build needs. Do NOT fake it: never create stub/dummy/placeholder
modules or packages to satisfy an import, never edit, delete, or skip the repo's tests, and never
inject pytest options to deselect failures. A failing test must be fixed by installing the real
dependency it needs; if a dependency genuinely cannot be installed, leave the test failing rather
than fabricate a way to pass it."""


class ReactPlanner:
    def __init__(self, client: Any, model: str,
                 graph_context: "Callable[[Any], str] | None" = None,
                 log=None):
        self.client = client
        self.model = model
        self.graph_context = graph_context
        self.log = log

    def _render(self, history, script: str, observation: str, graph) -> str:
        parts = [
            "CURRENT setup.sh:\n```bash\n" + (script or "") + "\n```",
            "LAST RUN OBSERVATION:\n" + (observation or ""),
            render_history(history.steps),
        ]
        if self.graph_context is not None:
            ctx = self.graph_context(graph) or ""
            if ctx.strip():
                parts.append("GRAPH CONTEXT (certified state):\n" + ctx)
        parts.append("Respond with Thought + one Action or Script.")
        return "\n\n".join(parts)

    def plan(self, history, script: str, observation: str, graph):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._render(history, script, observation, graph)},
        ]
        text, usage, _raw = complete_with_retry(self.client, self.model, messages,
                                                temperature=0, stop=["Observation:"])
        thought, action = extract_thought(text), parse_action(text)
        if self.log is not None:
            self.log.d("PLAN", f"thought={thought[:60]!r} action={action.kind}")
            self.log.trace("plan", observation=observation, prompt=messages, reply_raw=text,
                           thought=thought,
                           action={"kind": action.kind, "command": action.command,
                                   "new_script": action.new_script})
        return thought, action, usage
