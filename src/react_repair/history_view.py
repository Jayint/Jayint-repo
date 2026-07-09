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

from src.react_repair.observation import safety_compress_observation

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


def _short(sig: str) -> str:
    """A compact reference to a blocker for an inline note."""
    m = _MOD.search(sig)
    if m:
        return f"'{m.group(1)}'"
    m = _FATAL.search(sig)
    if m:
        return m.group(1).split(":")[0].strip()
    return sig.split(" → ")[-1][:40]


# A build-mutating move: the native-tool-calling arm records these as `edit v..`; the whole-script
# path records `patch v..`. BOTH must hit this branch — else an edit (the arm's PRIMARY action)
# falls through to "(invalid move — re-prompted)" and the blocker/do-not-retry tracking below never
# runs for the current arm.
_PAT_MUTATION = re.compile(r"^(?:patch|edit) v(\d+)(?: \((.*)\))? → (.+)$")
_PAT_BASE = re.compile(r"^baseline → (.+)$")
_PAT_EXPLORE = re.compile(r"^explore: (.+)$")

# An explore OLDER than the recency window keeps only the FACT it surfaced, not its full body: a
# compact, hard-capped digest (the knowledge ledger) so old probes don't bloat the prompt.
_EXPLORE_FINDING_CAP = 200

# The last few explores are different: the agent is acting on what it just read, and it needs the
# real output — the flat 200-char digest blinded it into re-probing the same file (addons re-cat
# pyproject.toml 16×; azure re-read tools/setup.py ~13×). Show the last _EXPLORE_BODY_STEPS explores'
# output content-aware (radical's safety_compress_observation) at this char budget, in-block beside
# the patch each informed; older ones fall back to the digest. Both tunable.
_EXPLORE_BODY_STEPS = int(os.getenv("REACT_EXPLORE_BODY_STEPS", "3"))
_EXPLORE_BODY_CAP = int(os.getenv("REACT_EXPLORE_OUTPUT_CAP", "4000"))


def _explore_finding(obs: str | None) -> str:
    """Whitespace-flattened, hard-capped head of an explore's output ("" when it produced none)."""
    if not obs:
        return ""
    flat = " ".join(obs.split())
    return flat if len(flat) <= _EXPLORE_FINDING_CAP else flat[:_EXPLORE_FINDING_CAP].rstrip() + " …"


# Recency window for build/test BODIES. The grouped view is signature-only by default; but the last
# few PRIOR attempts' actual error text can carry detail the signature collapses (WHICH egg-info
# dirs, WHICH missing symbol). Show the compressed body of the last _RECENT_BODY_STEPS mutations that
# CHANGED the error (a transition — a still-blocked prior just restates the signature, and the
# current attempt is already shown in full under LAST RUN OBSERVATION). Signatures still do the bulk.
_RECENT_BODY_STEPS = int(os.getenv("REACT_RECENT_BODY_STEPS", "2"))
_RECENT_BODY_CAP = int(os.getenv("REACT_RECENT_BODY_CHARS", "1200"))


def _recent_body(obs: str | None) -> str:
    """Compressed body of a recent prior attempt, indented under its summary line ("" if empty)."""
    if not obs:
        return ""
    body, _ = safety_compress_observation(
        obs, threshold_chars=_RECENT_BODY_CAP, target_chars=_RECENT_BODY_CAP)
    body = body.strip()
    if not body:
        return ""
    return "      └ output (prior attempt):\n" + "\n".join("        " + ln for ln in body.splitlines())


def render_history(steps) -> str:
    if not steps:
        return "HISTORY — (no prior steps yet)"
    lines = ["HISTORY — chronological; a BLOCKER header marks where the failure signature changed:"]
    prev_sig = "\x00"                                # sentinel: nothing seen yet
    block_no = 0
    failed_deltas: list[str] = []
    last = steps[-1]                                 # the just-run step gets its full explore output
    # The last _RECENT_BODY_STEPS PRIOR mutations (exclude the current one — it's already shown in
    # full under LAST RUN OBSERVATION) may carry their compressed body on an error transition.
    mut_positions = [i for i, s in enumerate(steps) if _PAT_MUTATION.match(s.action_summary or "")]
    body_window = (set(mut_positions[-(_RECENT_BODY_STEPS + 1):-1])
                   if len(mut_positions) >= 2 else set())
    # The last _EXPLORE_BODY_STEPS explores (by recency, across blocks) render their FULL output;
    # recency lands them in the active block, beside the patch they informed. Older ones → digest.
    explore_positions = [i for i, s in enumerate(steps) if _PAT_EXPLORE.match(s.action_summary or "")]
    explore_body_window = set(explore_positions[-_EXPLORE_BODY_STEPS:])

    def open_block(sig, context):
        nonlocal block_no, failed_deltas
        block_no += 1
        shown = sig if sig is not None else "(build meets the gate — no blocker)"
        lines.append(f"[{block_no}] BLOCKER: {shown}   ({context})")
        failed_deltas = []

    for idx, st in enumerate(steps):
        summ = st.action_summary or ""

        me = _PAT_EXPLORE.match(summ)
        if me:
            if idx in explore_body_window:          # recent probe → real output, in-block by the patch
                body, _ = safety_compress_observation(
                    st.observation_raw or "",
                    threshold_chars=_EXPLORE_BODY_CAP, target_chars=_EXPLORE_BODY_CAP)
                body = body.strip()
                if body:                            # indent the body under its `explored` line
                    indented = "\n".join("        " + ln for ln in body.splitlines())
                    lines.append(f"      explored `{me.group(1)}` →\n{indented}")
                else:
                    lines.append(f"      explored `{me.group(1)}`")
            else:                                   # aged probe → compact digest (knowledge ledger)
                finding = _explore_finding(st.observation_raw)
                lines.append(f"      explored `{me.group(1)}`" + (f" → {finding}" if finding else ""))
            continue

        mb = _PAT_BASE.match(summ)
        if mb:
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
            if st is last and hint:
                lines.append("      (invalid move — re-prompted): " + hint)
            else:
                lines.append("      (invalid move — re-prompted)")
            continue

        ver, change, score = mp.group(1), mp.group(2), mp.group(3)
        change_s = f"({change}) " if change else ""
        sig = extract_blocker(st.observation_raw)
        # A recent prior attempt's body — only attached on an error TRANSITION below (where the body
        # carries detail the signature collapses); a still-blocked repeat just restates the signature.
        body = _recent_body(st.observation_raw) if idx in body_window else ""

        if sig is None:                             # reached a passing / gate-meeting state
            lines.append(f"      v{ver} {change_s}→ {score}   previous error no longer present → gate reached")
            failed_deltas = []
            prev_sig = None
            continue
        if sig == prev_sig:                         # same blocker persists
            lines.append(f"      v{ver} {change_s}→ {score}   still blocked (same error)")
            if change:
                failed_deltas.append(change)
            prev_sig = sig
            continue
        if _specific(sig) and _specific(prev_sig):  # CONFIDENT change → close block, open the next
            lines.append(f"      v{ver} {change_s}→ {score}   {_short(prev_sig)} no longer present")
            if body:
                lines.append(body)
            open_block(sig, f"surfaced after v{ver}")
            prev_sig = sig
            continue
        # low-confidence change (weak signature on one side) → do NOT split; note inline
        lines.append(f"      v{ver} {change_s}→ {score}   error changed (signature uncertain)")
        if body:
            lines.append(body)
        prev_sig = sig

    if failed_deltas:                               # still-open blocker: consolidate what didn't help
        lines.append(f"      ↳ already tried for this blocker (didn't help): {', '.join(failed_deltas)}")
        if len(failed_deltas) >= 2:
            # Anti-fixation reframe (HerAgent "Historical Reflection"): >=2 edits against the SAME
            # blocker with none clearing it is fixation, not progress — promote the passive ledger to
            # an imperative to change strategy (the addons 3x setuptools re-pin). The factual line
            # above stays so the agent still sees exactly what NOT to retry.
            lines.append(
                "      ⚠ STUCK — this blocker survived every edit above; none cleared it. STOP "
                "repeating variations of the same fix and CHANGE your APPROACH. Re-read the error "
                "and reconsider: is the install METHOD wrong (a system library is needed, not a pip "
                "package; or `pip install -e .` is not right for this repo), or is a service / "
                "config / env var the real gap?")
    return "\n".join(lines)
