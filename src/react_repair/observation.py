"""Content-aware observation compression for the react arm's explore output.

Ported from the radical baseline's ``src/observation_compressor.py`` (the deterministic
``safety_compress_observation`` path only — no LLM). The react arm previously showed an explore's
output as a flat 200-char head (``history_view._explore_finding``), which blinded the agent to the
files it read (it re-``cat``-ed the same file 16× on addons-server because it never saw past line 4).
This keeps the SIGNAL — error lines, install status, meaningful head+tail, error-blocks — and drops
NOISE (download/apt progress), within a char budget. Pure; no external dependencies."""
from __future__ import annotations

import re

SAFETY_COMPRESSION_NOTE = "... (repetitive output omitted by safety compression) ..."
SAFETY_TRUNCATION_NOTE = (
    "\n... (safety-compressed output truncated to stay within prompt budget) ..."
)

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
)

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


def _render_safety_segments(
    lines: list[str],
    selected_indices: list[int],
    original_chars: int,
    threshold_chars: int,
) -> str:
    if not selected_indices:
        return "\n".join(lines)

    rendered = [
        "[Safety Compression Applied]",
        f"Original observation length: {original_chars} chars (threshold: {threshold_chars}).",
        "",
    ]

    segment_start = selected_indices[0]
    segment_end = selected_indices[0]
    for index in selected_indices[1:]:
        if index == segment_end + 1:
            segment_end = index
            continue
        rendered.extend(lines[segment_start : segment_end + 1])
        omitted_count = index - segment_end - 1
        if omitted_count > 0:
            rendered.append(f"... ({omitted_count} lines omitted by safety compression) ...")
        segment_start = index
        segment_end = index

    rendered.extend(lines[segment_start : segment_end + 1])
    rendered.append("")
    rendered.append(SAFETY_COMPRESSION_NOTE)
    return "\n".join(rendered).strip()


def _truncate_safety_output(text: str, target_chars: int) -> str:
    if len(text) <= target_chars:
        return text
    if target_chars <= len(SAFETY_TRUNCATION_NOTE) + 20:
        return text[:target_chars]
    remaining = target_chars - len(SAFETY_TRUNCATION_NOTE)
    head_chars = remaining // 2
    tail_chars = remaining - head_chars
    return text[:head_chars].rstrip() + SAFETY_TRUNCATION_NOTE + text[-tail_chars:].lstrip()


def safety_compress_observation(
    observation_raw: str | None,
    threshold_chars: int = 200_000,
    target_chars: int = 20_000,
) -> tuple[str, bool]:
    """Return ``(text_for_prompt, applied)``. If the raw output is at/under *threshold_chars* it is
    returned verbatim (``applied=False``) — small outputs reach the agent whole. Otherwise, keep the
    meaningful head+tail + status lines + error-blocks (dropping progress noise), then hard-cap to
    *target_chars* (head/tail split with a note). ``applied`` is True iff the result differs."""
    text = observation_raw or ""
    if len(text) <= threshold_chars:
        return text, False

    lines = text.splitlines()
    if not lines:
        return _truncate_safety_output(text, target_chars), True

    selected = _collect_safety_indices(lines, head_limit=24, tail_limit=80)
    compressed = _render_safety_segments(lines, selected, len(text), threshold_chars)

    if len(compressed) > target_chars:                      # tighten a second pass if still over
        selected = _collect_safety_indices(lines, head_limit=12, tail_limit=40, max_error_blocks=8)
        compressed = _render_safety_segments(lines, selected, len(text), threshold_chars)

    compressed = _truncate_safety_output(compressed, target_chars)
    return compressed, compressed != text
