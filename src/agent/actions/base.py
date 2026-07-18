"""The agent's move (spec §4): one read-only EXPLORE command or a line-anchored EDIT.
Parsing is pure; the loop enforces read-only on explore and applies the edit.

Two input shapes are supported. The PRIMARY path is native tool-calling: the model calls the
`explore`/`edit` function and we map its structured JSON args to an Action (`action_from_tool_call`)
— no markdown/backtick/`Action:`-label drift is possible because the args are JSON. The text path
(`parse_action`) remains as a FALLBACK for providers/turns that return no tool_call."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from graph.patch.gate import is_read_only

# Any fenced bash/sh (or unlabeled) block IS the replacement script. We match the fence
# itself, not a `Script:` label, so the patch parses regardless of how the model announces it
# — `Script:`, markdown `**Script:**`, `### Script`, or no label at all. (A ```python block
# won't match, so an explore Action isn't hijacked by an incidental code snippet.)
_SCRIPT_BLOCK = re.compile(r"```(?:bash|sh)?[ \t]*\r?\n(.*?)```", re.DOTALL)

# Models (MiniMax especially) decorate the directive line with markdown — `**Edit:**`, `### Action:`,
# `> Edit:` — instead of a bare `Edit:`/`Action:`. Tolerate a leading blockquote/heading/emphasis run
# and a closing emphasis run between the label and its argument, so the directive still parses (else
# the turn is wasted as `invalid` — the gitingest run lost turns exactly this way). This does NOT relax
# edit-only: a fenced block carrying no directive still falls through to invalid.
_EMPH = r"[*_]*"                                                       # a run of bold/italic markers
_LABEL_PFX = rf"^[ \t]*(?:>[ \t]*)?(?:#{{1,6}}[ \t]*)?{_EMPH}[ \t]*"   # line start → optional md → label
_LABEL_SEP = rf"[ \t]*{_EMPH}[ \t]*"                                   # `Label:` → optional `**`/space → arg

_ACTION_LINE = re.compile(_LABEL_PFX + r"Action:" + _LABEL_SEP + r"(.+)$", re.MULTILINE)
_ACTION_PREFIX = re.compile(r"^Action:\s*", re.IGNORECASE)
_THOUGHT = re.compile(r"Thought:\s*(.+?)(?=\n\s*(?:Action|Edit|Script)\b|\n```|$)",
                      re.DOTALL | re.IGNORECASE)
# When the model skips the "Thought:" label (writes plain reasoning, then a directive/fence — as
# MiniMax/deepseek often do), capture that leading prose as the thought so the trace still records
# WHY the move was made. Bounded to the text before the first tool directive or fenced block.
_LEADING_PROSE = re.compile(r"\A(.*?)(?=\n\s*(?:Action|Edit|Script)\b|\n?```)",
                            re.DOTALL | re.IGNORECASE)
_STARTS_DIRECTIVE = re.compile(r"\A(?:Action|Edit|Script)\b|\A```", re.IGNORECASE)

# First tokens that are pure read-only INVESTIGATION. A fenced block that is JUST one of these is a
# mis-wrapped explore probe, not a build script (which installs/builds something). Conservative —
# extend as real transcripts surface new probes. `echo`/`printf`/`true` are deliberately EXCLUDED:
# they can be trivial script statements, and a lone script is still a (degenerate) patch, not a probe.
_READ_PROBE_CMDS = frozenset({
    "cat", "ls", "find", "grep", "egrep", "fgrep", "head", "tail", "ldconfig", "ldd", "nm",
    "stat", "file", "which", "type", "readlink", "realpath", "dirname", "basename", "wc",
    "dpkg", "apt-cache", "pip", "pip3", "python", "python3", "env", "printenv", "du", "df",
    "objdump", "sed", "awk", "test", "pkg-config", "sha256sum", "md5sum",
})


@dataclass(frozen=True)
class EditOp:
    verb: str            # "replace" | "insert" | "delete"
    start: int           # 1-based line (for insert: the anchor; 0 = top of file)
    end: int             # 1-based inclusive; == start for a single line
    content: str = ""    # new line(s); empty for a delete


@dataclass(frozen=True)
class Action:
    kind: str                       # "explore" | "patch" | "edit" | "invalid"
    command: str | None = None      # explore
    new_script: str | None = None   # patch (whole-script fallback)
    edit: "EditOp | None" = None    # edit — a line-anchored change (preferred over patch)


# The two tools offered to the model (OpenAI function-calling schema). Structured args are why
# tool-calling has none of the text-parser's drift: the command / edit fields arrive as JSON, so
# markdown bold, wrapping backticks, `[bracket]` tags, and "no directive at all" are impossible.
# `tool_choice="required"` (set by the planner) forces exactly one of these every turn.
TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "explore",
        "description": ("Run ONE read-only shell command to investigate before you fix the build "
                        "(ls, cat, find, pip show, ldconfig, …). Never installs or modifies. Its "
                        "output comes back to you next turn."),
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "a single read-only shell command"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "edit",
        "description": ("Change setup.sh by line number — this is how you repair the build. Line "
                        "numbers are the ones shown in CURRENT setup.sh (the same ones the build "
                        "failure names)."),
        "parameters": {"type": "object", "properties": {
            "verb": {"type": "string", "enum": ["replace", "insert", "delete"],
                     "description": "replace/delete lines start..end; insert AFTER line `start` (0 = top)"},
            "start": {"type": "integer", "description": "1-based anchor line"},
            "end": {"type": "integer", "description": "1-based inclusive end; omit for a single line"},
            "content": {"type": "string",
                        "description": "the shell line(s) to add for replace/insert; omit for delete"}},
            "required": ["verb", "start"]}}},
]

_THINK = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def extract_reasoning(content: str | None) -> str:
    """The turn's reasoning for the trace, from the tool-call response's message content. Models
    put chain-of-thought in a `<think>…</think>` block (MiniMax) — return its inner text; otherwise
    return the plain content. Empty when there is nothing (a bare tool call with no prose)."""
    t = (content or "").strip()
    m = _THINK.search(t)
    if m:
        return m.group(1).strip()
    return t


def action_from_tool_call(name: str, arguments: str) -> Action:
    """Map a native tool call (function name + JSON-string arguments) to an Action. Pure and
    total: any malformed/unknown call becomes `invalid` so the loop re-prompts (never crashes)."""
    try:
        args = json.loads(arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return Action("invalid")
    if not isinstance(args, dict):
        return Action("invalid")
    if name == "explore":
        cmd = str(args.get("command") or "").strip()
        return Action("explore", command=cmd) if cmd else Action("invalid")
    if name == "edit":
        verb = str(args.get("verb") or "").strip().lower()
        if verb.startswith("insert"):
            verb = "insert"
        if verb not in ("replace", "insert", "delete"):
            return Action("invalid")
        try:
            start = int(args["start"])
        except (KeyError, TypeError, ValueError):
            return Action("invalid")
        try:
            end = int(args.get("end", start))
        except (TypeError, ValueError):
            end = start
        if end < start:
            end = start
        content = str(args.get("content") or "")
        if verb in ("replace", "insert") and not content.strip():
            return Action("invalid")     # nothing to add — unusable
        return Action("edit", edit=EditOp(verb, start, end, content))
    return Action("invalid")


# One Edit directive per turn (v1): `Edit: replace 40` / `replace 40-42` / `insert after 40` /
# `delete 40` / `delete 40-42`. Line numbers are 1-based and match the ERR-trap `lineno` in the build
# failure (the sandbox reports lineno relative to THIS script), so "line 40 failed" and `Edit: replace
# 40` name the same line. An optional `line`/`lines` word after the verb is tolerated.
_EDIT_RE = re.compile(
    _LABEL_PFX + r"Edit:" + _LABEL_SEP +
    r"(replace|insert\s+after|delete)\s+(?:lines?\s+)?(\d+)(?:\s*-\s*(\d+))?\b",
    re.IGNORECASE | re.MULTILINE)


def apply_edit(script: str, op: EditOp) -> "str | None":
    """Apply a line-anchored edit to *script*; return the new script text, or None if the line ref is
    out of range (the loop then re-prompts). Pure list-splice on physical lines. keep-best is the
    safety net for a wrong-but-in-range edit: one that breaks the build scores worse than the seed and
    is discarded, so we don't need a content-echo guard here."""
    lines = (script or "").splitlines()
    n = len(lines)
    new_lines = op.content.splitlines() if op.content else []
    if op.verb == "insert":                        # insert AFTER line `start` (0 = top of file)
        if not (0 <= op.start <= n):
            return None
        result = lines[:op.start] + new_lines + lines[op.start:]
    else:                                          # replace / delete lines start..end (inclusive)
        if not (1 <= op.start <= op.end <= n):
            return None
        result = lines[:op.start - 1] + new_lines + lines[op.end:]   # new_lines == [] → delete
    return "\n".join(result) + "\n"


def _single_meaningful_line(body: str) -> str | None:
    """The lone non-blank, non-comment line of *body*, or None if there are zero or several."""
    lines = [ln.strip() for ln in body.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    return lines[0] if len(lines) == 1 else None


def _explore_from_script_block(body: str) -> str | None:
    """Recover the explore a model MEANT when it wrapped a read-only probe in a ```bash fence
    (Bug B). Accepting such a block as a patch replaces the whole setup.sh with a non-installing
    one-liner (observed on MiniMax: `Action: cat pyproject.toml`, then a bare `find … | head`), so
    the build 'succeeds' while installing nothing. Two conservative triggers, single-line blocks only:
      • the line is an `Action:` directive — unambiguous; no genuine setup.sh line starts with Action:
        (a non-read-only recovered command is still rejected downstream by the loop's is_read_only gate);
      • the line is a bare read-only INVESTIGATION command (first token in _READ_PROBE_CMDS).
    A single-line INSTALL (`pip install …`) is not read-only, so it correctly stays a patch."""
    line = _single_meaningful_line(body)
    if line is None:
        return None
    if _ACTION_PREFIX.match(line):
        return _ACTION_PREFIX.sub("", line).strip() or None
    first = line.split()[0] if line.split() else ""
    if first in _READ_PROBE_CMDS and is_read_only(line):
        return line
    # A single-line COMPOUND whose every &&/||/; segment is read-only (e.g.
    # `cd /app && cat pyproject.toml && ls -la`) is a mis-wrapped explore probe, not a build
    # script — even when its FIRST token (`cd`) is neither an install verb nor a probe verb, so
    # the first-token allowlist above misses it. Applying it as a patch overwrote the seed with a
    # non-installing script that still "built green" (false green: ezdata, promnesia). Deciding by
    # is_read_only PER SEGMENT (not the first token) recovers it as the explore the model meant. A
    # compound with any install segment (`cd … && pip install …`) is NOT all-read-only → not recovered.
    if _is_readonly_compound(line):
        return line
    return None


def _is_readonly_compound(line: str) -> bool:
    """True when *line* chains ≥2 commands (on &&/||/;) that are ALL read-only — a mis-wrapped
    investigation probe. Recovered as an explore. Single simple statements (`echo hi`) have one
    segment, so they aren't recovered here and fall through to invalid (edit-only: not a valid move)."""
    segments = [seg.strip() for seg in re.split(r"&&|\|\||;", line) if seg.strip()]
    return len(segments) >= 2 and all(is_read_only(seg) for seg in segments)


def parse_action(text: str) -> Action:
    t = text or ""
    m_edit = _EDIT_RE.search(t)             # a line-anchored Edit is the PREFERRED change (v1: one op)
    if m_edit:
        verb = "insert" if m_edit.group(1).lower().startswith("insert") else m_edit.group(1).lower()
        start = int(m_edit.group(2))
        end = int(m_edit.group(3)) if m_edit.group(3) else start
        content = ""
        if verb in ("replace", "insert"):   # these carry the new line(s) as one fenced block
            # Grab the fence that FOLLOWS the Edit directive — not the first block anywhere, so an
            # example fence in the model's prose before `Edit:` can't be spliced in by mistake. Only
            # this block's contents become the new lines (any surrounding prose is discarded).
            mb = _SCRIPT_BLOCK.search(t, m_edit.end())
            if mb is None:                  # replace/insert without a following block is unusable
                return Action("invalid")
            content = mb.group(1).strip("\n")
        return Action("edit", edit=EditOp(verb, start, end, content))
    # Edit-only: a fenced ```bash block is NOT a way to change setup.sh (whole-script rewrites are
    # gone — only line-anchored Edits mutate the script). We still RECOVER a mis-wrapped read-only
    # probe from a fence as the explore the model meant; anything else falls through to invalid so
    # the loop re-prompts "use Edit".
    m = _SCRIPT_BLOCK.search(t)
    if m:
        recovered = _explore_from_script_block(m.group(1).strip())
        if recovered is not None:
            return Action("explore", command=recovered)
    m = _ACTION_LINE.search(t)              # an explicit read-only investigate directive
    if m:
        return Action("explore", command=m.group(1).strip())
    return Action("invalid")                # non-probe fence or prose → re-prompt (use Edit)


def extract_thought(text: str) -> str:
    t = text or ""
    m = _THOUGHT.search(t)
    if m:
        return m.group(1).strip()
    lead = _LEADING_PROSE.match(t)                 # no "Thought:" label — take the leading prose
    prose = (lead.group(1).strip() if lead else "")
    if not prose or _STARTS_DIRECTIVE.match(prose):  # a bare directive is not a thought
        return ""
    return prose[:500]
