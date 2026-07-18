"""Grounded ctypes/cffi runtime-library discovery (the dlopen blind spot).

The two static sensors (import->pip, ldd DT_NEEDED) never see a library a pure-
Python package opens at run time via ``ctypes.util.find_library`` / ``CDLL`` /
cffi ``dlopen`` — the lib is not linked, so it leaves no DT_NEEDED trace, and the
package installs fine. This scanner reads the INSTALLED closure's source for those
call literals, normalizes each to a canonical soname, resolves it to apt via the
kept ``os_resolver.PROVIDER_TABLE``, and mints a ``SystemLib`` node grounded in a
real ``file:line``. It is an OBSERVATION (of installed source), never a curated
dist->syslib prediction (the map deleted in e04784c9).
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


def _strip_inline_comment(content: str) -> str:
    """Drop a trailing ``#`` comment, respecting single/double quotes so a
    ``#`` inside a string literal is preserved. Sonames never contain ``#``,
    so cutting at the first UNQUOTED ``#`` cannot truncate a real call literal.
    A comment-only line collapses to ``""`` (then skipped by the caller).

    NOTE: this is a line-local filter. A ctypes call inside a triple-quoted
    docstring example (no ``#`` on the line) cannot be distinguished from real
    code on isolated grep lines and remains a documented residual — the Part V
    false-positive guard measures the real-corpus rate.
    """
    quote = None
    for i, ch in enumerate(content):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            return content[:i]
    return content


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
        content = _strip_inline_comment(content)
        if not content.strip():
            continue
        for rx in CTYPES_CALL_RES:
            for m in rx.finditer(content):
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
