"""Turn pytest's FAILURES/ERRORS traceback sections into a ranked cause histogram.

Once the build is green the fault is no longer a setup.sh LINE — it's whatever the tests can't
import/find. This module aggregates each failing/erroring test by (exception type + normalized
message) so the agent sees the DOMINANT cause and how many tests it blocks, instead of a tail that
happens to show the last — not the biggest — failure (anthropic-sdk: 430 aiohttp shown, 1448
ConnectionError hidden).

Parsing is BLOCK-based, over the real pytest output (verified against an actual run), NOT the short
`-ra` summary — because for the dominant case, COLLECTION errors, the summary line ("ERROR path.py")
carries no reason at all; the cause (`ModuleNotFoundError: ...`) lives only in the traceback block.
pytest emits those blocks by default, so no extra reporting flag is needed. Each block runs from a
`___ title ___` banner to the next banner or `===` section line; its cause is the exception on the
last `E   ExcType: msg` line, else the `path:line: ExcType` traceback terminator. Pure, no I/O.
The top cause doubles as a stable failure signature (a future novelty-based stall can reuse it).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_EXC_SUFFIX = r"(?:Error|Exception|Warning|Timeout|Failed|Interrupted|Skipped)"

# A pytest block banner: a run of underscores, a title, a run of underscores ("___ test_x ___",
# "___ ERROR collecting a.py ___"). The `===`-delimited section headers use "=" and never match.
_BANNER = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}\s*$")
_SECTION = re.compile(r"^={3,}")
# "E   ModuleNotFoundError: No module named 'x'" — the exception on a marked traceback line (has msg).
_E_EXC = re.compile(r"^E\s+([A-Za-z_][\w.]*" + _EXC_SUFFIX + r")\b:?\s*(.*)$")
# "tests/test_x.py:2: AssertionError" — the traceback terminator (type, usually no msg). Excludes
# frame lines like "tests/x.py:1: in <module>" because "in <module>" doesn't end in an exc suffix.
_LOC_EXC = re.compile(r"^\S+:\d+:\s+([A-Za-z_][\w.]*" + _EXC_SUFFIX + r")\b\s*(.*)$")
_PYFILE = re.compile(r"([\w./+-]+\.py)")


@dataclass(frozen=True)
class Cause:
    exc: str          # exception type, e.g. "ModuleNotFoundError"
    detail: str       # representative RAW message (first seen), for display ("" if none)
    count: int        # tests affected by this cause
    outcome: str      # "ERROR" (collection) | "FAILED" (execution)
    module: str       # a representative file (first seen)


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
    groups: dict[tuple[str, str], dict] = {}
    for title, body in _blocks(output):
        exc, detail = _cause_of(body)
        if exc is None:
            continue
        key = (exc, _norm(detail))
        g = groups.get(key)
        if g is None:
            groups[key] = {"exc": exc, "detail": detail, "count": 1,
                           "outcome": "ERROR" if title.startswith("ERROR") else "FAILED",
                           "module": _module_of(title, body)}
        else:
            g["count"] += 1
    causes = [Cause(**g) for g in groups.values()]
    causes.sort(key=lambda c: (-c.count, c.exc))
    return causes


def _short_module(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) > 2 else path


def format_breakdown(causes: list[Cause], top: int = 5) -> str:
    """Render the ranked histogram (no header, no traceback tail). Top-N rows + a one-line
    remainder so a long tail of one-off failures stays compact."""
    rows = []
    for c in causes[:top]:
        detail = f": {c.detail}" if c.detail else ""
        rows.append(f"  {c.count} × {c.exc}{detail}  (e.g. {_short_module(c.module)})")
    rest = causes[top:]
    if rest:
        rows.append(f"  …and {len(rest)} more cause(s) across {sum(c.count for c in rest)} tests")
    return "\n".join(rows)
