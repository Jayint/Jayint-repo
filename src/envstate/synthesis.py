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


# ---------------------------------------------------------------------------
# Test-required env-var capture (DROPPED_ENV)
# ---------------------------------------------------------------------------

# NAME=VALUE token: NAME is a POSIX shell identifier; VALUE is the rest up to
# whitespace OR a quoted run. Conservative: only the explicit assignments the
# agent issued are captured — never the ambient process environment.
_RE_ASSIGN = re.compile(r"""(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>'[^']*'|"[^"]*"|[^\s;&|]*)""")
# Leading `export` (possibly chained) before assignments.
_RE_EXPORT_STMT = re.compile(r"(?:^|[;&]|\|\|)\s*export\s+(?P<rest>[^;&|]+)")
# A var referenced in a command (`$X` or `${X}`) — keeps only test-relevant exports.
_RE_VAR_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

# Never bake these — shell/build incidentals or secrets, not test config.
# PYTHONPATH is denylisted: a stale absolute path the agent set (e.g. /app) breaks
# imports in the rebuilt image, which clones the repo to a different path (e.g. /testbed).
_ENV_DENYLIST = {
    "PATH", "PWD", "OLDPWD", "HOME", "SHLVL", "TERM", "USER", "HOSTNAME", "_",
    "LS_COLORS", "LANG", "LC_ALL", "SHELL", "TMPDIR", "PS1", "PS2", "VIRTUAL_ENV",
    "PYTHONPATH",
}

# Names that look like credentials are never baked into the image (secret leak).
_RE_SECRET_NAME = re.compile(
    r"SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL",
    re.IGNORECASE,
)


def _strip_quotes(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def extract_env_vars_from_ledger(ledger, *, extra_commands=()):
    """Return ordered, de-duplicated (NAME, VALUE) env assignments the agent issued.

    Captures ONLY explicit assignments (never the ambient environment):
      * `export NAME=VALUE` (standalone or chained) in rc==0 ledger commands;
      * inline `NAME=VALUE` prefixes on a recorded command (e.g.
        `DJANGO_SETTINGS_MODULE=cfg pytest -q`), from extra_commands (pass the
        verified test command(s)) and from rc==0 ledger commands.
    Last assignment to a NAME wins; first-appearance order preserved. Denylisted
    incidentals dropped. A var is kept only if it is also referenced (`$NAME`) OR
    appears as an inline prefix on a command — filters throwaway scratch vars.
    """
    assigns, order, referenced, prefix_required = {}, [], set(), set()

    def _record(name, val):
        if not name or name in _ENV_DENYLIST or _RE_SECRET_NAME.search(name):
            return
        val = _strip_quotes(val)
        if name not in assigns:
            order.append(name)
        assigns[name] = val

    rc0_cmds = [e.cmd for e in ledger.events() if e.rc == 0]
    for cmd in list(rc0_cmds) + list(extra_commands or ()):
        if not cmd:
            continue
        for ref in _RE_VAR_REF.findall(cmd):
            referenced.add(ref)
        for m in _RE_EXPORT_STMT.finditer(cmd):
            for a in _RE_ASSIGN.finditer(m.group("rest")):
                _record(a.group("name"), a.group("val"))
        stripped = cmd.strip()
        while True:
            a = _RE_ASSIGN.match(stripped)
            if not a or "=" not in stripped[: a.end()]:
                break
            _record(a.group("name"), a.group("val"))
            prefix_required.add(a.group("name"))
            stripped = stripped[a.end():].lstrip()

    out = []
    for name in order:
        if name in referenced or name in prefix_required:
            out.append((name, assigns[name]))
    return out
