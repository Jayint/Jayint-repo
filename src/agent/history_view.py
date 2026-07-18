"""Grouped, chronological history view for the react arm's planner prompt (design reviewed by Codex).

The canonical history is the flat chronological Step list (`History.steps`); this renders it as a
navigation layer over that truth — NOT a replacement for it:

  - **Chronological backbone.** Entries are never reordered; a ``BLOCKER:`` header is *inserted*
    where the failure signature changes, so oscillation (A→B→A) stays visible instead of being
    merged.
  - **Hedged language.** "previous error no longer present", never a causal "cleared" — the whole
    script re-runs each turn, so a single patch's ``+lines`` may not be what fixed it.
  - **Conservative splitting.** A new block opens only on a CONFIDENT signature change (a specific
    error token on both sides). A low-confidence change is noted inline, not split, so we never
    fabricate progress (a false "new blocker" is worse than an over-long block).
  - **Do-not-retry.** The still-open blocker ends with the deltas already tried against it that
    did not clear it.

Signatures are structured (failing command, missing module, fatal header) rather than raw error
text — exact for build failures (we have the failing command), a bounded regex heuristic for test
failures.
"""
from __future__ import annotations

import os
import re

from src.agent.history import safety_truncate
from src.agent.observe import safety_compress_observation

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
