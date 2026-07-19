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

from graph.contracts.executor import Executor
from graph.model import syslib_id, TEST_NODE_ID
from graph.model import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Node, NodeType, State,
)
from graph.python.native.apt import ObservedNeed, resolve
from graph.python.native.system_libs import make_syslib_node

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


# Bounded grep over the standard slim-image site-packages roots. ``-I`` skips
# binary files; ``|| true`` keeps grep's rc1 (no match) from looking like a
# failure. head-cap bounds the cost for a giant closure. The wiring reads
# stdout regardless of rc.
CTYPES_GREP_CMD = (
    "grep -rInE --include='*.py' "
    "-e 'find_library' -e 'CDLL' -e 'LoadLibrary' -e 'dlopen' "
    "/usr/local/lib/python*/site-packages /usr/lib/python*/dist-packages "
    "2>/dev/null | head -n 2000 || true"
)


# Core C-runtime / base-toolchain sonames that ship in EVERY Debian & python-slim
# base image (glibc, the dynamic linker, libgcc/libstdc++). A package doing
# find_library('c') / CDLL('libm.so.6') is a genuine observation, but the library
# is never a MISSING obligation — minting it just clutters setup.sh with an
# always-satisfied apt line and pollutes the graph. Keyed by canonical soname in
# BOTH the find_library base form (libX.so) and the versioned soname (libX.so.N),
# since canonical_soname passes versioned sonames through. This is a
# universal-PRESENCE fact (the C runtime is always there), NOT a benchmark-specific
# dist->syslib prediction map — principle-aligned, same class as the accepted
# always-present exceptions.
_CORE_SONAMES: frozenset[str] = frozenset({
    "libc.so", "libc.so.6",
    "libm.so", "libm.so.6",
    "libpthread.so", "libpthread.so.0",
    "libdl.so", "libdl.so.2",
    "librt.so", "librt.so.1",
    "libutil.so", "libutil.so.1",
    "libnsl.so", "libnsl.so.1", "libnsl.so.2",
    "libresolv.so", "libresolv.so.2",
    "libcrypt.so", "libcrypt.so.1", "libcrypt.so.2",
    "libgcc_s.so", "libgcc_s.so.1",
    "libstdc++.so", "libstdc++.so.6",
    "ld-linux.so", "ld-linux.so.2",
    "ld-linux-x86-64.so", "ld-linux-x86-64.so.2",
    "ld-linux-aarch64.so", "ld-linux-aarch64.so.1",
})


def _anchor_id(graph: DepGraph) -> str | None:
    """Node the discovered libs hang off: prefer the Project hub, else the Test
    goal (both legal ``requires`` sources), else None."""
    proj = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
    if proj is not None:
        return proj.id
    if graph.get(TEST_NODE_ID) is not None:
        return TEST_NODE_ID
    return None


def add_ctypes_runtime_libs(graph: DepGraph, executor: Executor) -> DepGraph:
    """Scan the installed closure's source for ctypes/cffi library literals and
    mint a ``SystemLib`` node (apt fix via PROVIDER_TABLE) for each, anchored to
    the Project (or Test) with a ``requires`` edge. Returns a NEW graph; a no-op
    when no literals are found or no anchor exists. Idempotent: an existing
    ``syslib:<soname>`` node is kept (only the edge is added)."""
    result = executor.run(CTYPES_GREP_CMD)
    hits = parse_ctypes_grep(result.stdout or "")
    if not hits:
        return graph
    anchor = _anchor_id(graph)
    if anchor is None:
        return graph

    new = graph
    seen: set[str] = set()
    for hit in hits:
        soname = canonical_soname(hit.lib)
        if soname in _CORE_SONAMES:
            continue  # C runtime: always present in the base image, never an obligation
        sid = syslib_id(soname)
        if sid in seen:
            continue
        seen.add(sid)
        if new.get(sid) is None:
            cands = resolve(ObservedNeed("soname", soname, context="runtime"), executor)
            apt = cands[0].package if cands else None
            new = new.with_node(make_syslib_node(
                soname,
                discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN,
                apt=apt,
                evidence=hit.evidence,
                provenance="ctypes-scan (installed source)",
            ))
        new = new.with_edge(Edge(
            src=anchor, dst=sid, relation=EdgeType.REQUIRES, origin="ctypes-scan"
        ))
    return new
