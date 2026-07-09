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


_PAT_PATCH = re.compile(r"^patch v(\d+)(?: \((.*)\))? → (.+)$")
_PAT_BASE = re.compile(r"^baseline → (.+)$")
_PAT_EXPLORE = re.compile(r"^explore: (.+)$")

# An explore's value is the FACT it surfaced, not just that it ran. Carry a compact, hard-capped
# digest of its output forward (the knowledge ledger) so the agent doesn't re-probe or reason
# blind — without dumping a file/listing into the prompt.
_EXPLORE_FINDING_CAP = 200


def _explore_finding(obs: str | None) -> str:
    """Whitespace-flattened, hard-capped head of an explore's output ("" when it produced none)."""
    if not obs:
        return ""
    flat = " ".join(obs.split())
    return flat if len(flat) <= _EXPLORE_FINDING_CAP else flat[:_EXPLORE_FINDING_CAP].rstrip() + " …"


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
            finding = _explore_finding(st.observation_raw)
            lines.append(f"      explored `{me.group(1)}`" + (f" → {finding}" if finding else ""))
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
