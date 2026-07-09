"""The agent's move (spec §4): one read-only EXPLORE command or a full-script PATCH.
Parsing is pure; the loop enforces read-only on explore and applies the patch."""
from __future__ import annotations

import re
from dataclasses import dataclass

from python_deps.depgraph.patch_gate import is_read_only

# Any fenced bash/sh (or unlabeled) block IS the replacement script. We match the fence
# itself, not a `Script:` label, so the patch parses regardless of how the model announces it
# — `Script:`, markdown `**Script:**`, `### Script`, or no label at all. (A ```python block
# won't match, so an explore Action isn't hijacked by an incidental code snippet.)
_SCRIPT_BLOCK = re.compile(r"```(?:bash|sh)?[ \t]*\r?\n(.*?)```", re.DOTALL)
_ACTION_LINE = re.compile(r"^Action:\s*(.+)$", re.MULTILINE)
_ACTION_PREFIX = re.compile(r"^Action:\s*", re.IGNORECASE)
_THOUGHT = re.compile(r"Thought:\s*(.+?)(?=\n(?:Action|Script):|$)", re.DOTALL)

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
class Action:
    kind: str                       # "explore" | "patch" | "invalid"
    command: str | None = None      # explore
    new_script: str | None = None   # patch


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
    # compound with any install segment (`cd … && pip install …`) is NOT all-read-only → stays a patch.
    if _is_readonly_compound(line):
        return line
    return None


def _is_readonly_compound(line: str) -> bool:
    """True when *line* chains ≥2 commands (on &&/||/;) that are ALL read-only — a mis-wrapped
    investigation probe, never a build script. Single simple statements (`echo hi`) have one
    segment and are left to the patch path, preserving patch-wins semantics."""
    segments = [seg.strip() for seg in re.split(r"&&|\|\||;", line) if seg.strip()]
    return len(segments) >= 2 and all(is_read_only(seg) for seg in segments)


def _is_all_readonly_block(body: str) -> bool:
    """A fenced block whose EVERY meaningful line is read-only (≥2 lines) is a mis-formatted set of
    probes, not a build script — a build script installs/builds. Applying it as a patch replaces
    setup.sh with a non-installing script, so the build 'succeeds' with an empty env (false green;
    observed under concurrency on gitingest/ingestr). Single-line probe blocks are handled by
    _explore_from_script_block; this catches the multi-line case."""
    lines = [ln.strip() for ln in body.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) < 2:
        return False
    return all(is_read_only(_ACTION_PREFIX.sub("", ln)) for ln in lines)


def parse_action(text: str) -> Action:
    t = text or ""
    m = _SCRIPT_BLOCK.search(t)             # a fenced block is normally the whole replacement script
    if m:
        body = m.group(1).strip()
        recovered = _explore_from_script_block(body)
        if recovered is not None:           # a single mis-wrapped read-only probe → the explore it meant
            return Action("explore", command=recovered)
        if body and _is_all_readonly_block(body):   # multi-line all-probe block → not a build script
            return Action("invalid")
        if body:                            # a real (non-empty) build script → the patch
            return Action("patch", new_script=body + "\n")
    m = _ACTION_LINE.search(t)
    if m:
        return Action("explore", command=m.group(1).strip())
    return Action("invalid")


def extract_thought(text: str) -> str:
    m = _THOUGHT.search(text or "")
    return m.group(1).strip() if m else ""
