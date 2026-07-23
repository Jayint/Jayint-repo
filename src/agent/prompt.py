"""How the react arm ASKS — the prompt cluster (spec §4A consolidation).

Holds the react LLM planner (ReactPlanner: reads current script + latest failure + compressed
history, returns ONE move; the `graph_context` slot is empty for the baseline and populated for the
graph-guided variant — the only difference between the two, spec §14) and, folded in from the former
repair_scope.py (§4A), the typed-patch arm's RepairScope + render_repair_scope, which renders the
graph-derived repair slice into the same prompt surface. Both build what the model is asked; one
file. No arm-C imports; uses the shared `complete_with_tools`."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from src.llm import complete_with_tools
from src.agent.actions import (
    TOOLS_SCHEMA, action_from_tool_call, extract_reasoning, extract_thought, parse_action)
from src.agent.history import render_history
from src.agent.history import build_messages

# ROLE + GOAL + READING A FAILURE + RULES are STATIC and ablation-invariant (identical for the
# no-graph and graph runs). The ENVIRONMENT section (injected per-run: base image / OS / repo dir /
# file tree) is the only part that varies — the graph variant additionally feeds certified state per-turn.
_ROLE_GOAL_RULES = """\
ROLE
You repair ONE build script, setup.sh, that provisions a repo's test environment. It re-runs from a
clean base image every turn — only edits to setup.sh persist. The seed already installs the repo's
declared package closure; treat it as near-complete, not a blank slate.

GOAL — fix the environment, in priority order:
  1. Build exits 0 (setup.sh runs clean).
  2. `pytest --collect-only` reports ZERO errors — every test module imports. Collection errors block
     whole test files, so clearing them is the highest-leverage fix: map each ModuleNotFoundError /
     missing extra / link error to the dependency to install.
  3. Maximize the pytest pass rate. (A few tests need a service or config env — prefer the repo's own
     test config and host-provided endpoints; don't spin up heavy services on a guess.)
The host runs the build and the suite and decides success — never claim it yourself.

READING A FAILURE
set -e stops at the first failing command; the error names it and its line, tagged ← BUILD HALTED
HERE in the script below. Lines above it already ran — don't reinstall them; lines below never ran.
A collection error names the missing import — fix its cause: a declared extra, a missing distribution,
or a system library the wheel needs.

RULES
  • Make the smallest evidence-backed edit. Evidence = the last run's error + the repo's own manifests
    and test config. Don't strip a working script to a stub.
  • Never fake a pass: no stub modules, no error-suppressing flags, and never shrink what pytest
    collects (no --ignore / -k / testpaths / conftest skips / editing or deleting test files). The
    host rejects collection-shrinking edits.
  • Install THIS repo from its local checkout: `pip install -e .` plus its declared extras — never
    from a package index (a published copy fakes a pass against code that isn't here).
  • Turns are counted (shown each turn). Once you can name the fix, make it — don't explore the
    budget away."""

_TOOLS = """\
TOOLS — reason briefly, then call EXACTLY ONE:
  explore(command)          read-only probe: cat, pip show, ldconfig, `pytest --collect-only`.
                            Result returns next turn; nothing you install here persists.
  edit(verb, start, end, content)   verb = replace | insert | delete. Line numbers match CURRENT
                            setup.sh (the same ones the build failure names)."""


def build_system_prompt(env_info: str = "") -> str:
    """Assemble the full system prompt: static ROLE/GOAL/RULES, the per-run ENVIRONMENT facts, then
    the TOOLS. `env_info` is the injected environment block (base image/OS/repo dir/file tree);
    empty → a placeholder so the prompt is still well-formed."""
    env = (env_info or "").rstrip() or "  (environment details unavailable this run)"
    return f"{_ROLE_GOAL_RULES}\n\nENVIRONMENT (this run)\n{env}\n\n{_TOOLS}"


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
                result=None, causes=None, prev_states=None,
                env_state: "str | None" = None) -> str:
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
        if env_state:
            parts.append(env_state)
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
                  rejected=None, result=None, causes=None, prev_states=None, env_state=None):
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
                rejection=rejection, rejected=rejected, env_state_text=env_state)
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user",
             "content": self._render(history, script, observation, graph, fail_lineno,
                                     turn, max_turns, rejection, result, causes, prev_states,
                                     env_state)},
        ]

    def plan(self, history, script: str, observation: str, graph, fail_lineno: int | None = None,
             turn: int | None = None, max_turns: int | None = None, rejection: str | None = None,
             rejected: "dict | None" = None,
             result=None, causes=None, prev_states=None,
             env_state: "str | None" = None):
        messages = self._messages(history, script, observation, graph, fail_lineno,
                                  turn, max_turns, rejection, rejected,
                                  result, causes, prev_states, env_state)
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


# ======================================================================================
# repair_scope — folded from repair_scope.py (§4A): the typed-patch repair slice renderer.
#
# The §9 RepairScope packet (2b §6.1): curated, structured context for the v3 typed-patch
# agent. Pure: no Docker/network/LLM. Re-derives the Phase-1 RequirementSlice; never raw history.
# ======================================================================================

PATCH_SCHEMA_HINT = """\
Respond with EXACTLY ONE fenced JSON object and nothing after it:
```json
{
  "rationale": {"why": "<one sentence>"},
  "patch": {
    "add_requirements": [{"id": "syslib:<name>", "type": "SystemLib", "name": "<name>",
       "layer": "system", "check_command": "<read-only check>", "evidence_ref": "<ev.id>"}],
    "add_providers": [{"id": "apt:<pkg>", "kind": "apt",
       "command": "apt-get install -y <pkg>", "provides": ["syslib:<name>"], "override": false}],
    "add_edges": [{"source": "<id>", "target": "<id>", "relation": "requires", "hard": true}],
    "script_patches": [{"block_id": "<layer>.<short>", "wave": "system",
       "commands": ["<install>"], "target_node_ids": ["<node id>"],
       "checks": ["<read-only check>"], "provides": ["<id>"], "evidence_ref": "<ev.id>"}]
  }
}
```
Rules: canonical ids (syslib:/pkg:/tool:/service:/config:). check_command MUST be read-only.
Cite an evidence_ref present in the evidence below. Set "override": true to replace a known-bad provider."""


@dataclass(frozen=True)
class RepairScope:
    target_node_id: str | None
    failed_command: str | None
    failed_output: str
    slice_lines: tuple[str, ...]
    known_invalid: tuple[str, ...]
    constraints: tuple[tuple[str, str], ...]
    known_evidence_ids: frozenset[str]


def build_repair_scope(graph, *, target_node_id, failed_block, bundle,
                       known_invalid=(), constraints=None):
    cons = tuple(sorted((str(k), str(v)) for k, v in dict(constraints or {}).items()))
    # Graph context (the requirement slice) is intentionally OMITTED from the repair
    # prompt: the C0/C1 graph-repair ablation showed it does not improve the agent's
    # localization (and can mislead on the graph's own over-predictions). The dependency
    # graph stays the patch OUTPUT target + the gate/replay rail — it is dropped only as
    # agent-facing reasoning CONTEXT. `graph` is kept in the signature (the repair_loop
    # scope_builder contract) and the `slice_lines` field + render branch are retained so
    # this can be re-enabled — or replaced by a territory/state graph — by populating
    # slice_lines here.
    slice_lines = ()
    failed_cmd = failed_block.commands[-1] if (failed_block and failed_block.commands) else None
    failed_out = ""
    # bundle may be None on the binding-install path when there is no install-command failure
    # (e.g. install rc 0 but a reciped check still MISSING). On an install failure the binding
    # path supplies a single-item EvidenceBundle built from the InstallResult (see
    # orchestrator._build_install_evidence). Treat a missing bundle as empty evidence rather
    # than crashing on bundle.items.
    _evidence = bundle.items if bundle is not None else ()
    for ev in _evidence:
        if ev.rc != 0 and (failed_block is None or ev.block_id == failed_block.block_id):
            failed_cmd = failed_cmd or ev.command
            failed_out = ev.output_excerpt or ""
    return RepairScope(
        target_node_id=target_node_id, failed_command=failed_cmd, failed_output=failed_out,
        slice_lines=slice_lines, known_invalid=tuple(known_invalid), constraints=cons,
        known_evidence_ids=frozenset(ev.evidence_id for ev in _evidence))


def render_repair_scope(scope: RepairScope) -> str:
    parts = []
    if scope.target_node_id:
        parts.append(f"Failing obligation: {scope.target_node_id}")
    if scope.slice_lines:
        parts.append("Graph context:\n" + "\n".join(scope.slice_lines))
    if scope.failed_command:
        parts.append(f"Failed command: {scope.failed_command}")
    if scope.failed_output:
        parts.append("Failure output:\n" + scope.failed_output)
    if scope.known_invalid:
        parts.append("DO NOT propose these (already failed): " + ", ".join(scope.known_invalid))
    if scope.constraints:
        parts.append("Constraints: " + ", ".join(f"{k}={v}" for k, v in scope.constraints))
    parts.append("Cite evidence by id (available: "
                 + ", ".join(sorted(scope.known_evidence_ids)) + ").")
    parts.append(PATCH_SCHEMA_HINT)
    return "\n\n".join(parts)
