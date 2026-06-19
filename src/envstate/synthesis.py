from __future__ import annotations
import re
from typing import List, Optional, Sequence, Tuple

from src.envstate.ledger import ActionLedger


# ---------------------------------------------------------------------------
# File-edit detection (CHANGE D)
# ---------------------------------------------------------------------------

# Detection patterns. A redirect counts as a file write ONLY when it sends stdout
# to a REAL file — never a stderr/fd redirect (`2>`, `&>`) and never `/dev/null`.
# This avoids rescuing read-only inspection like `cat x | grep y 2>/dev/null`.
_RE_WRITE_PREFIX = re.compile(r"^\s*(?:printf|echo|cat|tee)\b")
_RE_FILE_WRITE = re.compile(r"(?<![0-9&])>>?\s*(?!/dev/null\b)(?!&)\S")
_RE_SED_I = re.compile(r"^\s*sed\s+-i\b")
_RE_PYTHON_C_WRITE = re.compile(
    r"""^\s*python[0-9.]*\s+-c\b.*open\s*\(.*(?:['"][aw]\+?['"]|\.write\s*\()""",
    re.DOTALL,
)

_PYTEST_RE = re.compile(r"^\s*(?:python[0-9.]*\s+-m\s+)?pytest\b")
_READONLY_PREFIXES = ("ls ", "grep ", "find ", "head ", "tail ")

# A heredoc operator: `<<` (optionally `<<-`), optional quote, then a delimiter word
# that starts with a letter/underscore.  The leading-letter anchor means arithmetic
# bit-shifts like `$((1 << 4))` never match (the token after `<<` is a digit).
_RE_HEREDOC_OP = re.compile(r"<<-?\s*['\"]?[A-Za-z_]\w*")


def _is_unterminated_heredoc(cmd: str) -> bool:
    """True for a SINGLE-LINE command bearing a heredoc operator (``<< DELIM``).

    The build agent records only the first line of a multi-line action
    (build_agent.py:_extract_worker_action plain/fenced branches apply
    ``.splitlines()[0]``), so a heredoc like ``cat > f << 'EOF'\\n<body>\\nEOF`` is
    stored as the orphan opener ``cat > f << 'EOF'`` — body and terminator gone. Such a
    command is structurally unreplayable: emitted as a Dockerfile ``RUN``, the eval
    adapter's heredoc parser waits for a terminator that never arrives and greedily
    swallows every following ``RUN`` into one no-op script (Bug 2 "no-op recipe"). A
    genuinely multi-line heredoc keeps its body and terminator, contains a newline, and
    is therefore NOT matched here — only the broken single-line opener is.
    """
    if not cmd or "\n" in cmd:
        return False
    return bool(_RE_HEREDOC_OP.search(cmd))


def _is_source_file_edit(cmd: str) -> bool:
    """Return True if cmd's primary action writes or edits a file.

    Used ONLY in the synthesis layer to rescue rc==0 file-edit commands that the
    env-mutation classifier left with mutation_class=None.  Deliberately does NOT
    match test runners, read-only inspection, or stderr/dev-null redirects.
    """
    if not cmd:
        return False
    s = cmd.strip()
    if _PYTEST_RE.match(s):
        return False
    if s.startswith(_READONLY_PREFIXES):
        return False
    # A body-less heredoc-open (truncated at record time) is not a replayable edit —
    # drop it regardless of target path (see _is_unterminated_heredoc / Bug 2).
    if _is_unterminated_heredoc(s):
        return False
    # printf/echo/cat/tee only when it genuinely writes stdout to a real file:
    if _RE_WRITE_PREFIX.match(s) and _RE_FILE_WRITE.search(s):
        return True
    if _RE_SED_I.match(s):
        return True
    if _RE_PYTHON_C_WRITE.match(s):
        return True
    return False


def build_commands_from_ledger(ledger: ActionLedger, distill=None) -> List[str]:
    """Authoritative, order-preserving build-command extraction (design §15).

    Includes only successful (rc==0) env-mutating commands, in trajectory order.
    Read-only commands (mutation_class is None and not a file edit) and test
    commands (also mutation_class None) are excluded. Duplicates are intentionally
    preserved — setup side effects are NOT algebraically mergeable (see CLAUDE.md).

    rc==0 commands with mutation_class=None that look like file edits (printf/echo
    redirects, sed -i, python -c open/write) are also kept — these patch repo source
    in-container and must persist in the rebuilt seed image.

    `distill`, if given (e.g. Synthesizer._extract_recordable_setup_commands), is
    applied to each kept command and must return a list of 0+ distilled command
    strings. This strips a trailing test invocation from a compound 'install && test'
    action so it never becomes a Dockerfile RUN step, matching the legacy path.
    Without it, the raw command is kept.
    """
    commands: List[str] = []
    for event in ledger.events():
        if event.rc != 0:
            continue
        if _is_unterminated_heredoc(event.cmd):
            # Broken single-line heredoc-open (body+terminator stripped at record time).
            # Drop BEFORE the mutation_class branch: classify_mutation tags `cat > f <<`
            # as 'file_or_env_change', which would otherwise keep it and collapse the
            # recipe at eval time (Bug 2). Covers both the v1 ledger-appender path
            # (mutation_class=None) and the build_agent path (mutation_class set).
            continue
        if not event.mutation_class and not _is_source_file_edit(event.cmd):
            continue  # read-only or test command
        if distill is not None:
            commands.extend(distill(event.cmd))
        else:
            commands.append(event.cmd)
    return commands


# ---------------------------------------------------------------------------
# Pin-closure helpers
# ---------------------------------------------------------------------------

PIN_PATH = "/tmp/jayint-pinned-closure.txt"  # distinct basename: dodges the runner's -r parse bug


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace("_", "-")


def build_pin_instructions(
    installed: Sequence,
    *,
    project_name: Optional[str] = None,
    pin_path: str = PIN_PATH,
) -> List[str]:
    """Two RUN-body strings [printf_write, pip_install_r] pinning the exact closure,
    or [] if nothing to pin. Excludes the project's own package by normalized name.
    Inputs are Fact(name, detail=version); editable/VCS are already absent (no '==')."""
    proj = _norm(project_name)
    _INSTALLER_NAMES = {"pip", "setuptools", "wheel"}
    specs = []
    for f in installed or ():
        name = getattr(f, "name", "") or ""
        ver = getattr(f, "detail", "") or ""
        if not name or not ver:
            continue
        if _norm(name) in _INSTALLER_NAMES:
            continue  # never pin the installer itself (causes self-downgrade ordering)
        if proj and _norm(name) == proj:
            continue
        specs.append(f"{name}=={ver}")
    if not specs:
        return []
    quoted = " ".join("'" + s + "'" for s in specs)
    return [
        f"printf '%s\\n' {quoted} > {pin_path}",
        f"pip install -r {pin_path}",
    ]
