"""The react arm's per-run TRANSCRIPT — record + render (spec §6/§4A consolidation).

One file for the flat Step list and every way it is turned into prompt text. Folded together (§4A)
from four single-concept modules, each preserved below under its own section banner:

  * history       — the Step dataclass, `safety_truncate` (Tier-1 head/tail cap), and the `History`
    recorder (Tier-2 reflective compression hook).
  * style         — the `REACT_MSG_STYLE` lever (`agentic()`), shared because both write-time
    (loop) and read-time (render) need it.
  * history_view  — the BLOB render (`render_history`): markdown BLOCKER cards, the do-not-retry
    ledger, the size caps (`_OBS_COMPRESS_CAP` / `_EXPLORE_FULL_CAP` / `_THOUGHT_CAP`), and the
    signature machinery (`extract_blocker` / `_specific` / the `_PAT_*` matchers).
  * message_view  — the growing MESSAGE-LIST render (`build_messages`): classic + agentic shapes,
    the recency-tiered observation elision, the fenced workbench scaffold.

R4 dedup: `_explore_full` was byte-duplicated in history_view + message_view (identical body); the
single copy from history_view is kept. The caps are defined ONCE here (message_view used to import
them from history_view by value) — the golden's `_ATTR_DEFAULTS` pins them on this one module now."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable

from src.agent.observe import (
    compact_pytest_blocks, edit_result, run_envelope, safety_compress_observation,
    strip_legacy_header, strip_pip_progress)


@dataclass
class Step:
    step_id: int
    thought: str
    action_summary: str
    observation_raw: str
    observation_prompt: str          # possibly truncated (Tier 1) then compressed (Tier 2)
    action: "dict | None" = None     # structured tool call (verb/start/end/content or command) for
                                     # BYTE-EXACT message-list rendering; None for baseline/invalid
                                     # steps (message_view then falls back to parsing action_summary)
    outcome: "dict | None" = None    # structured build/test result (build_ok, failing_command, lineno,
                                     # passed/failed/errors/skipped/collected). The agentic view renders
                                     # its `$ cmd → exit N` envelope from THIS, not by re-parsing the
                                     # observation text — the stored body is already compressed, so its
                                     # summary line may not survive. None for explore/invalid steps.


def safety_truncate(text: str, *, max_chars: int, keep_tail: bool = True) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    if not keep_tail:
        return text[:max_chars] + "…[truncated]…", True
    # Keep BOTH ends: the head carries the build header / failing command / start of the error,
    # the tail carries the pytest summary + final error line. Tail-only buried the head under
    # download/compiler noise on recent (not-yet-LLM-compressed) steps.
    head = max_chars // 4
    tail = max_chars - head
    return f"{text[:head]}\n…[omitted]…\n{text[-tail:]}", True


class History:
    def __init__(self, *, safety_max_chars: int = 4000, compress_delay: int = 2,
                 compress_threshold_chars: int = 1500,
                 compressor: "Callable[[Step, list[Step]], str] | None" = None,
                 log=None):
        self.steps: list[Step] = []
        self.safety_max_chars = safety_max_chars
        self.compress_delay = compress_delay
        self.compress_threshold_chars = compress_threshold_chars
        self.compressor = compressor
        self.log = log

    def record(self, step_id: int, thought: str, action_summary: str,
               observation_raw: str, action: "dict | None" = None,
               outcome: "dict | None" = None) -> Step:
        prompt_obs, _ = safety_truncate(observation_raw or "", max_chars=self.safety_max_chars)
        step = Step(step_id, thought, action_summary, observation_raw or "", prompt_obs,
                    action, outcome)
        self.steps.append(step)
        self._maybe_compress()           # Tier 2 — reflective compression (no-op if no compressor injected)
        return step

    def _maybe_compress(self) -> None:
        if self.compressor is None:
            return
        target_idx = len(self.steps) - 1 - self.compress_delay
        if target_idx < 0:
            return
        target = self.steps[target_idx]
        already = target.observation_prompt != target.observation_raw and "[summary" in target.observation_prompt
        if already or len(target.observation_raw) < self.compress_threshold_chars:
            return
        try:
            reduced = self.compressor(target, self.steps[:target_idx])
        except Exception as exc:                                 # never break the run (spec §10)
            if self.log is not None:
                self.log.d("COMPRESS", f"compression failed, keeping raw: {exc}")
            return
        target.observation_prompt = reduced
        if self.log is not None:
            self.log.d("COMPRESS", f"step {target.step_id}: {len(target.observation_raw)} chars → summary")
            self.log.trace("compress", tier=2, target_step=target.step_id,
                           raw_chars=len(target.observation_raw), summary_chars=len(reduced),
                           summary=reduced)

    def render(self) -> str:
        if not self.steps:
            return "(no prior steps)"
        return "\n".join(
            f"{s.step_id}. [{s.action_summary}] {s.observation_prompt}" for s in self.steps
        )


# ======================================================================================
# style — folded from style.py (§4A): the REACT_MSG_STYLE lever.
#
# The prompt-style lever, in one place because BOTH ends of the pipe need it.
#
# `REACT_MSG_STYLE=agentic` is a bundle, not a render flag — and the ordering matters:
#
#   write time (loop._obs_body)   dedup + pip-strip, THEN safety_compress
#   read time  (message_view)     envelope + fence, re-render at the recency tier
#
# Dedup must happen BEFORE compression, not after. safety_compress's selection pass keeps at most 12
# error blocks; on a 50k pytest dump with 200 identical collection errors, those 12 slots fill with
# copies of ONE cause and a genuinely different cause further down is dropped — permanently, because
# the compressed text is what gets stored. Deduping first means the 12 slots hold 12 distinct CAUSES.
# Rendering afterwards can only shrink what write time already threw away.
#
# (Both transforms are idempotent, so the render layer re-applies them harmlessly — which keeps the
# view correct for hand-built histories and for traces recorded before this landed.)
#
# Read per-call, never cached at import: a VM run flips it via env, and the unit tests monkeypatch it.
# ======================================================================================

_LEVER = "REACT_MSG_STYLE"


def agentic() -> bool:
    """True when the agentic prompt bundle is on. Default is `classic` — the shape that shipped, and
    the A/B's control arm."""
    return os.getenv(_LEVER, "classic").lower() == "agentic"


# ======================================================================================
# history_view — folded from history_view.py (§4A): the blob render + caps + signatures.
#
# Grouped, chronological history view for the react arm's planner prompt (design reviewed by Codex).
#
# The canonical history is the flat chronological Step list (`History.steps`); this renders it as a
# navigation layer over that truth — NOT a replacement for it:
#
#   - **Chronological backbone.** Entries are never reordered; a ``BLOCKER:`` header is *inserted*
#     where the failure signature changes, so oscillation (A→B→A) stays visible instead of being
#     merged.
#   - **Hedged language.** "previous error no longer present", never a causal "cleared" — the whole
#     script re-runs each turn, so a single patch's ``+lines`` may not be what fixed it.
#   - **Conservative splitting.** A new block opens only on a CONFIDENT signature change (a specific
#     error token on both sides). A low-confidence change is noted inline, not split, so we never
#     fabricate progress (a false "new blocker" is worse than an over-long block).
#   - **Do-not-retry.** The still-open blocker ends with the deltas already tried against it that
#     did not clear it.
#
# Signatures are structured (failing command, missing module, fatal header) rather than raw error
# text — exact for build failures (we have the failing command), a bounded regex heuristic for test
# failures.
# ======================================================================================

# Distinguishing failure tokens, most-specific first. Vocabulary widened from the radical
# baseline's SAFETY_ERROR_PATTERNS / select_failure_lines so service/tool/permission/timeout
# failures get a real signature instead of the weak "build failed: <cmd>" fallback.
_MOD = re.compile(r"No module named ['\"]([\w.]+)['\"]")
_FATAL = re.compile(r"fatal error: *([^\n]+)")
_CONN_REFUSED = re.compile(r"[Cc]onnection refused")
_CONN_HOSTPORT = re.compile(r"connecting to ([\w.\-]+:\d+)")
_CMD_NF = re.compile(r"([\w.\-+/]+): command not found")
_CMD_NF2 = re.compile(r"command not found: ([\w.\-+/]+)")
_PERM = re.compile(r"Permission denied(?::? *['\"]?([^'\"\n]{0,60}))?", re.IGNORECASE)
_NOFILE = re.compile(r"No such file or directory(?::? *['\"]?([^'\"\n]{0,60}))?", re.IGNORECASE)
_TIMEOUT = re.compile(r"timed out|TimeoutError|ETIMEDOUT", re.IGNORECASE)
_PYEXC = re.compile(r"([A-Z]\w*(?:Error|Exception): [^\n]+)")   # generic Python exception tail
_PIPERR = re.compile(r"(ERROR: [^\n]+)")
_HDR_FAIL = re.compile(r"^BUILD FAILED at `([^`]*)`")
_HDR_OK = re.compile(r"^BUILD OK\. TESTS (\d+)/(\d+)")


def _detail(obs: str) -> str | None:
    """The most distinguishing failure token in *obs*, or None. Ordered most-specific first so a
    precise class (missing module, fatal header, connection refused, …) wins over generic ones."""
    m = _MOD.search(obs)
    if m:
        return f"No module named '{m.group(1)}'"
    m = _FATAL.search(obs)
    if m:
        return f"fatal error: {m.group(1).strip()}"[:90]
    if _CONN_REFUSED.search(obs):                          # service unreachable (redis/postgres/…)
        hp = _CONN_HOSTPORT.search(obs)
        return f"connection refused: {hp.group(1)}" if hp else "connection refused"
    m = _CMD_NF.search(obs) or _CMD_NF2.search(obs)        # missing tool/binary
    if m:
        return f"command not found: {m.group(1)}"
    m = _PERM.search(obs)
    if m:
        path = (m.group(1) or "").strip().strip("'\"")
        return f"permission denied: {path}"[:90] if path else "permission denied"
    m = _NOFILE.search(obs)
    if m:
        path = (m.group(1) or "").strip().strip("'\"")
        return f"no such file: {path}"[:90] if path else "no such file or directory"
    if _TIMEOUT.search(obs):
        return "timed out"
    m = _PYEXC.search(obs)                                 # any other Python exception (RuntimeError…)
    if m:
        return m.group(1).strip()[:90]
    m = _PIPERR.search(obs)
    if m:
        return m.group(1).strip()[:90]
    return None


def extract_blocker(observation: str | None) -> str | None:
    """A short structured signature of the top failure, or None when there is no blocker (build ok
    AND all executed tests pass, or non-build output such as an explore probe)."""
    if not observation:
        return None
    split = observation.splitlines()
    first = split[0] if split else ""
    mf = _HDR_FAIL.match(first)
    if mf:
        cmd = mf.group(1)
        d = _detail(observation)
        return f"{cmd} → {d}" if d else f"build failed: {cmd}"
    mo = _HDR_OK.match(first)
    if mo:
        passed, executed = int(mo.group(1)), int(mo.group(2))
        if passed >= executed:                      # all executed passed → no blocker
            return None
        d = _detail(observation)
        return f"tests: {d}" if d else f"tests failing ({passed}/{executed})"
    return None                                     # unrecognized (explore output etc.)


def _specific(sig: str | None) -> bool:
    """Trustworthy enough to split a block on — i.e. NOT one of the two weak fallbacks
    (`build failed: <cmd>` / `tests failing (N/M)`) that carry no distinguishing error token."""
    return bool(sig) and not sig.startswith("build failed:") and not sig.startswith("tests failing (")


def _header_label(sig: "str | None") -> "str | None":
    """The BLOCKER header's descriptor. A SPECIFIC signature shows verbatim; the weak fallbacks are
    NOT shown as prose (`build failed: …` / `tests failing (N/M)` / `build meets the gate …`): a weak
    build failure shows just its real failing command, and the tokenless / gate cases get NO descriptor
    (bare `### BLOCKER n` — the real error is in the cards, the context note carries the outcome).
    extract_blocker's internal returns are unchanged, so control flow (splitting/ledger/STUCK) is intact."""
    if sig is None:
        return None
    if _specific(sig):
        return sig
    if sig.startswith("build failed: "):
        return sig[len("build failed: "):]              # weak build → the real failing command, no prose
    return None                                         # weak test (tokenless) → bare header


# A build-mutating move: the native-tool-calling arm records these as `edit v..`; the whole-script
# path records `patch v..`. BOTH must hit this branch — else an edit (the arm's PRIMARY action)
# falls through to "(invalid move — re-prompted)" and the blocker/do-not-retry tracking below never
# runs for the current arm.
_PAT_MUTATION = re.compile(r"^(?:patch|edit) v(\d+)(?: \((.*)\))? → (.+)$")
_PAT_BASE = re.compile(r"^baseline → (.+)$")
_PAT_EXPLORE = re.compile(r"^explore: (.+)$")

# Honest observe body — the REAL stdout/stderr, not a paraphrase (user directive). A build/test
# observation is safety_compress'd (drops download/apt noise, keeps errors + install/test status);
# an explore/cat probe shows its FULL stdout (reading the whole file IS the point of exploring),
# only head+tail hard-capped so a pathological `find /` can't blow the prompt. The blocker SIGNATURE
# still drives control flow (block splitting, do-not-retry, STUCK) — only the DISPLAY changed from a
# synthesized verdict ("still blocked") to the actual output.
_OBS_COMPRESS_CAP = int(os.getenv("REACT_OBS_BODY_CAP", "1500"))     # build/test → safety_compress'd
_EXPLORE_FULL_CAP = int(os.getenv("REACT_EXPLORE_FULL_CAP", "6000"))  # explore/cat → full (head+tail cap)

# Anti-fixation stuck signal. The FACTUAL do-not-retry ledger ("already tried …") always shows; this
# is the extra escalation once a blocker survives _STUCK_THRESHOLD same-shaped edits. Default is a
# NEUTRAL fact — no prescriptive coaching. The old `directive` text ("CHANGE your APPROACH; is a
# service the real gap?") is off by default: it can misfire (advising abandonment of a correct
# incremental fix) AND it pre-bakes a crude graph-style diagnosis into the baseline, contaminating the
# graph ablation. Kept as a lever (REACT_STUCK_MODE=directive|neutral|off) so it can be A/B'd, not assumed.
_STUCK_THRESHOLD = int(os.getenv("REACT_STUCK_THRESHOLD", "3"))
_STUCK_MODE = os.getenv("REACT_STUCK_MODE", "neutral").lower()      # neutral | off | directive

# History layout lever (blob path only — the default arm is now the message list, see message_view).
# `flat` (DEFAULT) is the SWE-agent shape: a plain chronological list of think→action→observe cards —
# no headers/grouping/ledger/STUCK. `grouped` (opt-in) organizes cards under `### BLOCKER n` headers
# keyed on the failure signature, with the do-not-retry ledger + STUCK. Because the arm re-runs the
# WHOLE script from a clean base each turn, the cross-turn "blocker" narrative is partly synthetic (a
# `set -e` script just reveals the next latent failure), so flat is the honest default. Observe bodies
# stay real in both modes.
_HISTORY_MODE = os.getenv("REACT_HISTORY", "flat").lower()         # flat | grouped


def _observe_body(obs: str | None) -> str:
    """Real, safety-compressed stdout/stderr for a build/test step ("" if empty). The synthesized
    `BUILD OK. TESTS p/e passed` header line is dropped for display (it duplicated the action verdict);
    the `BUILD FAILED at cmd (line N)` header stays — its line number earns it. extract_blocker still
    reads the untouched observation_raw for the signature."""
    text = re.sub(r"^BUILD OK\. TESTS \d+/\d+ passed[^\n]*\n?", "", obs or "", count=1)
    body, _ = safety_compress_observation(
        text, threshold_chars=_OBS_COMPRESS_CAP, target_chars=_OBS_COMPRESS_CAP)
    return body.strip()


def _explore_full(obs: str | None) -> str:
    """Full stdout for an explore/cat probe — verbatim, only head+tail hard-capped ("" if empty)."""
    body, _ = safety_truncate(obs or "", max_chars=_EXPLORE_FULL_CAP)
    return body.strip()


def _indent(body: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + ln for ln in body.splitlines())


# The model's own reasoning for a step — the `think` half of the think→action→observe card. Kept
# (capped, single-line) so the agent sees WHY it made a move and doesn't re-reason a path it already
# rejected (SWE-agent/mini keep every thought; our old view dropped it, showing only the outcome).
_THOUGHT_CAP = int(os.getenv("REACT_THOUGHT_CAP", "180"))


def _thought_line(thought: str | None) -> str | None:
    """A capped, single-line ``think:`` for a step's reasoning; None when there is no thought (all
    existing render tests use thought="" → no line, so the card degrades to action+observe)."""
    t = " ".join((thought or "").split())
    if not t:
        return None
    if len(t) > _THOUGHT_CAP:
        t = t[:_THOUGHT_CAP].rstrip() + " …"
    return f"    think:   {t}"


def render_history(steps) -> str:
    """Render the flat Step list as markdown BLOCKER sections, each holding think→action→observe
    cards. The `observe` half is the REAL output (user directive): a build/test step shows its
    safety-compressed stdout/stderr, an explore/cat probe shows its FULL stdout. A `### BLOCKER n`
    header opens where the failure SIGNATURE changes; the signature still drives control flow (block
    splitting, do-not-retry, STUCK) even though the display is now the raw output, not a verdict.

    Two bodies are deliberately withheld to avoid duplication/bloat: the CURRENT mutation's output
    (already shown in full under LAST RUN OBSERVATION — after an explore the top still shows the last
    build, so `mut_positions[-1]` is what's up there), and a still-blocked repeat whose output is
    byte-identical to the prior attempt (collapsed to `(output unchanged from vN)`). Chronological
    (oldest→newest); the still-open blocker sits last and is tagged `← current turn`."""
    if not steps:
        return "HISTORY — (no prior steps yet)"
    grouped = _HISTORY_MODE != "flat"               # flat = SWE-agent shape: no headers/ledger/STUCK
    if grouped:
        lines = ["HISTORY — chronological; grouped by BLOCKER (the failure signature being fought). "
                 "Each attempt is a card: think → action → observe (observe = real stdout/stderr)."]
    else:
        lines = ["HISTORY — chronological; each attempt is a card: think → action → observe "
                 "(observe = real stdout/stderr)."]
    prev_sig = "\x00"                                # sentinel: nothing seen yet
    block_no = 0
    failed_deltas: list[str] = []
    open_header_idx = -1                             # line index of the current block's header (← marker)
    prev_body, prev_ver = None, None                # last rendered mutation body (for dedup)
    # The CURRENT mutation's output is up top under LAST RUN OBSERVATION (explores don't rebuild, so
    # it is the LAST mutation, not necessarily the last step) — withhold its body here to avoid a dup.
    mut_positions = [i for i, s in enumerate(steps) if _PAT_MUTATION.match(s.action_summary or "")]
    current_mut = mut_positions[-1] if mut_positions else None

    def open_block(sig, context):
        nonlocal block_no, failed_deltas, open_header_idx
        block_no += 1
        failed_deltas = []
        if not grouped:                                  # flat: no BLOCKER header (control flow still runs)
            return
        label = _header_label(sig)                       # None for weak/gate → bare header (no prose)
        open_header_idx = len(lines)
        head = f"### BLOCKER {block_no} — {label}" if label else f"### BLOCKER {block_no}"
        lines.append(f"{head}   ({context})")

    def observe(body: str) -> None:
        """Append the `observe:` half from a real, already-extracted output body (or a placeholder)."""
        nonlocal prev_body, prev_ver
        if not body:
            lines.append("    observe: (no output)")
        elif body == prev_body:                     # byte-identical repeat → don't re-print it
            lines.append(f"    observe: (output unchanged from v{prev_ver})")
        else:
            lines.append("    observe:\n" + _indent(body, 6))

    for idx, st in enumerate(steps):
        summ = st.action_summary or ""
        thought = _thought_line(st.thought)

        me = _PAT_EXPLORE.match(summ)
        if me:
            if thought:                             # think → action → observe: reason first, then probe
                lines.append(thought)
            body = _explore_full(st.observation_raw)    # FULL stdout — reading the file IS the point
            if body:
                lines.append(f"- explored `{me.group(1)}` →\n{_indent(body, 8)}")
            else:
                lines.append(f"- explored `{me.group(1)}`")
            continue

        mb = _PAT_BASE.match(summ)
        if mb:
            # The baseline's real output is summarized by the first BLOCKER header (its signature);
            # its full body is the oldest/least-relevant content, so the header stands in for it.
            prev_sig = extract_blocker(st.observation_raw)
            open_block(prev_sig, f"baseline: {mb.group(1)}")
            continue

        mp = _PAT_MUTATION.match(summ)
        if not mp:
            # Surface the loop's guidance for the JUST-MADE invalid move — the agent is about to
            # retry and needs to know WHY it was rejected (e.g. "explore is read-only, use edit()").
            # Aged invalids stay terse so old reminders don't pile up. Without this the reminder was
            # dead: the agent only ever saw "(invalid move)" with no direction (azure).
            hint = (st.observation_raw or "").strip()
            if st is steps[-1] and hint:
                lines.append("- (invalid move — re-prompted): " + hint)
            else:
                lines.append("- (invalid move — re-prompted)")
            continue

        ver, change = mp.group(1), mp.group(2)          # group(3) is the verdict — no longer displayed (it
        action = f"- v{ver} · {change}" if change else f"- v{ver}"   # duplicated the observe header)
        sig = extract_blocker(st.observation_raw)

        lines.append(action)                        # the ACTION half of the card
        if thought:                                 # the THINK half (only when the model gave one)
            lines.append(thought)
        if idx == current_mut:                      # OBSERVE half — real output, or the top pointer
            lines.append("    observe: (current run — full output above under LAST RUN OBSERVATION)")
        else:
            body = _observe_body(st.observation_raw)
            observe(body)
            prev_body, prev_ver = body, ver

        # Control flow on the SIGNATURE (unchanged): block splitting + do-not-retry + STUCK.
        if sig is None:                             # reached a passing / gate-meeting state
            failed_deltas = []
            prev_sig = None
            continue
        if sig == prev_sig:                         # same blocker persists → feed the anti-repeat ledger
            if change:
                failed_deltas.append(change)
            prev_sig = sig
            continue
        if _specific(sig) and _specific(prev_sig):  # CONFIDENT change → close block, open the next
            open_block(sig, f"surfaced after v{ver}")
            prev_sig = sig
            continue
        prev_sig = sig                              # low-confidence change → do NOT split

    if grouped:                                     # flat mode has no blocker to mark / no ledger / no STUCK
        if prev_sig is not None and open_header_idx >= 0:   # the last block is still OPEN → mark it current
            lines[open_header_idx] += "   ← current turn"
        if failed_deltas:                           # still-open blocker: the FACTUAL do-not-retry ledger
            lines.append(f"    ↳ already tried for this blocker (didn't help): {', '.join(failed_deltas)}")
            # Escalation only past the threshold, and neutral by default (a fact, not coaching). The
            # prescriptive `directive` variant is opt-in — see the _STUCK_MODE note above.
            if len(failed_deltas) >= _STUCK_THRESHOLD and _STUCK_MODE != "off":
                if _STUCK_MODE == "directive":
                    lines.append(
                        "    ⚠ STUCK — this blocker survived every edit above; none cleared it. STOP "
                        "repeating variations of the same fix and CHANGE your APPROACH. Re-read the error "
                        "and reconsider: is the install METHOD wrong (a system library is needed, not a pip "
                        "package; or `pip install -e .` is not right for this repo), or is a service / "
                        "config / env var the real gap?")
                else:                               # "neutral" (default): a fact, no prescription
                    lines.append(f"    ⚠ {len(failed_deltas)} same-shaped edits against this blocker, "
                                 "none cleared it.")
    return "\n".join(lines)


# ======================================================================================
# message_view — folded from message_view.py (§4A): the message-list render (R4: its duplicate _explore_full was dropped; the history_view copy above is kept).
#
# Message-list prompt builder for the react arm (lever: REACT_PROMPT_STYLE=messages).
#
# The default `blob` style (planner._render) sends TWO messages per turn — a stable system prompt and
# ONE re-rendered user blob whose "history" is a third-person reconstruction (render_history). This
# module is the alternative SWE-agent/mini-swe-agent shape: a GROWING message list where the model's
# own thought+action are real ``assistant`` turns and each observation is a real ``user`` turn.
#
# Two things fall out of that shape, and they are the point of the experiment:
#   - **Whose words.** The assistant turns carry the model's real reasoning + the action it took,
#     not a narrator's paraphrase.
#   - **The current observation is just the last user message.** There is no dedicated
#     ``LAST RUN OBSERVATION`` slot duplicating a history card, so the withhold/dedup machinery in
#     render_history has nothing to do here — it simply doesn't exist in this path.
#
# Reset-each-turn caveat: unlike SWE-agent (persistent repo), every observation here is the WHOLE
# script re-run from a clean base, so a ``user`` observation is "what running the current script does
# now", not the incremental result of just the preceding edit. The transcript still reads coherently
# (edit → whole-script result); the system prompt already states the reset semantics.
#
# This is a render layer over the same flat Step list — arm-independent, like render_history. The
# signature control flow (do-not-retry ledger) is preserved and mirrors history_view's transitions.
# ======================================================================================

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
