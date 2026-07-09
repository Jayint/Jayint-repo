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

import re

# Strong, distinguishing error tokens — specific enough to trust a block split on.
_MOD = re.compile(r"No module named ['\"]([\w.]+)['\"]")
_FATAL = re.compile(r"fatal error: *([^\n]+)")
_IMPORT = re.compile(r"(ImportError: [^\n]+)")
_PIPERR = re.compile(r"(ERROR: [^\n]+)")
_HDR_FAIL = re.compile(r"^BUILD FAILED at `([^`]*)`")
_HDR_OK = re.compile(r"^BUILD OK\. TESTS (\d+)/(\d+)")

_SPECIFIC_MARKERS = ("No module named", "fatal error:", "ImportError:")


def _detail(obs: str) -> str | None:
    """The most distinguishing failure token in *obs*, or None."""
    m = _MOD.search(obs)
    if m:
        return f"No module named '{m.group(1)}'"
    m = _FATAL.search(obs)
    if m:
        return f"fatal error: {m.group(1).strip()}"[:90]
    m = _IMPORT.search(obs)
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
    return bool(sig) and any(marker in sig for marker in _SPECIFIC_MARKERS)


def _short(sig: str) -> str:
    """A compact reference to a blocker for an inline note."""
    m = _MOD.search(sig)
    if m:
        return f"'{m.group(1)}'"
    m = _FATAL.search(sig)
    if m:
        return m.group(1).split(":")[0].strip()
    return sig.split(" → ")[-1][:40]


_PAT_PATCH = re.compile(r"^patch v(\d+)(?: \((.*)\))? → (.+)$")
_PAT_BASE = re.compile(r"^baseline → (.+)$")
_PAT_EXPLORE = re.compile(r"^explore: (.+)$")


def render_history(steps) -> str:
    if not steps:
        return "HISTORY — (no prior steps yet)"
    lines = ["HISTORY — chronological; a BLOCKER header marks where the failure signature changed:"]
    prev_sig = "\x00"                                # sentinel: nothing seen yet
    block_no = 0
    failed_deltas: list[str] = []

    def open_block(sig, context):
        nonlocal block_no, failed_deltas
        block_no += 1
        shown = sig if sig is not None else "(build meets the gate — no blocker)"
        lines.append(f"[{block_no}] BLOCKER: {shown}   ({context})")
        failed_deltas = []

    for st in steps:
        summ = st.action_summary or ""

        me = _PAT_EXPLORE.match(summ)
        if me:
            lines.append(f"      explore: {me.group(1)}")
            continue

        mb = _PAT_BASE.match(summ)
        if mb:
            prev_sig = extract_blocker(st.observation_raw)
            open_block(prev_sig, f"baseline: {mb.group(1)}")
            continue

        mp = _PAT_PATCH.match(summ)
        if not mp:
            lines.append("      (invalid move — re-prompted)")
            continue

        ver, change, score = mp.group(1), mp.group(2), mp.group(3)
        change_s = f"({change}) " if change else ""
        sig = extract_blocker(st.observation_raw)

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
            open_block(sig, f"surfaced after v{ver}")
            prev_sig = sig
            continue
        # low-confidence change (weak signature on one side) → do NOT split; note inline
        lines.append(f"      v{ver} {change_s}→ {score}   error changed (signature uncertain)")
        prev_sig = sig

    if failed_deltas:                               # still-open blocker: consolidate what didn't help
        lines.append(f"      ↳ already tried for this blocker (didn't help): {', '.join(failed_deltas)}")
    return "\n".join(lines)
