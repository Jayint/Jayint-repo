"""Structural compaction of pytest ERROR/FAILURE blocks — dedup by CAUSE, not by size.

Why this exists (found by reading a real live prompt, not a test):

    ── LAST RUN (full — the state you are acting on) ──
    BUILD OK. TESTS 0/5 passed.
    ==================================== ERRORS ====================================
    ... 62 lines, 5 near-identical collection tracebacks ...

3,532 chars, and exactly ONE unique piece of information in the whole block:
``ModuleNotFoundError: No module named 'itsdangerous'``. Nothing compressed it, because
``safety_compress_observation`` is SIZE-gated (3.5k < the 8k threshold) — and a few-KB pytest
failure is the single most common observation this arm ever sees. So the size gate guarantees the
compressor never fires on exactly the output that needs it most.

Two transforms, both content-aware and both UNCONDITIONAL (like the noise strip, unlike the
size-gated selection pass):

  1. **Boilerplate frames.** A collection error's traceback always walks through importlib's
     bootstrap (``_bootstrap._gcd_import``), which tells the model nothing — the interesting frame
     is the test module's own import line. Dropped, along with the fixed "Hint: make sure your test
     modules..." line pytest prints on every single one.
  2. **Same-cause blocks.** N test modules that fail to import for ONE reason produce N structurally
     identical blocks. Keep the first VERBATIM (real stdout, whose-words intact — this is not a
     synthesized histogram), collapse the rest to a one-line roster of which modules shared it.

This also defuses ``_collect_safety_indices``'s 12-error-block cap: a flood of identical errors used
to exhaust it and push a genuinely DIFFERENT buried cause out of the prompt. Dedup by cause means
the cap now counts causes, not copies.

Pure. No I/O, no LLM, no synthesis — every line emitted (bar the roster) is a line pytest printed.
"""
from __future__ import annotations

import re

# pytest delimits each failure/error with a rule of underscores around a title:
#   __________ ERROR collecting tests/test_encoding.py ___________
#   _______________________ test_sign_bad_key ________________________
_BLOCK_RULE = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}\s*$")
# The section banners that open/close the FAILURES / ERRORS regions ("==== ERRORS ====").
_SECTION_RULE = re.compile(r"^=+\s+.*\s+=+\s*$")

# Frames that are pure plumbing in an import-time collection error. The importlib bootstrap appears
# in EVERY one of them and localizes nothing; the caret line belongs to it, not to user code.
_BOILERPLATE = (
    re.compile(r"^Hint: make sure your test modules/packages have valid Python names\.\s*$"),
    re.compile(r"^ImportError while importing test module .*\.\s*$"),
    re.compile(r"^Traceback:\s*$"),
    re.compile(r"^.*[/\\]importlib[/\\]__init__\.py:\d+: in import_module\s*$"),
    re.compile(r"^\s*return _bootstrap\._gcd_import\(.*\)\s*$"),
)
# A caret pointer (`^^^^^^`) is meaningful under user code (it points at the failing expression) but
# noise under a dropped bootstrap frame — so it is only removed when the line above it was dropped.
_CARETS = re.compile(r"^\s*\^{3,}\s*$")

# The line that actually names the failure. pytest prefixes it with `E ` in every traceback style.
_E_LINE = re.compile(r"^E\s+(.*\S)\s*$")
# Volatile bits of a cause that differ per test module but mean the same thing (paths, line numbers,
# memory addresses) — normalized away so "same cause" isn't defeated by a differing filename.
_VOLATILE = (
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),
    (re.compile(r"[\w./\\-]+\.py:\d+"), "FILE:LINE"),
    (re.compile(r"\s+"), " "),
)


def _drop_boilerplate(lines: "list[str]") -> "list[str]":
    """Remove importlib bootstrap plumbing (and the caret line that belongs to it) from a block."""
    out: list[str] = []
    dropped_prev = False
    for ln in lines:
        if any(p.match(ln) for p in _BOILERPLATE):
            dropped_prev = True
            continue
        if dropped_prev and _CARETS.match(ln):
            continue                                  # the carets pointed at the frame we just dropped
        dropped_prev = False
        out.append(ln)
    return out


def _cause_key(lines: "list[str]") -> "str | None":
    """The normalized failure cause of a block — its LAST ``E ...`` line, path/line-number-agnostic.
    None when the block has no ``E`` line at all (then it is never deduped: we don't know what it is,
    so we keep it whole rather than risk collapsing two different failures together)."""
    e_lines = [m.group(1) for m in (_E_LINE.match(ln) for ln in lines) if m]
    if not e_lines:
        return None
    key = e_lines[-1]
    for pat, repl in _VOLATILE:
        key = pat.sub(repl, key)
    return key.strip()


_ROSTER_CAP = 8                                       # modules named in a collapsed group


def _title_short(title: str) -> str:
    """`ERROR collecting tests/pkg/test_signer.py` → `tests/pkg/test_signer.py`; a test-id title is
    left alone. Just enough to make the collapsed roster readable."""
    m = re.match(r"^ERROR collecting\s+(.*)$", title)
    return m.group(1).strip() if m else title.strip()


def _cause_headline(key: str) -> str:
    """The cause, trimmed for the one-line roster."""
    return key if len(key) <= 90 else key[:87].rstrip() + "…"


def compact_pytest_blocks(text: str) -> str:
    """Collapse structurally-identical pytest ERROR/FAILURE blocks and strip import boilerplate.

    The first block of each distinct cause survives VERBATIM (minus boilerplate frames). Every later
    block with the same cause is replaced — the whole group, once — by a roster line naming the
    modules that shared it. Text outside any block (banners, the short-summary section, pytest's own
    tallies) passes through untouched. Idempotent; a no-op on output with 0 or 1 blocks."""
    if not text or "_" not in text:
        return text
    lines = text.splitlines()

    # Partition into a prologue + a list of (title, body_lines). A `====` section banner CLOSES the
    # current block (that's how pytest ends FAILURES/ERRORS and opens "short test summary info").
    prologue: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    epilogue: list[str] = []
    cur: "list[str] | None" = None
    for ln in lines:
        m = _BLOCK_RULE.match(ln)
        if m:
            cur = []
            blocks.append((m.group(1), cur))
            continue
        if cur is not None and _SECTION_RULE.match(ln):
            cur = None                                # block region closed — back to flat text
            epilogue.append(ln)
            continue
        (cur if cur is not None else (epilogue if blocks else prologue)).append(ln)

    if len(blocks) < 2:                               # nothing to dedup — still strip boilerplate
        if not blocks:
            return text
        title, body = blocks[0]
        kept = _drop_boilerplate(body)
        return "\n".join([*prologue, _rule(title), *kept, *epilogue])

    # Group by cause, preserving first-seen order. A block with no `E` line gets a unique key so it
    # is never merged with anything else.
    order: list[str] = []
    groups: dict[str, list[tuple[str, list[str]]]] = {}
    for i, (title, body) in enumerate(blocks):
        key = _cause_key(body) or f"\x00unique-{i}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((title, body))

    out = list(prologue)
    for key in order:
        members = groups[key]
        title, body = members[0]
        out.append(_rule(title))
        out.extend(_drop_boilerplate(body))
        rest = members[1:]
        if not rest:
            continue
        # Cap the roster too. On a 120-module flood, spelling out every filename just trades one kind
        # of noise for another — the agent needs the CAUSE and a sense of scale, not the census.
        shown = [_title_short(t) for t, _ in rest[:_ROSTER_CAP]]
        if len(rest) > _ROSTER_CAP:
            shown.append(f"(+{len(rest) - _ROSTER_CAP} more)")
        noun = "block" if len(rest) == 1 else "blocks"
        out.append("")
        out.append(f"… {len(rest)} more {noun}, same cause ({_cause_headline(key)}):")
        out.append(f"    {', '.join(shown)}")
        out.append("")
    out.extend(epilogue)
    return "\n".join(out)


def _rule(title: str) -> str:
    """Re-emit a block title as a pytest-style underscore rule, padded to pytest's 80 columns."""
    pad = max(3, (78 - len(title)) // 2)
    return f"{'_' * pad} {title} {'_' * pad}"
