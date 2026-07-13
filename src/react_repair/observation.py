"""Content-aware observation compression for the react arm's explore output.

Ported from the radical baseline's ``src/observation_compressor.py`` (the deterministic
``safety_compress_observation`` path only — no LLM). The react arm previously showed an explore's
output as a flat 200-char head (``history_view._explore_finding``), which blinded the agent to the
files it read (it re-``cat``-ed the same file 16× on addons-server because it never saw past line 4).
This keeps the SIGNAL — error lines, install status, meaningful head+tail, error-blocks — and drops
NOISE (download/apt progress), within a char budget. Pure; no external dependencies."""
from __future__ import annotations

import re

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
