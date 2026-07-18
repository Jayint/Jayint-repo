"""What the react agent SEES — the observe cluster (spec §4A consolidation).

One file for the pure transforms that turn a raw build/test/explore run into the text the agent
reads next turn. Folded together (§4A) from four single-concept modules, each preserved below under
its own section banner:

  * observation  — content-aware compression: ``safety_compress_observation`` (noise strip + size-
    gated head/tail/error-block selection) + ``strip_pip_progress``. The core budget compaction.
  * envelope     — the ``$ cmd → result`` envelope (``run_envelope``), edit tool-results
    (``edit_result``), and the legacy-header strip (``strip_legacy_header``).
  * pytest_blocks — identical pytest error blocks collapsed by cause (``compact_pytest_blocks``).
  * pytest_summary — the ranked pytest cause histogram (``summarize`` + ``format_breakdown``, ``Cause``).

All pure, no external dependencies, no env lever — so the observe golden pins them byte-for-byte.
(The graph-side ``diagnose`` is deliberately NOT here — R6 keeps it in graph.)"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The ONLY synthesized line we emit: an honest count wherever real content was dropped. No
# "[Safety Compression Applied]" preamble, no trailing note — those were meta-chatter about the
# compressor itself, which told the model nothing and ate prompt budget.
def _elision(n: int) -> str:
    return f"… ({n} line{'' if n == 1 else 's'} omitted) …"


_ELISION_LEN = len("… (999 lines omitted) …")     # budget reservation for the marker

# Lines that are pure progress/transport noise — dropped unless they also match a STATUS pattern.
SAFETY_NOISE_PATTERNS = (
    r"^\s*Progress \(\d+\):",
    r"^\s*\d+K (?:\.+\s+)+\d+%.*$",
    r"^\s*Downloading from ",
    r"^\s*Downloaded from ",
    r"^\s*Get:\d+\s",
    r"^\s*Hit:\d+\s",
    r"^\s*Ign:\d+\s",
    r"^\s*Fetched\s",
    r"^\s*Resolving deltas:",
    r"^\s*Receiving objects:",
    r"^\s*remote:",
    # Harness plumbing, never model-facing: the ERR-trap sentinel the sandbox emits so the HOST can
    # recover (failing_command, lineno) — see sandbox._parse_install_failure. The host parses it; the
    # agent must never see it (it leaked verbatim into a live prompt).
    r"^\s*__INSTALL_FAIL__",
)

# pip's transport chatter. Deliberately NOT in SAFETY_NOISE_PATTERNS: that list is always-on for
# every caller, and the default (classic) observation path is the A/B's control arm — it must stay
# byte-identical to what shipped. So this is a separate, opt-in strip used only by the agentic body
# renderer, and it bundles with that lever.
#
# Everything here carries ZERO diagnostic information: which wheel was fetched, how fast, and the
# root-user nag pip prints on literally every container run. What is NOT here is deliberate:
# `Successfully installed X-2.1` (says which version actually landed) and `Requirement already
# satisfied: X` (says the package IS present) both survive — they answer real questions.
PIP_PROGRESS_PATTERNS = (
    r"^\s*Collecting\s",
    r"^\s*Downloading\s",
    r"^\s*Using cached\s",
    r"^\s*[━╸]+",                                    # the rich progress bar
    r"^\s*Installing collected packages:",           # redundant with "Successfully installed"
    r"^\s*Attempting uninstall:",
    r"^\s*Found existing installation:",
    r"^\s*Uninstalling\s",
    r"^\s*Successfully uninstalled\s",               # pip internals; `Successfully installed` is the signal
    r"^\s*Preparing metadata\s",
    r"^\s*Building wheel\s",
    r"^\s*Created wheel\s",
    r"^\s*Stored in directory:",
    r"^WARNING: Running pip as the 'root' user",     # printed on every single container run
    r"^\s*\[notice\] A new release of pip",
    r"^\s*\[notice\] To update, run:",
)


def strip_pip_progress(text: str) -> str:
    """Drop pip's transport chatter (see PIP_PROGRESS_PATTERNS). Pure; returns *text* unchanged when
    nothing matched, so a non-pip observation is byte-for-byte untouched."""
    if not text:
        return text
    lines = text.splitlines()
    kept = [ln for ln in lines if not _matches_any_pattern(ln, PIP_PROGRESS_PATTERNS)]
    if len(kept) == len(lines):
        return text
    return "\n".join(kept)


# Outcome/summary lines worth keeping verbatim (package installs, test tallies, build results).
SAFETY_STATUS_PATTERNS = (
    r"\bBUILD SUCCESS\b",
    r"\bBUILD FAILURE\b",
    r"^Total time:",
    r"^Finished at:",
    r"^Reactor Summary",
    r"^Results ?:",
    r"^FAILURES?:",
    r"short test summary info",
    r"collected \d+ items",
    r"\b\d+ passed\b",
    r"\b\d+ failed\b",
    r"\bxfailed\b",
    r"Successfully installed",
    r"Requirement already satisfied",
    r"already satisfied",
    r"^The following NEW packages will be installed",
    r"^The following packages will be upgraded",
    r"^Need to get ",
    r"^After this operation",
    r"saved \[",
)

SAFETY_ERROR_PATTERNS = (
    r"^\s*\[ERROR\]",
    r"\btraceback\b",
    r"\bexception\b",
    r"\bcommand not found\b",
    r"\bno such file or directory\b",
    r"\bfailed\b",
    r"\berror\b",
)

# (pattern, how-many-following-lines-to-keep) — the block after a summary/error header carries the
# actual detail, so grab a run of lines below it.
SAFETY_BLOCK_PATTERNS = (
    (r"^Reactor Summary", 12),
    (r"short test summary info", 20),
    (r"^FAILURES?:", 20),
    (r"^Results ?:?", 12),
    (r"^\s*\[ERROR\]", 8),
)


def _matches_any_pattern(line: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns)


def _is_safety_noise_line(line: str) -> bool:
    return _matches_any_pattern(line, SAFETY_NOISE_PATTERNS)


def _is_safety_status_line(line: str) -> bool:
    return _matches_any_pattern(line, SAFETY_STATUS_PATTERNS)


def _is_safety_error_line(line: str) -> bool:
    return _matches_any_pattern(line, SAFETY_ERROR_PATTERNS)


def _take_meaningful_head_indices(lines: list[str], limit: int) -> list[int]:
    indices: list[int] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if _is_safety_noise_line(line) and not _is_safety_status_line(line):
            continue
        indices.append(index)
        if len(indices) >= limit:
            break
    if not indices:
        return list(range(min(limit, len(lines))))
    return indices


def _take_meaningful_tail_indices(lines: list[str], limit: int) -> list[int]:
    indices: list[int] = []
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.strip():
            continue
        if _is_safety_noise_line(line) and not _is_safety_status_line(line):
            continue
        indices.append(index)
        if len(indices) >= limit:
            break
    if not indices:
        return list(range(max(0, len(lines) - limit), len(lines)))
    return list(reversed(indices))


def _collect_following_block(lines: list[str], start_index: int, max_lines: int) -> set[int]:
    end_index = min(len(lines), start_index + max_lines)
    block_indices: set[int] = set()
    for index in range(start_index, end_index):
        line = lines[index]
        block_indices.add(index)
        if index > start_index and not line.strip():
            break
    return block_indices


def _collect_safety_indices(
    lines: list[str],
    head_limit: int,
    tail_limit: int,
    max_error_blocks: int = 12,
) -> list[int]:
    selected = set(_take_meaningful_head_indices(lines, head_limit))
    selected.update(_take_meaningful_tail_indices(lines, tail_limit))

    error_blocks = 0
    for index, line in enumerate(lines):
        if _is_safety_status_line(line):
            selected.add(index)
        for pattern, block_len in SAFETY_BLOCK_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                selected.update(_collect_following_block(lines, index, block_len))
                break
        if error_blocks < max_error_blocks and _is_safety_error_line(line):
            start = max(0, index - 2)
            end = min(len(lines), index + 5)
            selected.update(range(start, end))
            error_blocks += 1

    if not selected:
        selected.update(range(min(len(lines), head_limit + tail_limit)))

    return sorted(selected)


def _strip_noise_lines(text: str) -> str:
    """Pass 1 — ALWAYS ON. Drop pure progress/transport chatter and harness sentinels. This pass can
    only ever remove junk, so (unlike the SELECTION pass below, which drops real content) there is no
    reason to gate it on size. The old size-gate meant a small observation reached the model with every
    noise line intact — which is exactly what a live run showed. A noise line that ALSO matches a
    STATUS pattern is kept (status wins)."""
    lines = text.splitlines()
    kept = [ln for ln in lines
            if not (_is_safety_noise_line(ln) and not _is_safety_status_line(ln))]
    if len(kept) == len(lines):
        return text                      # nothing dropped → preserve the original byte-for-byte
    return "\n".join(kept)


def _render_safety_segments(lines: list[str], selected_indices: list[int]) -> str:
    """Stitch the selected lines back together, marking each gap with an honest elision count. No
    preamble, no trailer — the model gets the real output plus `… (N lines omitted) …` where content
    was dropped, and nothing about the compressor itself."""
    if not selected_indices:
        return "\n".join(lines)

    rendered: list[str] = []
    segment_start = segment_end = selected_indices[0]
    for index in selected_indices[1:]:
        if index == segment_end + 1:
            segment_end = index
            continue
        rendered.extend(lines[segment_start : segment_end + 1])
        omitted = index - segment_end - 1
        if omitted > 0:
            rendered.append(_elision(omitted))
        segment_start = segment_end = index

    rendered.extend(lines[segment_start : segment_end + 1])
    return "\n".join(rendered).strip()


def _truncate_safety_output(text: str, target_chars: int) -> str:
    """Hard-cap to *target_chars*, cutting on LINE boundaries — never mid-token. (The old character
    splice produced garbage like `…can result in brok` + `-26.2 pluggy-1.6.0`.) Keeps a head and a
    tail with an honest elision count between them."""
    if len(text) <= target_chars:
        return text
    lines = text.splitlines()
    budget = max(1, target_chars - _ELISION_LEN - 10)        # room for the elision marker
    half = max(1, budget // 2)

    head: list[str] = []
    used_h = 0
    for ln in lines:
        if used_h + len(ln) + 1 > half:
            break
        head.append(ln)
        used_h += len(ln) + 1

    tail: list[str] = []
    used_t = 0
    for ln in reversed(lines[len(head):]):
        if used_t + len(ln) + 1 > budget - used_h:
            break
        tail.insert(0, ln)
        used_t += len(ln) + 1

    omitted = len(lines) - len(head) - len(tail)
    body = head + ([_elision(omitted)] if omitted > 0 else []) + tail
    out = "\n".join(body)
    return out if out.strip() else text[:target_chars]      # one pathologically long line → char cut


def safety_compress_observation(
    observation_raw: str | None,
    threshold_chars: int = 200_000,
    target_chars: int = 20_000,
) -> tuple[str, bool]:
    """Return ``(text_for_prompt, applied)``. TWO passes, deliberately gated differently:

    1. **NOISE STRIP — always on.** Progress/transport chatter + harness sentinels are dropped
       regardless of size: removing junk never costs signal, so size-gating it was a bug (a small
       observation reached the model with every noise line intact).
    2. **SELECTION — size-gated.** Only when the stripped text still exceeds *threshold_chars* do we
       keep head+tail+status+error-blocks and drop the middle. That pass CAN drop real content, so it
       runs only when the budget demands it. Then a LINE-BOUNDARY hard cap to *target_chars*.

    ``applied`` is True iff the result differs from the raw input."""
    raw = observation_raw or ""
    text = _strip_noise_lines(raw)                           # pass 1 — unconditional
    if len(text) <= threshold_chars:
        return text, text != raw

    lines = text.splitlines()
    if not lines:
        return _truncate_safety_output(text, target_chars), True

    selected = _collect_safety_indices(lines, head_limit=24, tail_limit=80)
    compressed = _render_safety_segments(lines, selected)

    if len(compressed) > target_chars:                       # tighten a second pass if still over
        selected = _collect_safety_indices(lines, head_limit=12, tail_limit=40, max_error_blocks=8)
        compressed = _render_safety_segments(lines, selected)

    compressed = _truncate_safety_output(compressed, target_chars)
    return compressed, compressed != raw


# ======================================================================================
# envelope — folded from envelope.py (§4A): the $cmd→result envelope + edit tool-results.
#
# Agentic observation envelope — command → result, the unit of a real agent transcript.
#
# The classic observation opens with a synthesized verdict:
#
#     BUILD OK. TESTS 0/5 passed.
#
# Two things are wrong with that line, and this module fixes both.
#
# **It is not a tool result, it is a narrator.** An agent transcript's atom is `command → output`;
# ours shipped output with no command attached. The models are post-trained on the former. Worse, the
# single strangest fact about this arm — that the WHOLE script re-runs from a clean container every
# turn — had to be explained in three sentences of system prose because the transcript never showed
# it. Print the command and it explains itself:
#
#     $ bash setup.sh                      # fresh container from the base image — EVERY turn
#     exit 0
#
# **And "0/5 passed" is false.** That repo has 297 tests. Zero of them ran. The "5" is five *modules*
# that failed to import. `executed = passed + failed + errors` is the right GATE denominator (see
# gate.py) but rendering it as a ratio tells the model five tests exist and none passed, which is not
# a thing that happened. Report what pytest reported:
#
#     $ python -m pytest -q
#     0 passed, 0 failed, 5 collection errors — no tests ran
#
# Pure render layer. The canonical `Step.observation_raw` keeps its legacy header (extract_blocker and
# the do-not-retry ledger parse it, and the blob path still renders it), so this strips that header off
# and re-frames the same bytes from the structured outcome recorded alongside it.
#
# ======================================================================================

# The reset is the whole game and it was invisible. Say it where it happens, every time.
_RESET_NOTE = "# fresh container from the base image — EVERY turn"
_BUILD_CMD = "bash setup.sh"

# The legacy headers _observation() prepends to the stored body. Stripped before re-framing so the
# same fact isn't stated twice, in two voices.
_LEGACY_OK = re.compile(r"^BUILD OK\. TESTS \d+/\d+ passed[^\n]*\n?")
_LEGACY_FAIL = re.compile(r"^BUILD FAILED at `[^`]*`(?: \(line \d+\))?:\n?")


def strip_legacy_header(observation: "str | None") -> str:
    """The stored observation minus its synthesized `BUILD OK …` / `BUILD FAILED at …` header line."""
    text = observation or ""
    text = _LEGACY_OK.sub("", text, count=1)
    text = _LEGACY_FAIL.sub("", text, count=1)
    return text.lstrip("\n")


def _pytest_counts(o: dict) -> str:
    """What pytest actually reported — its own counts, never fused into a ratio.

    No exit code is printed: we never captured pytest's rc (TestOutcome.ok is the HOST's 80% gate
    verdict, not pytest's return value), and inventing one would be the same sin as the fake ratio."""
    passed, failed = int(o.get("passed", 0)), int(o.get("failed", 0))
    errors, skipped = int(o.get("errors", 0)), int(o.get("skipped", 0))
    collected = int(o.get("collected", 0))
    parts = [f"{passed} passed", f"{failed} failed"]
    if errors:
        parts.append(f"{errors} collection error{'' if errors == 1 else 's'}")
    if skipped:
        parts.append(f"{skipped} skipped")
    line = ", ".join(parts)
    if passed + failed == 0:
        return line + " — no tests ran"
    if collected > passed + failed:               # the silent-skip gap the old header tried to show
        line += f" — {collected} tests collected"
    return line


def run_envelope(outcome: "dict | None") -> str:
    """The `$ command → result` header for a build/test observation, from the structured outcome.

    Build failed  → the script's exit + the line it halted on (the numbered script marks the same line).
    Build green   → exit 0, then the pytest command and pytest's own counts."""
    o = outcome or {}
    head = f"$ {_BUILD_CMD}{' ' * 10}{_RESET_NOTE}"
    if not o.get("build_ok", False):
        cmd = o.get("failing_command") or "(unknown command)"
        lineno = o.get("lineno")
        where = f" at line {lineno}" if lineno else ""
        return f"{head}\nexit 1 — halted{where}: `{cmd}`"
    test_cmd = o.get("test_command") or "python -m pytest -q"
    if not o.get("ran_tests", False):
        return f"{head}\nexit 0"
    return f"{head}\nexit 0\n\n$ {test_cmd}\n{_pytest_counts(o)}"


_EDIT_ECHO_LINES = 6                                  # cap on the lines echoed back from a splice


def edit_result(action: "dict | None") -> "str | None":
    """The tool RESULT of an `edit` call — what a real file-editing tool returns: confirmation the
    splice landed, and the lines it landed on. None for non-edit actions.

    This is the only honest home for line numbers. A past assistant turn reading `edit(replace @7-8)`
    is unresolvable later — after one insert, line 7 is a different line, and only the CURRENT script
    is ever shown, so there is nothing in the prompt to resolve the number against. Here the numbers
    sit next to the content they produced, at the moment they were true."""
    if not action or action.get("kind") != "edit":
        return None
    verb, start = action.get("verb"), action.get("start")
    end = action.get("end", start)
    if verb == "delete":
        span = f"line {start}" if end == start else f"lines {start}-{end}"
        return f"setup.sh updated — deleted {span}."
    content = (action.get("content") or "").splitlines()
    if not content or not isinstance(start, int):
        return "setup.sh updated."
    shown = [f"  {start + i}| {ln}" for i, ln in enumerate(content[:_EDIT_ECHO_LINES])]
    if len(content) > _EDIT_ECHO_LINES:
        shown.append(f"  … (+{len(content) - _EDIT_ECHO_LINES} more lines)")
    return "setup.sh updated:\n" + "\n".join(shown)


# ======================================================================================
# pytest_blocks — folded from pytest_blocks.py (§4A): identical error blocks collapsed by cause.
#
# Structural compaction of pytest ERROR/FAILURE blocks — dedup by CAUSE, not by size.
#
# Why this exists (found by reading a real live prompt, not a test):
#
#     ── LAST RUN (full — the state you are acting on) ──
#     BUILD OK. TESTS 0/5 passed.
#     ==================================== ERRORS ====================================
#     ... 62 lines, 5 near-identical collection tracebacks ...
#
# 3,532 chars, and exactly ONE unique piece of information in the whole block:
# ``ModuleNotFoundError: No module named 'itsdangerous'``. Nothing compressed it, because
# ``safety_compress_observation`` is SIZE-gated (3.5k < the 8k threshold) — and a few-KB pytest
# failure is the single most common observation this arm ever sees. So the size gate guarantees the
# compressor never fires on exactly the output that needs it most.
#
# Two transforms, both content-aware and both UNCONDITIONAL (like the noise strip, unlike the
# size-gated selection pass):
#
#   1. **Boilerplate frames.** A collection error's traceback always walks through importlib's
#      bootstrap (``_bootstrap._gcd_import``), which tells the model nothing — the interesting frame
#      is the test module's own import line. Dropped, along with the fixed "Hint: make sure your test
#      modules..." line pytest prints on every single one.
#   2. **Same-cause blocks.** N test modules that fail to import for ONE reason produce N structurally
#      identical blocks. Keep the first VERBATIM (real stdout, whose-words intact — this is not a
#      synthesized histogram), collapse the rest to a one-line roster of which modules shared it.
#
# This also defuses ``_collect_safety_indices``'s 12-error-block cap: a flood of identical errors used
# to exhaust it and push a genuinely DIFFERENT buried cause out of the prompt. Dedup by cause means
# the cap now counts causes, not copies.
#
# Pure. No I/O, no LLM, no synthesis — every line emitted (bar the roster) is a line pytest printed.
#
# ======================================================================================

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


# ======================================================================================
# pytest_summary — folded from pytest_summary.py (§4A): the ranked pytest cause histogram.
#
# Turn pytest's FAILURES/ERRORS traceback sections into a ranked cause histogram.
#
# Once the build is green the fault is no longer a setup.sh LINE — it's whatever the tests can't
# import/find. This module aggregates each failing/erroring test by (exception type + normalized
# message) so the agent sees the DOMINANT cause and how many tests it blocks, instead of a tail that
# happens to show the last — not the biggest — failure (anthropic-sdk: 430 aiohttp shown, 1448
# ConnectionError hidden).
#
# Parsing is BLOCK-based, over the real pytest output (verified against an actual run), NOT the short
# `-ra` summary — because for the dominant case, COLLECTION errors, the summary line ("ERROR path.py")
# carries no reason at all; the cause (`ModuleNotFoundError: ...`) lives only in the traceback block.
# pytest emits those blocks by default, so no extra reporting flag is needed. Each block runs from a
# `___ title ___` banner to the next banner or `===` section line; its cause is the exception on the
# last `E   ExcType: msg` line, else the `path:line: ExcType` traceback terminator. Pure, no I/O.
# The top cause doubles as a stable failure signature (a future novelty-based stall can reuse it).
#
# ======================================================================================

_EXC_SUFFIX = r"(?:Error|Exception|Warning|Timeout|Failed|Interrupted|Skipped)"
# An exception type name: an optional dotted prefix then a suffix. The prefix is OPTIONAL so a bare
# suffix-only name is captured too — pytest-timeout raises "Failed", pytest.fail raises "Failed",
# and dotted names like "requests.exceptions.ConnectionError" keep their module path.
_EXC_NAME = r"((?:[A-Za-z_][\w.]*)?" + _EXC_SUFFIX + r")"

# A pytest block banner: a run of underscores, a title, a run of underscores ("___ test_x ___",
# "___ ERROR collecting a.py ___"). The `===`-delimited section headers use "=" and never match.
_BANNER = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}\s*$")
_SECTION = re.compile(r"^={3,}")
# "E   ModuleNotFoundError: No module named 'x'" — the exception on a marked traceback line (has msg).
_E_EXC = re.compile(r"^E\s+" + _EXC_NAME + r"\b:?\s*(.*)$")
# "tests/test_x.py:2: AssertionError" — the traceback terminator (type, usually no msg). Excludes
# frame lines like "tests/x.py:1: in <module>" because "in <module>" doesn't end in an exc suffix.
_LOC_EXC = re.compile(r"^\S+:\d+:\s+" + _EXC_NAME + r"\b\s*(.*)$")
_PYFILE = re.compile(r"([\w./+-]+\.py)")

# pytest's block banners name the phase. A COLLECTION error is per-FILE (the tests inside
# were never created as items); a setup/teardown error is per-TEST (the test WAS collected,
# its fixture broke); a failure is the test body itself. All three "ERROR" banners start with
# the same word, which is why bucketing on `startswith("ERROR")` conflated them.
_PHASE_PREFIXES = (
    ("ERROR collecting", "collect"),
    ("ERROR at setup of", "setup"),
    ("ERROR at teardown of", "teardown"),
)


def _phase_of(title: str) -> str:
    for prefix, phase in _PHASE_PREFIXES:
        if title.startswith(prefix):
            return phase
    return "call"


@dataclass(frozen=True)
class Cause:
    exc: str          # exception type, e.g. "ModuleNotFoundError"
    detail: str       # representative RAW message (first seen), for display ("" if none)
    count: int        # blocks affected: MODULES for phase="collect", TESTS otherwise (see below)
    outcome: str      # "ERROR" | "FAILED" — which pytest SECTION the block appeared under
    module: str       # a representative file (first seen)
    phase: str = "call"   # "collect" | "setup" | "call" | "teardown" — the pytest phase

    # `outcome` and `phase` are INDEPENDENT and neither may be derived from the other.
    # pytest builds the banner as f"ERROR at {rep.when} of ..." for anything its `error`
    # category owns, and a plugin can put a CALL report in that category via
    # pytest_report_teststatus — so `ERROR at call of test_x` is a real, valid banner with
    # outcome="ERROR" AND phase="call". Deriving outcome from phase silently flips it.


def _blocks(output: str):
    """Yield (title, body_lines) for each `___ title ___` block, stopping the body at the next
    banner or `===` section boundary so the short-summary/totals never leak into a block."""
    title, body = None, []
    for line in (output or "").splitlines():
        m = _BANNER.match(line)
        if m:
            if title is not None:
                yield title, body
            title, body = m.group(1), []
        elif _SECTION.match(line):
            if title is not None:
                yield title, body
            title, body = None, []
        elif title is not None:
            body.append(line)
    if title is not None:
        yield title, body


def _cause_of(body: list[str]) -> tuple[str | None, str]:
    """The block's exception: prefer the LAST `E   ExcType: msg` line (carries the message), else
    the LAST `path:line: ExcType` terminator. Returns (None, "") when the block names no exception."""
    exc, detail = None, ""
    for line in body:
        m = _E_EXC.match(line)
        if m:
            exc, detail = m.group(1), m.group(2).strip()
    if exc is not None:
        return exc, detail
    for line in body:
        m = _LOC_EXC.match(line)
        if m:
            exc, detail = m.group(1), (m.group(2) or "").strip()
    return exc, detail


def _module_of(title: str, body: list[str]) -> str:
    m = _PYFILE.search(title)
    if m:
        return m.group(1)
    for line in body:
        m = _PYFILE.search(line)
        if m:
            return m.group(1)
    return title


def _norm(detail: str) -> str:
    """Collapse volatile bits so the SAME cause groups: hex addresses and digit runs are masked
    (assert 3 == 4 / assert 5 == 6 → one cause), quoted module names are kept (the discriminator)."""
    d = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", detail)
    d = re.sub(r"\d+", "N", d)
    return d.strip()[:120]


def summarize(output: str) -> list[Cause]:
    """Parse pytest output → causes ranked by tests affected (desc). Groups blocks by (exc,
    normalized message); keeps the first-seen raw message + module for display. Returns [] when
    nothing failed or no traceback blocks are present."""
    groups: dict[tuple[str, str, str], dict] = {}
    for title, body in _blocks(output):
        exc, detail = _cause_of(body)
        if exc is None:
            continue
        phase = _phase_of(title)
        # Phase is part of the key: a ModuleNotFoundError at COLLECTION (an env problem with a
        # fix) and one raised inside a test body (residual logic) are different problems and
        # must not collapse into one Cause.
        key = (exc, _norm(detail), phase)
        g = groups.get(key)
        if g is None:
            groups[key] = {"exc": exc, "detail": detail, "count": 1,
                           "outcome": "ERROR" if title.startswith("ERROR") else "FAILED",
                           "module": _module_of(title, body), "phase": phase}
        else:
            g["count"] += 1
    causes = [Cause(**g) for g in groups.values()]
    causes.sort(key=lambda c: (-c.count, c.exc))
    return causes


def format_breakdown(causes: list[Cause], top: int = 5) -> str:
    """Render the ranked histogram (no header, no traceback tail): each row is pure triage —
    count + a `Cause.phase` tag (`[collect]`/`[setup]`/`[call]`/`[teardown]`) + exception type +
    message. Deliberately NO representative file path: for the dominant import-error case the
    actionable identifier is already in the message (the missing module), and naming a test file
    only lures the agent into `cat`-ing it (wasted navigation). The file stays on the Cause as
    metadata. Top-N rows + a one-line remainder so a long tail stays compact.

    The tag is `Cause.phase` directly: `[collect]` (an import/collection failure — fix the build by
    installing the missing thing) vs `[setup]`/`[teardown]` (a fixture blew up — the test WAS
    collected) vs `[call]` (an execution failure inside the test body — provide a service/config, or
    it's a residual the env can't fix). It tells the agent whether a row is a build problem or a
    runtime problem, and whether a run-phase problem is fixture plumbing or test logic — different
    repairs for each.

    LIMITATION: a `[collect]` row's `count` is MODULES affected, NOT tests affected — an unimportable
    module emits one collection-error block regardless of how many tests it hides, so `[collect]`
    rows under-rank. Recovering "blocks N tests" needs the hidden gold set (final-only) or the graph
    arm; do not attempt it here. The tag's diagnostic value is independent of the count."""
    rows = []
    for c in causes[:top]:
        detail = f": {c.detail}" if c.detail else ""
        rows.append(f"  {c.count} × [{c.phase}] {c.exc}{detail}")
    rest = causes[top:]
    if rest:
        rows.append(f"  …and {len(rest)} more cause(s) across {sum(c.count for c in rest)} tests")
    return "\n".join(rows)
