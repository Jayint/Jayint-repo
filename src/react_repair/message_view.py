"""Message-list prompt builder for the react arm (lever: REACT_PROMPT_STYLE=messages).

The default `blob` style (planner._render) sends TWO messages per turn — a stable system prompt and
ONE re-rendered user blob whose "history" is a third-person reconstruction (render_history). This
module is the alternative SWE-agent/mini-swe-agent shape: a GROWING message list where the model's
own thought+action are real ``assistant`` turns and each observation is a real ``user`` turn.

Two things fall out of that shape, and they are the point of the experiment:
  - **Whose words.** The assistant turns carry the model's real reasoning + the action it took,
    not a narrator's paraphrase.
  - **The current observation is just the last user message.** There is no dedicated
    ``LAST RUN OBSERVATION`` slot duplicating a history card, so the withhold/dedup machinery in
    render_history has nothing to do here — it simply doesn't exist in this path.

Reset-each-turn caveat: unlike SWE-agent (persistent repo), every observation here is the WHOLE
script re-run from a clean base, so a ``user`` observation is "what running the current script does
now", not the incremental result of just the preceding edit. The transcript still reads coherently
(edit → whole-script result); the system prompt already states the reset semantics.

This is a render layer over the same flat Step list — arm-independent, like render_history. The
signature control flow (do-not-retry ledger) is preserved and mirrors history_view's transitions.
"""
from __future__ import annotations

import json
import os
import re

from src.react_repair.envelope import edit_result, run_envelope, strip_legacy_header
from src.react_repair.history_view import (
    _EXPLORE_FULL_CAP, _OBS_COMPRESS_CAP, _PAT_BASE, _PAT_EXPLORE, _PAT_MUTATION,
    _STUCK_MODE, _STUCK_THRESHOLD, _specific, extract_blocker)
from src.react_repair.history import safety_truncate
from src.react_repair.observation import safety_compress_observation, strip_pip_progress
from src.react_repair.pytest_blocks import compact_pytest_blocks
from src.react_repair.style import agentic

# Render style (lever, read per-call so a VM run can flip it via env).
#
#   classic (DEFAULT) — what shipped: observations open with the synthesized `BUILD OK. TESTS p/e
#                       passed.` verdict, the workbench is glued bare onto the last user turn, a
#                       rejection is a footer at the bottom of it.
#   agentic  (opt-in) — the transcript a tool-using model is post-trained on. Every observation is
#                       `$ command → result` (so the reset-from-clean-base, the arm's strangest
#                       property, is SHOWN instead of explained in system prose); pytest's real
#                       counts replace the fake `0/5` ratio; identical error blocks collapse by
#                       cause; an edit gets a real tool result; a rejection lands in the rejected
#                       call's own result slot; and the workbench is fenced as harness state so the
#                       model can tell data from instruction.
#
# One lever, one bundle — the A/B measures the transcript SHAPE, not six independent knobs. It lives
# in style.py because loop.py needs it too: dedup/pip-strip run at WRITE time (before the compress
# throws content away), the envelope and fence at READ time. See style.py for why the order matters.

# Delimiters for the live decision block (the last user turn). The model attends hardest to the end,
# so the freshest run + the editable script get visible section headers there.
_LAST_RUN_LABEL = "── LAST RUN (full — the state you are acting on) ──"
_SCRIPT_DELIM = ('── CURRENT setup.sh (edit by line number; "n| " prefix is NOT part of the script,'
                 ' and matches the build failure\'s "line N") ──')

# Captures the mutation KIND (patch|edit) alongside its change — _PAT_MUTATION drops the kind.
_MUT_KIND = re.compile(r"^(patch|edit) v(\d+)(?: \((.*)\))? → (.+)$")

# Recency gradient on observation detail. The IMMEDIATE (last) run output is shown at a big cap so the
# agent has the fullest signal to diagnose from THIS turn (the "more ideas" tier); recent runs within
# the last-N window compress to a lean cap; older runs elide entirely. All three are RE-RENDERED from
# the one stored observation_raw each turn (build_messages is stateless), so an observation ages
# full → compressed → elided by POSITION, with no second copy stored. Levers, read per-call.
_MSG_IMMEDIATE_CAP = "REACT_MSG_IMMEDIATE_CAP"


def _immediate_cap() -> int:
    """Char budget for the immediate (last) run output — never below the recent-tier cap."""
    try:
        return max(_OBS_COMPRESS_CAP, int(os.getenv(_MSG_IMMEDIATE_CAP, "8000")))
    except ValueError:
        return 8000


def _obs_compressed(obs: "str | None", cap: "int | None" = None) -> str:
    """A build/test observation as real, safety-compressed stdout/stderr, at *cap* chars (default the
    recent-tier cap). safety_compress drops download/apt NOISE and keeps errors/status/head+tail, so a
    bigger cap = more real error signal, not more junk. Header KEPT (unlike the blob's history cards,
    which strip `BUILD OK. TESTS p/e` — here there is no separate verdict, so it carries the count)."""
    cap = _OBS_COMPRESS_CAP if cap is None else cap
    body, _ = safety_compress_observation(obs or "", threshold_chars=cap, target_chars=cap)
    return body.strip()


def _explore_full(obs: "str | None") -> str:
    """Full explore/cat stdout — verbatim, only head+tail hard-capped (reading the file IS the point)."""
    body, _ = safety_truncate(obs or "", max_chars=_EXPLORE_FULL_CAP)
    return body.strip()


def _edit_call_repr(a: dict) -> str:
    """A line-anchored edit rendered BYTE-EXACT from the structured op — full content, no preview
    truncation. Single-line content stays inline; multi-line content is shown indented below the
    header so a long insert reads like the real tool call the model made."""
    verb, start = a.get("verb", "?"), a.get("start", "?")
    end = a.get("end", start)
    span = f"{start}" if end == start else f"{start}-{end}"
    header = f"edit({verb} @{span})"
    content = a.get("content") or ""
    if verb == "delete" or not content.strip():
        return header
    body = content.splitlines()
    if len(body) == 1:
        return f"{header}: {body[0].strip()}"
    return header + ":\n" + "\n".join("    " + ln for ln in body)


def _action_repr_from_struct(a: dict) -> str:
    """Byte-exact action from the structured tool call threaded onto Step (loop.py)."""
    kind = a.get("kind")
    if kind == "explore":
        return f"explore: {a.get('command', '')}"
    if kind == "edit":
        return _edit_call_repr(a)
    if kind == "patch":
        change = a.get("content") or ""
        return f"patch: {change}" if change else "patch"
    return kind or "(action)"


def _action_repr_from_summary(summary: "str | None") -> str:
    """Fallback when a Step carries no structured action (baseline/invalid, or a pre-existing history):
    recover the action from action_summary. Faithful for explores (full command) and typical edits,
    but an edit's content is the ~60-char preview here — the structured path above is byte-exact."""
    summary = summary or ""
    me = _PAT_EXPLORE.match(summary)
    if me:
        return f"explore: {me.group(1)}"
    mm = _MUT_KIND.match(summary)
    if mm:
        kind, change = mm.group(1), mm.group(3)
        return f"{kind}: {change}" if change else kind
    if summary.strip() == "invalid":
        return "(invalid tool call — re-prompted)"
    return summary


def _assistant_content(st) -> str:
    """An assistant turn = the model's real reasoning (if any) + the action it took. Prefers the
    structured tool call (byte-exact); falls back to parsing action_summary when absent."""
    parts = []
    thought = " ".join((st.thought or "").split())
    if thought:
        parts.append(thought)
    action = getattr(st, "action", None)
    repr_ = _action_repr_from_struct(action) if action else _action_repr_from_summary(st.action_summary)
    parts.append("→ " + repr_)
    return "\n".join(parts)


# ── agentic renderers ────────────────────────────────────────────────────────────────────────────
def _call_repr(a: dict) -> str:
    """The action as the CALL the model made — real kwargs, json-escaped, byte-exact. `→ edit(replace
    @7-8): pip install x` was a paraphrase in narrator voice; this is what a tool-use transcript
    actually contains, which is the distribution the model was trained on."""
    kind = a.get("kind")
    if kind == "explore":
        return f'explore(command={json.dumps(a.get("command") or "")})'
    if kind == "edit":
        verb = a.get("verb")
        args = [f"verb={json.dumps(verb)}", f'start={a.get("start")}', f'end={a.get("end", a.get("start"))}']
        content = a.get("content") or ""
        if verb != "delete" and content.strip():
            stripped = content.rstrip("\n")
            if "\n" in stripped:                     # multi-line insert → block form, still verbatim
                return ("edit(" + ", ".join(args) + ', content="""\n' + stripped + '\n""")')
            args.append(f"content={json.dumps(stripped.strip())}")
        return "edit(" + ", ".join(args) + ")"
    if kind == "patch":
        change = a.get("content") or ""
        return f"patch(new_script=…)   # {change}" if change else "patch(new_script=…)"
    return kind or "(action)"


def _assistant_agentic(st_or_pair) -> str:
    """An assistant turn: the model's own reasoning, then the call it made. Accepts a Step or a
    ``{"thought", "action"}`` dict (the rejected-call path, which has no Step)."""
    get = (lambda k: getattr(st_or_pair, k, None)) if hasattr(st_or_pair, "thought") \
        else st_or_pair.get
    thought = " ".join((get("thought") or "").split())
    action = get("action")
    call = _call_repr(action) if action else _action_repr_from_summary(get("action_summary") or "")
    return f"{thought}\n\n{call}" if thought else call


def _run_body(raw: "str | None", cap: int) -> str:
    """A build/test observation for the agentic view: legacy header off (the envelope states it in
    the tool's own voice), identical error blocks collapsed by CAUSE, then the usual safety compress.

    The collapse is UNCONDITIONAL, unlike safety_compress's size-gated selection pass — a 3.5k pytest
    failure sails under that gate untouched, and 3.5k pytest failures are this arm's most common
    observation by far (a live one spent 3,532 chars restating one ModuleNotFoundError five times)."""
    text = strip_pip_progress(strip_legacy_header(raw))
    text = compact_pytest_blocks(text)
    body, _ = safety_compress_observation(text, threshold_chars=cap, target_chars=cap)
    return body.strip()


def _run_message(st, cap: int) -> str:
    """A run observation = the edit's tool result (if any), then `$ cmd → exit N → output`."""
    parts = []
    result = edit_result(getattr(st, "action", None))
    if result:
        parts.append(result)
    parts.append(run_envelope(getattr(st, "outcome", None)))
    body = _run_body(st.observation_raw, cap)
    if body:
        parts.append(body)
    return "\n\n".join(parts)


_FENCE_TOP = "──────────────────────────────── harness state ────────────────────────────────"
_FENCE_BOT = "───────────────────────────────────────────────────────────────────────────────"


def _scaffold_agentic(steps, numbered_script: str, closing_line: str,
                      graph_context_text: "str | None") -> str:
    """The workbench, FENCED. The numbered script, the ledger and the turn budget are harness state,
    not tool output — and nothing in the classic bytes said so: they were pasted bare onto the end of
    a message whose first half was pytest's stdout. The fence is the cheapest possible way to stop the
    model having to guess which voice it is reading."""
    parts = ["setup.sh — edit by these line numbers:\n" + numbered_script]
    ledger = _ledger_note(steps)
    if ledger:
        parts.append(ledger)
    if graph_context_text and graph_context_text.strip():
        parts.append("GRAPH CONTEXT (certified state):\n" + graph_context_text)
    parts.append(closing_line)
    return _FENCE_TOP + "\n" + "\n\n".join(parts) + "\n" + _FENCE_BOT


def _build_agentic(steps, *, system_prompt, numbered_script, closing_line,
                   graph_context_text, rejected, keep_last_obs) -> "list[dict]":
    """The agentic transcript. Same flat Step list, same recency gradient, same ledger — only the
    SHAPE of each message changes (see _MSG_STYLE)."""
    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    obs_indices: list[int] = []
    run_obs: list[tuple[int, object]] = []
    for st in steps:
        summ = st.action_summary or ""
        if _PAT_BASE.match(summ):
            msgs.append({"role": "user", "content": _run_message(st, _OBS_COMPRESS_CAP)})
            obs_indices.append(len(msgs) - 1)
            run_obs.append((len(msgs) - 1, st))
            continue
        if _PAT_EXPLORE.match(summ):
            msgs.append({"role": "assistant", "content": _assistant_agentic(st)})
            cmd = _PAT_EXPLORE.match(summ).group(1)
            body = _explore_full(st.observation_raw)  # explores stay FULL — reading the file IS the point
            msgs.append({"role": "user",
                         "content": f"$ {cmd}\n{body}" if body else f"$ {cmd}\n(no output)"})
            obs_indices.append(len(msgs) - 1)
            continue
        if not _PAT_MUTATION.match(summ):             # an exhausted invalid move
            msgs.append({"role": "assistant", "content": _assistant_agentic(st)})
            msgs.append({"role": "user",
                         "content": "⚠ REJECTED — " + ((st.observation_raw or "").strip()
                                                       or "invalid tool call.")})
            obs_indices.append(len(msgs) - 1)
            continue
        msgs.append({"role": "assistant", "content": _assistant_agentic(st)})
        msgs.append({"role": "user", "content": _run_message(st, _OBS_COMPRESS_CAP)})
        obs_indices.append(len(msgs) - 1)
        run_obs.append((len(msgs) - 1, st))

    _elide_old_observations(msgs, obs_indices, _keep_last_obs(keep_last_obs))
    if run_obs:                                       # immediate tier: re-render the last run bigger
        idx, st = run_obs[-1]
        msgs[idx]["content"] = _run_message(st, _immediate_cap())

    # A refused call belongs in ITS OWN result slot, right after the call — not in a footer three
    # blocks below, addressed to nobody. This is the one fidelity fix with a real behavioural claim:
    # the constraint gets attached to the action that violated it.
    if rejected and rejected.get("action"):
        msgs.append({"role": "assistant", "content": _assistant_agentic(rejected)})
        msgs.append({"role": "user",
                     "content": "⚠ REJECTED by the host — " + (rejected.get("reason") or "invalid call")
                                + "\nThat call did not run. Make a different one."})

    scaffold = _scaffold_agentic(steps, numbered_script, closing_line, graph_context_text)
    if msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n\n" + scaffold
    else:
        msgs.append({"role": "user", "content": scaffold})
    return msgs


def _open_blocker_deltas(steps) -> "list[str]":
    """The edits already tried against the CURRENTLY-open blocker that did not clear it — the
    do-not-retry ledger. Mirrors render_history's signature transitions exactly (reset on baseline /
    confident signature change / reaching a passing state) so both prompt styles agree. Kept because
    reset-each-turn makes this arm fixation-prone in a way SWE-agent (accumulating state) is not."""
    prev_sig = "\x00"
    deltas: list[str] = []
    for st in steps:
        summ = st.action_summary or ""
        if _PAT_EXPLORE.match(summ):
            continue                                     # explore doesn't rebuild → no signature change
        if _PAT_BASE.match(summ):
            prev_sig = extract_blocker(st.observation_raw)
            deltas = []
            continue
        m = _PAT_MUTATION.match(summ)
        if not m:
            continue
        change = m.group(2)
        sig = extract_blocker(st.observation_raw)
        if sig is None:                                  # reached a passing / gate-meeting state
            deltas = []
            prev_sig = None
            continue
        if sig == prev_sig:                              # same blocker persists → record the delta
            if change:
                deltas.append(change)
            prev_sig = sig
            continue
        if _specific(sig) and _specific(prev_sig):       # confident change → new blocker, reset ledger
            deltas = []
            prev_sig = sig
            continue
        prev_sig = sig                                   # low-confidence change → don't split, don't reset
    return deltas


def _ledger_note(steps) -> "str | None":
    """The factual do-not-retry ledger + (past the threshold) the neutral STUCK fact, injected onto
    the live user turn instead of a rendered history section. `directive` mode falls back to the
    neutral line in this prototype (parity is via history_view's own directive path)."""
    deltas = _open_blocker_deltas(steps)
    if not deltas:
        return None
    lines = [f"↳ already tried against this failing state (didn't help): {', '.join(deltas)}"]
    if len(deltas) >= _STUCK_THRESHOLD and _STUCK_MODE != "off":
        lines.append(f"⚠ {len(deltas)} same-shaped edits against this blocker, none cleared it.")
    return "\n".join(lines)


# Old-observation elision (ported from SWE-agent's LastNObservations). Reset-each-turn re-runs the
# WHOLE script every turn, so observations repeat heavily (three identical "Connection refused" dumps
# is typical); keeping only the FIRST (the original failure, cheap context) + the last N verbatim, and
# collapsing the stale middle, is exactly what that processor is for. Lever, read per-call so a VM run
# can A/B it. The current observation is always in the last-N window, so it (and its scaffold) is safe.
_MSG_KEEP_LAST_OBS = "REACT_MSG_KEEP_LAST_OBS"


def _keep_last_obs(explicit: "int | None") -> int:
    if explicit is not None:
        return max(1, explicit)
    try:
        return max(1, int(os.getenv(_MSG_KEEP_LAST_OBS, "3")))
    except ValueError:
        return 3


def _elide_old_observations(msgs: "list[dict]", obs_indices: "list[int]", keep_last: int) -> None:
    """In place: replace the body of every observation that is neither the FIRST nor within the last
    ``keep_last`` with `Old run output: (K lines elided)`. Assistant turns are never in `obs_indices`,
    so reasoning is untouched — only stale tool output collapses (SWE-agent's rule, adapted)."""
    if len(obs_indices) <= keep_last + 1:                # first + last N already covers everything
        return
    kept = {obs_indices[0], *obs_indices[-keep_last:]}
    for i in obs_indices:
        if i in kept:
            continue
        n_lines = len(msgs[i]["content"].splitlines()) or 1
        plural = "line" if n_lines == 1 else "lines"
        msgs[i]["content"] = f"Old run output: ({n_lines} {plural} elided)"


def _scaffold(steps, numbered_script: str, closing_line: str,
              graph_context_text: "str | None", rejection: "str | None") -> str:
    """The live decision block appended to the last user turn: the numbered CURRENT script (the only
    place the model edits — stale earlier scripts are not re-shown), the do-not-retry ledger, optional
    graph context / rejection, and the closing turn-budget line."""
    parts = [_SCRIPT_DELIM + "\n" + numbered_script]
    ledger = _ledger_note(steps)
    if ledger:
        parts.append(ledger)
    if graph_context_text and graph_context_text.strip():
        parts.append("GRAPH CONTEXT (certified state):\n" + graph_context_text)
    if rejection:
        parts.append("YOUR LAST TOOL CALL WAS REJECTED — fix it and try again: " + rejection)
    parts.append(closing_line)
    return "\n\n".join(parts)


def build_messages(steps, *, system_prompt: str, numbered_script: str, closing_line: str,
                   graph_context_text: "str | None" = None, rejection: "str | None" = None,
                   rejected: "dict | None" = None,
                   keep_last_obs: "int | None" = None) -> "list[dict]":
    """Reconstruct the growing conversation from the flat Step list. Pure: same inputs the blob
    renderer gets. Returns ``[{role, content}, ...]`` — system, then baseline observation, then an
    (assistant action, user observation) pair per acting step, with the live scaffold merged onto the
    final user turn. The current observation is therefore simply the last user message. Old
    observations past the first + last-N window are elided (see _elide_old_observations).

    Two shapes, one lever (REACT_MSG_STYLE): `classic` (default) and `agentic` — see _MSG_STYLE."""
    if agentic():
        return _build_agentic(steps, system_prompt=system_prompt, numbered_script=numbered_script,
                              closing_line=closing_line, graph_context_text=graph_context_text,
                              rejected=rejected, keep_last_obs=keep_last_obs)
    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    obs_indices: list[int] = []                          # indices of user OBSERVATION turns (for elision)
    run_obs: list[tuple[int, str]] = []                  # (msg_idx, raw) of RUN outputs (baseline/build)
    for st in steps:
        summ = st.action_summary or ""
        if _PAT_BASE.match(summ):
            body = _obs_compressed(st.observation_raw) or "(no output)"
            msgs.append({"role": "user", "content": "BASELINE — ran the seed setup.sh:\n" + body})
            obs_indices.append(len(msgs) - 1)
            run_obs.append((len(msgs) - 1, st.observation_raw or ""))
            continue
        if _PAT_EXPLORE.match(summ):
            msgs.append({"role": "assistant", "content": _assistant_content(st)})
            body = _explore_full(st.observation_raw)     # explores are ALWAYS full (reading is the point)
            msgs.append({"role": "user",
                         "content": ("explore result:\n" + body) if body else "explore result: (no output)"})
            obs_indices.append(len(msgs) - 1)
            continue
        # mutation (edit/patch) or an exhausted invalid move → assistant action then its observation
        msgs.append({"role": "assistant", "content": _assistant_content(st)})
        msgs.append({"role": "user", "content": _obs_compressed(st.observation_raw) or "(no output)"})
        obs_indices.append(len(msgs) - 1)
        run_obs.append((len(msgs) - 1, st.observation_raw or ""))

    # Collapse stale middle observations (first + last-N kept) BEFORE the immediate re-render, so the
    # immediate render always wins over any elision on the last run output.
    _elide_old_observations(msgs, obs_indices, _keep_last_obs(keep_last_obs))
    # Immediate tier: re-render the LAST run output at the big cap + a visible label. Same stored raw,
    # bigger budget — this is the "display the current run in full" the recency gradient is about.
    if run_obs:
        idx, raw = run_obs[-1]
        msgs[idx]["content"] = _LAST_RUN_LABEL + "\n" + (_obs_compressed(raw, _immediate_cap()) or "(no output)")
    # Merge the live scaffold onto the last user turn LAST (never disturbed by the steps above).
    scaffold = _scaffold(steps, numbered_script, closing_line, graph_context_text, rejection)
    if msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n\n" + scaffold
    else:                                                # empty history → no user turn yet
        msgs.append({"role": "user", "content": scaffold})
    return msgs
