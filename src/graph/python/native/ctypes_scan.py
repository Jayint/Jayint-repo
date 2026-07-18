"""Grounded ctypes/cffi runtime-library discovery (the dlopen blind spot).

The two static sensors (import->pip, ldd DT_NEEDED) never see a library a pure-
Python package opens at run time via ``ctypes.util.find_library`` / ``CDLL`` /
cffi ``dlopen`` — the lib is not linked, so it leaves no DT_NEEDED trace, and the
package installs fine. This scanner reads the INSTALLED closure's source for those
call literals, normalizes each to a canonical soname, resolves it to apt via the
kept ``os_resolver.PROVIDER_TABLE``, and mints a ``SystemLib`` node grounded in a
real ``file:line``. It is an OBSERVATION (of installed source), never a curated
dist->syslib prediction (the map deleted in e04784c9).

Precision is line-local by design (grep-line + regex, not a Python tokenizer).
``_line_lex`` rejects the common false hits (commented-out calls, calls quoted
inside an ordinary string, single-line triple-quoted text) and preserves the
common real hits (calls after an earlier string, escaped quotes). Three residual
cases cannot be resolved from a single grep line and are accepted as known
limitations, measured — not asserted away — by the Part V false-positive guard
over 30 real negative repos:
  * a ctypes call inside a genuinely MULTI-line triple-quoted docstring
    (no quote/# on the matched line);
  * a single-line triple-quoted string that itself contains a quote
    (``\"\"\"note \" CDLL('x')\"\"\"``) — triple delimiters are treated as
    alternating ordinary quotes;
  * an f-string interpolation that IS executable (``f\"{CDLL('x')}\"``) — the
    whole f-string is treated as string text, so a real call there is missed.
Full fidelity would require in-container Python tokenization (a larger,
plan-scope change); the real-corpus rate of these patterns is ~nil.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# The call shapes that take a runtime library name as a STRING LITERAL. A
# variable argument (``CDLL(path_var)``) captures nothing and is skipped. Kept
# host-side and unit-tested; the container step (A3) is a plain grep that does
# no matching, so there is no second copy to drift from.
#
# Shared sub-patterns. `\b` before the call name rejects identifier-embedded
# false hits (NotCDLL); the trailing `\s*[,)]` requires the literal to be the
# COMPLETE first argument (rejects `CDLL('lib' + var)`); an optional raw/bytes
# string prefix and optional `name=` keyword accept the legal literal variants.
_PREFIX = r"(?:[rRbB]{1,2})?"        # r'' / b'' / rb'' string prefix
_KW = r"(?:name\s*=\s*)?"            # keyword form of the first arg
_TAIL = r"\s*[,)]"                   # literal must be the whole first arg
_STR = r"(?P<q>['\"])(?P<lib>{})(?P=q)"   # matched-quote literal; group 'lib'

CTYPES_CALL_RES: tuple[re.Pattern, ...] = (
    re.compile(
        r"\bfind_library\s*\(\s*" + _KW + _PREFIX + _STR.format(r"[\w.+-]+") + _TAIL
    ),
    re.compile(
        r"\b(?:CDLL|cdll\.LoadLibrary|windll\.LoadLibrary|LoadLibrary)\s*\(\s*"
        + _KW + _PREFIX + _STR.format(r"[\w./+-]+") + _TAIL
    ),
    re.compile(
        r"\b(?:ffi\.dlopen|dlopen)\s*\(\s*" + _KW + _PREFIX + _STR.format(r"[\w./+-]+") + _TAIL
    ),
)


@dataclass(frozen=True)
class LibHit:
    """One ctypes/cffi library literal found in installed source."""
    lib: str
    evidence: str   # "<path>:<lineno>  <snippet>"


def _short_evidence(path: str, lineno: str, content: str) -> str:
    # Trim the path to the site-packages tail for a compact, stable snippet.
    marker = "site-packages/"
    idx = path.find(marker)
    rel = path[idx + len(marker):] if idx >= 0 else path
    snippet = " ".join(content.split())
    return f"{rel}:{lineno}  {snippet}"[:200]


def _line_lex(content: str) -> tuple[list[bool], int]:
    """One escape-aware pass over a source line. Returns:
      - ``in_string``: per-char flags — True where the char is inside a string
        literal (the delimiting quotes included);
      - ``comment_at``: index of the first UNQUOTED ``#`` (a comment start), or
        ``len(content)`` if none.
    A regex match is a real call only when its call-name start is in code
    (``not in_string[start]`` and ``start < comment_at``). This distinguishes
    real calls from commented-out calls, calls quoted inside a string, and
    single-line triple-quoted text. It is line-local: a ctypes call inside a
    genuinely MULTI-line triple-quoted docstring (no quote/# on this line) is
    indistinguishable from code here and remains a documented residual — the
    Part V false-positive guard measures the real-corpus rate.
    """
    n = len(content)
    in_string = [False] * n
    quote = None
    escaped = False
    comment_at = n
    for i, ch in enumerate(content):
        if quote:
            in_string[i] = True
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            in_string[i] = True
        elif ch == "#":
            comment_at = i
            break
    return in_string, comment_at


def parse_ctypes_grep(stdout: str) -> list[LibHit]:
    """Parse ``grep -rIn`` output (``path:lineno:content``) into LibHits.

    Applies CTYPES_CALL_RES to each line's content; a line with no string
    literal (variable argument) yields nothing. Dedups by (lib, path:line).
    """
    hits: list[LibHit] = []
    seen: set[tuple[str, str]] = set()
    for line in (stdout or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path, lineno, content = parts
        in_string, comment_at = _line_lex(content)
        for rx in CTYPES_CALL_RES:
            for m in rx.finditer(content):
                start = m.start()
                if start >= comment_at or in_string[start]:
                    continue   # match is in a comment or is quoted string text
                lib = m.group("lib")
                key = (lib, f"{path}:{lineno}")
                if key in seen:
                    continue
                seen.add(key)
                hits.append(LibHit(lib=lib, evidence=_short_evidence(path, lineno, content)))
    return hits


def canonical_soname(lib: str) -> str:
    """Normalize a ctypes literal to a canonical soname.

    ``find_library('magic')`` -> ``libmagic.so``; ``CDLL('/usr/lib/libX.so.2')``
    -> ``libX.so.2`` (basename, already a soname); a bare ``cairo`` -> ``libcairo.so``.
    """
    base = os.path.basename(lib)
    if re.search(r"\.so(\.\d+)*$", base):   # terminal .so or .so.N[.M...]
        return base
    if base.startswith("lib"):
        return f"{base}.so"
    return f"lib{base}.so"
