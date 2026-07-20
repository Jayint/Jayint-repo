"""V3 build agent — the proposal plane (the LLM's one verb).

Extracted from build_agent.py to sever the v1 ReAct loop's heavy coupling
(synthesizer, world_model, ledger). The v3 path uses ONLY ``propose``: a
read-only ReAct loop that emits exactly one typed ``PatchProposal`` (or None).

The agent has proposal power, not write/truth/done power: its output is gated
by PatchGate, rendered into a graph-attached block, executed by the host, and
certified by a deterministic host check before it can affect node state.
"""
from __future__ import annotations

import os
import re
import json
from typing import Any, Callable

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.llm_response import complete_with_retry

# --- output truncation (keep head tracebacks AND tail pytest summary) ---------
_OUTPUT_HEAD, _OUTPUT_TAIL = 1500, 800
_OUTPUT_LIMIT = _OUTPUT_HEAD + _OUTPUT_TAIL


def _truncate_output(output: str) -> str:
    """Keep the head (tracebacks/setup) AND the tail (pytest pass summary)."""
    if len(output) <= _OUTPUT_LIMIT:
        return output
    return (
        output[:_OUTPUT_HEAD].rstrip()
        + "\n...[output truncated]...\n"
        + output[-_OUTPUT_TAIL:].lstrip()
    )


# --- action extraction (plain / fenced / tool-call, with heredoc recovery) ----
_ACTION_RE = re.compile(r"^\s*Action:\s*(.+?)\s*$", re.MULTILINE)
_ACTION_FENCED_RE = re.compile(
    r"^\s*Action:\s*```[a-zA-Z]*\n(.*?)```", re.MULTILINE | re.DOTALL
)
_TOOLCALL_CMD_RE = re.compile(
    r'<parameter\s+name="command"\s*>(.*?)</parameter>', re.DOTALL
)
# A heredoc operator (`<< DELIM` / `<<-DELIM`, optionally quoted). The leading-letter
# anchor on the delimiter means arithmetic shifts ($((1 << 4))) never match.
_RE_HEREDOC_OP = re.compile(r"<<-?\s*['\"]?[A-Za-z_]\w*")


def _is_terminated_heredoc(body: str) -> bool:
    """True when *body* is a MULTI-LINE command that opens a heredoc and carries a
    body+terminator (i.e. contains a newline after a ``<< DELIM`` operator)."""
    return bool(body) and "\n" in body and bool(_RE_HEREDOC_OP.search(body))


def _reconstruct_plain_heredoc(content: str, action_start: int) -> str:
    """Rebuild a full plain (unfenced) heredoc command from the raw LLM *content*.

    ``_ACTION_RE`` is line-bounded (no DOTALL), so for an ``Action: cat > f << 'EOF'``
    followed by a body+terminator it captures only the opener line. This recovers the
    body and terminator from *content*, cutting at the matching delimiter line so
    trailing Thought/Observation text is never swallowed."""
    tail = re.sub(r"^\s*Action:[ \t]*", "", content[action_start:])
    op = _RE_HEREDOC_OP.search(tail.splitlines()[0] if tail else "")
    if not op:
        return tail.splitlines()[0] if tail else ""
    delim_match = re.search(r"([A-Za-z_]\w*)\s*$", op.group(0))
    if not delim_match:
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
        return lines[0]
    return "\n".join(kept)


def _extract_worker_action(content: str) -> str:
    """Extract the Action line from LLM content.

    Handles three formats: plain ``Action: <cmd>``; fenced ``Action: ```lang\\n…```;
    and XML tool-call ``<command>…</command>``."""
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


V3_PROPOSE_SYSTEM_PROMPT = """\
You are diagnosing ONE failing environment obligation inside a Docker container.
You may run READ-ONLY diagnostics (pkg-config, apt-cache, ldconfig, pip show, ls, cat).
You may NOT install/modify/delete — the host applies your fix, not you.
Each turn respond with ONE of:
  Action: <one read-only shell command>      (you will get an Observation:)
  Final Patch: followed by exactly one fenced ```json PatchProposal object
The patch is the ONLY accepted output — never claim success in prose."""


GRAPH_EXECUTE_SYSTEM_PROMPT = """\
You are the diagnosis component of a graph-guided environment Execute Agent.
The host is executing a structured Build Plan compiled from a DepGraph. You see
ONE failed execution packet: its graph obligation slice, block commands,
providers, checks, evidence, and failure output.

Use the DepGraph to reason about WHAT must become true and the failed block to
reason about HOW the current plan tried to make it true. Prefer the smallest
causal patch that repairs this block or adds a missing prerequisite. The host
may restore a verified prefix checkpoint and re-execute only the invalidated
suffix after your patch.

There must be one active repair per obligation. For a graph-derived failed block,
replace its provider with override=true. For a failed governed script block, use
op="replace_block" and the exact failed block id. Never append fallback blocks
behind a command that already failed: execution cannot reach them.

Be concise: do not restate the packet, enumerate rejected alternatives, or
narrate internal deliberation. Use one short diagnostic when evidence is
insufficient; otherwise emit the typed patch immediately. For Python-2 renamed
stdlib imports such as Queue or urlparse in a Python-3 target, prefer a governed
Python-3 compatibility shim or source-adaptation block targeting the import
obligation. Do not install a Python-2 runtime solely to recreate an old import
name.

You may request open-ended READ-ONLY diagnostics. The Host validates every probe
before execution. You may NOT choose checkpoints, create/commit/abort containers,
execute Build Blocks, certify nodes, or claim completion.

Return exactly ONE JSON object per turn and no prose. Its type must be one of:
  probe         - request one read-only diagnostic command;
  propose_patch - submit the complete typed PatchProposal patch object;
  abstain       - suggest non_environment, subject to independent Host review.
PatchGate, CandidateTransaction validation, and deterministic Host checks alone
decide whether a patch is admitted, committed, and satisfied."""


class V3BuildAgent:
    """Proposal-plane agent: a read-only ReAct loop that yields ONE typed patch.

    Injected dependencies (testable without Docker):
      client    — OpenAI-compatible LLM client
      model     — model slug (str)
      on_usage  — optional callback called with the usage dict after each LLM call
    """

    system_prompt = V3_PROPOSE_SYSTEM_PROMPT

    def __init__(
        self,
        client: Any,
        model: str,
        *,
        on_usage: Callable[[dict], None] | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.on_usage = on_usage

    def propose(
        self,
        scope,
        exec_readonly,
        *,
        max_diag_turns: int = 30,
        rejection_errors=(),
        action_observer=None,
    ):
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
            {"role": "system", "content": self.system_prompt},
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
                self.client,
                self.model,
                messages,
                temperature=0,
                stop=["Observation:"],
                max_tokens=int(os.getenv("GRAPH_AGENT_MAX_OUTPUT_TOKENS", "2200")),
            )
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


class GraphExecuteAgent(V3BuildAgent):
    """DepGraph/Build-Plan-aware proposal agent used by the incremental arm."""

    system_prompt = GRAPH_EXECUTE_SYSTEM_PROMPT

    def propose(
        self,
        scope,
        exec_readonly,
        *,
        max_diag_turns: int = 4,
        rejection_errors=(),
        action_observer=None,
    ):
        """Structured JSON action loop for the canonical incremental arm."""
        from src.envstate.agent_action import (
            AbstainAction,
            AgentActionParseError,
            ProbeAction,
            ProposePatchAction,
            parse_agent_action,
            validate_probe_command,
        )
        from src.envstate.jsonutil import extract_json_object
        from src.envstate.repair_scope import render_repair_scope

        user = render_repair_scope(scope, structured_actions=True)
        if rejection_errors:
            user += (
                "\n\nThe Host rejected the previous action:\n"
                + "\n".join(f"- {error}" for error in rejection_errors)
                + "\nReturn one corrected JSON action."
            )
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user},
        ]

        def observe(**event):
            if action_observer is not None:
                action_observer(event)

        if rejection_errors:
            observe(
                action_type="propose_patch_rejected",
                target_node=getattr(scope, "target_node_id", None),
                validated=False,
                rejection="; ".join(str(error) for error in rejection_errors),
            )

        for turn in range(max_diag_turns + 1):
            text, usage, raw = complete_with_retry(
                self.client,
                self.model,
                messages,
                temperature=0,
                max_tokens=int(os.getenv("GRAPH_AGENT_MAX_OUTPUT_TOKENS", "2200")),
            )
            if self.on_usage:
                self.on_usage(usage)
            obj = extract_json_object(text)
            try:
                if obj is None:
                    raise AgentActionParseError(("no JSON action object found",))
                action = parse_agent_action(obj)
            except AgentActionParseError as exc:
                rejection = "; ".join(exc.errors)
                observe(
                    action_type="malformed",
                    target_node=getattr(scope, "target_node_id", None),
                    validated=False,
                    rejection=rejection,
                )
                log_llm_exchange(
                    "graph_execute_agent_action", raw,
                    parsed={"turn": turn, "type": "malformed", "errors": exc.errors},
                )
                messages.extend([
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": json.dumps({
                        "type": "action_rejected",
                        "errors": list(exc.errors),
                        "retry": True,
                    })},
                ])
                continue

            target = getattr(action, "target_node", None)
            expected_target = getattr(scope, "target_node_id", None)
            if target is not None and expected_target and target != expected_target:
                rejection = (
                    f"target_node must match the failing obligation {expected_target!r}"
                )
                observe(
                    action_type=action.type,
                    target_node=target,
                    validated=False,
                    rejection=rejection,
                )
                messages.extend([
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": json.dumps({
                        "type": "action_rejected",
                        "errors": [rejection],
                        "retry": True,
                    })},
                ])
                continue

            if isinstance(action, ProbeAction):
                validation = validate_probe_command(action.command)
                if not validation.allowed:
                    rejection = "; ".join(validation.errors)
                    observe(
                        action_type="probe",
                        target_node=action.target_node,
                        probe_command=action.command,
                        validated=False,
                        rejection=rejection,
                    )
                    log_llm_exchange(
                        "graph_execute_agent_action", raw,
                        parsed={
                            "turn": turn,
                            "type": "probe",
                            "validated": False,
                            "errors": validation.errors,
                        },
                    )
                    messages.extend([
                        {"role": "assistant", "content": text},
                        {"role": "user", "content": json.dumps({
                            "type": "probe_rejected",
                            "command": action.command,
                            "errors": list(validation.errors),
                            "executed": False,
                        })},
                    ])
                    continue
                rc, output = exec_readonly(action.command)
                excerpt = _truncate_output(output or "")
                observe(
                    action_type="probe",
                    target_node=action.target_node,
                    probe_command=action.command,
                    validated=True,
                    evidence=excerpt,
                )
                log_llm_exchange(
                    "graph_execute_agent_action", raw,
                    parsed={"turn": turn, "type": "probe", "validated": True, "rc": rc},
                )
                messages.extend([
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": json.dumps({
                        "type": "probe_observation",
                        "command": action.command,
                        "rc": rc,
                        "output": excerpt,
                    })},
                ])
                continue

            if isinstance(action, ProposePatchAction):
                observe(
                    action_type="propose_patch",
                    target_node=action.target_node,
                    validated=True,
                )
                log_llm_exchange(
                    "graph_execute_agent_action", raw,
                    parsed={"turn": turn, "type": "propose_patch"},
                )
                return action.proposal

            assert isinstance(action, AbstainAction)
            observe(
                action_type="abstain",
                target_node=getattr(scope, "target_node_id", None),
                validated=None,
                evidence=", ".join(action.evidence_refs),
                classification=action.classification,
            )
            log_llm_exchange(
                "graph_execute_agent_action", raw,
                parsed={"turn": turn, "type": "abstain"},
            )
            return action
        return None
