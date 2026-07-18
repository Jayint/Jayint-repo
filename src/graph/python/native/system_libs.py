"""System-library discovery: ldd runtime probe + SystemLib node shape + signatures.

Folds three native leaves into one concept file (3c-3):
  * ``ldd_probe`` — post-install ldd discovery of run-time ``=> not found`` sonames
    (the primary authoritative source of run-time native-lib nodes);
  * ``make_syslib_node`` — the single ``SystemLib`` node shape, so a pre-install
    prediction and a post-install observation collapse onto ONE canonical-soname id;
  * ``extract_needs`` — table-independent stderr -> ``ObservedNeed`` signature parsing.
"""

from __future__ import annotations

import json
import os
import re
import shlex

from graph.contracts.executor import Executor
from graph.ids import syslib_id
from graph.model import (
    Attempt,
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from graph.python.native.apt import ObservedNeed, default_context, resolve
from graph.python.native.probe import reconcile_predicted
from graph.python.util.failure_classifier import SONAME_RES
from graph.python.util.import_mapping import normalize_package_name


# === ldd_probe: post-install ldd run-time native-lib discovery ===
# One container round-trip: emit JSON {canonical_dist_name: [absolute ext-.so paths]}
# for all installed distributions via importlib.metadata.
#
# MUST-FIX guards applied inside the command (plan Task 1):
#   * d.files is None   -> skip (not crashed; ~9% of dists have no RECORD)
#   * absolute paths    -> via d.locate_file(f) (relative files() cause silent ldd misses)
#   * ext modules only  -> basename matches .cpython-NN[N]*.so or .abi3.so
#       (NN = 2-digit tag for Python 3.0-3.9 e.g. cpython-39; NNN = 3-digit for
#        3.10+ e.g. cpython-311 — the count MUST allow both or 3.9 ext modules
#        are silently skipped and their native libs never get ldd-probed)
#   * bundled helpers excluded:
#       - path containing .libs/  (manylinux auditwheel directory)
#       - basename matching ^lib<name>-<8hex>.so (auditwheel-renamed soname)
#
# Shell-quoting notes:
#   * Outer python -c uses double quotes -> inner Python strings use single quotes.
#   * $ in regex end-of-string anchors becomes \Z (shell double-quotes expand $
#     to empty; \Z is not a shell escape and passes through unchanged, then the
#     regex engine interprets \Z as end-of-string).
#   * DockerExecutor wraps in sh -c '...'; its single-quote escaping preserves
#     the inner Python single-quoted strings correctly.
EXT_SO_MAP_CMD = (
    "python -c \""
    "import importlib.metadata as M, json, re, os; "
    "dists = {}; "
    "EXT = re.compile('[.]cpython-[0-9]{2,3}.*[.]so\\Z|[.]abi3[.]so\\Z'); "
    "BND = re.compile('^lib[a-z0-9._+-]+-[0-9a-f]{8}[.]so\\Z'); "
    "_ = [dists.setdefault(c, []).append(p) "
    "for d in M.distributions() if d.files is not None "
    "for c in [re.sub('[-_.]+', '-', (d.metadata.get('Name') or '').strip()).lower()] "
    "if c "
    "for f in d.files "
    "if EXT.search(bn := os.path.basename(p := str(d.locate_file(f)))) "
    "and not BND.match(bn) "
    "and '.libs/' not in p]; "
    "print(json.dumps(dists))\""
)


def parse_ext_so_map(stdout: str) -> dict[str, list[str]]:
    """Parse the JSON ``{canonical_dist_name: [abs ext-.so paths]}`` output.

    Returns an empty dict on any parse failure so the caller no-ops gracefully.
    """
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, val in data.items():
        if isinstance(key, str) and isinstance(val, list):
            out[key] = [v for v in val if isinstance(v, str)]
    return out


def parse_ldd_not_found(stdout: str) -> list[str]:
    """Sonames from ``=> not found`` lines; deduped, preserving first occurrence.

    Input may be multi-file ldd output (per-file ``path:`` headers).  Only lines
    containing ``=> not found`` are kept; the token before ``=>`` is extracted
    and stripped.  A soname that appears multiple times is returned once.
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in stdout.splitlines():
        if "=> not found" not in line:
            continue
        arrow = line.find("=>")
        if arrow < 0:
            continue
        token = line[:arrow].strip()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def ldd_probe(graph: DepGraph, executor: Executor) -> DepGraph:
    """Stage 4.5: discover run-time native-lib gaps via ldd on extension modules.

    For each Package node: batch-ldd its extension ``.so`` files; collect
    ``=> not found`` sonames; resolve soname → apt via ``os_resolver.resolve``
    (fills ``chosen_fix`` only — never the id); then either reconcile with a
    seed RESOLVER prediction of the same CANONICAL SONAME id (keeping
    ``discovered_by=RESOLVER``) or create a fresh ``discovered_by=PROBE`` node.
    Adds a ``requires`` edge Package→SystemLib.  Returns a NEW graph; no-op for
    packages with no extension modules.

    Canonical identity (Task 9): the soname IS the SystemLib node's id (see
    ``seed.py`` module docstring "canonical rule").  Reconciliation is keyed by
    the soname, so the seed prediction and this observation always collapse
    onto ONE node — independent of whether ``resolve_soname_apt`` succeeds this
    round (the prior apt-keyed reconciliation split into two nodes whenever
    resolution failed; this cannot happen anymore).

    Option A: ``os_resolver.resolve`` is table-first with an apt-file fallback
    ABSENT on slim images.  An unknown soname (not in ``PROVIDER_TABLE``)
    yields a node with EMPTY ``fix_candidates`` — the *need* is surfaced but
    the apt *name* is not.  Option B (lazy apt-file) closes this gap — see
    plan Future TODOs.
    """
    so_result = executor.run(EXT_SO_MAP_CMD)
    if not so_result.ok:
        return graph
    so_map = parse_ext_so_map(so_result.stdout)
    if not so_map:
        return graph

    new = graph
    for pkg in [n for n in graph.nodes if n.type is NodeType.PACKAGE]:
        canon = normalize_package_name(pkg.name)
        so_paths = so_map.get(canon, [])
        if not so_paths:
            continue

        # Batch-ldd all extension .so files for this package in one round-trip.
        ldd_cmd = "ldd " + " ".join(shlex.quote(p) for p in so_paths)
        ldd_result = executor.run(ldd_cmd)
        if not ldd_result.ok:
            continue

        sonames = parse_ldd_not_found(ldd_result.stdout or "")
        for soname in sonames:
            cands = resolve(ObservedNeed("soname", soname, context="runtime"), executor)
            apt = cands[0].package if cands else None
            check = f"ldconfig -p | grep {soname}"
            evidence = _first_line_with(ldd_result.stdout or "", soname)

            # Reconcile with a RESOLVER seed prediction of the same CANONICAL
            # SONAME id (keeps discovered_by=RESOLVER per the spec); fall back
            # to a fresh PROBE node using the soname as the id.
            predicted_id = syslib_id(soname)
            reconciled = reconcile_predicted(
                new,
                predicted_id,
                check=check,
                evidence=evidence,
                command=ldd_cmd,
                chosen_fix=f"apt:{apt}" if apt else None,
                fix_candidates=tuple(f"apt:{c.package}" for c in cands),
            )
            if reconciled is not None:
                node_id = reconciled.id
                new = new.with_node(reconciled)
            else:
                # A prior package in this same pass may already have produced a
                # PROBE node for this soname (e.g. two dists both report
                # ``libGL.so.1 => not found`` and no RESOLVER seed exists). Append
                # this package's attempt to the existing node instead of replacing
                # it wholesale, which would silently drop the earlier package's
                # attempt history (review MEDIUM-1).
                existing = new.get(predicted_id)
                if existing is not None:
                    node = existing.with_attempt(
                        Attempt(command=ldd_cmd, outcome="failed", check=check)
                    )
                else:
                    node = _make_syslib_node(
                        soname, ldd_result.stdout or "", ldd_cmd, apt=apt
                    )
                node_id = node.id
                new = new.with_node(node)

            new = new.with_edge(
                Edge(src=pkg.id, dst=node_id, relation=EdgeType.REQUIRES, origin="probe")
            )

    return new


# --------------------------------------------------------------------------- #
# Node builders and helpers                                                    #
# --------------------------------------------------------------------------- #

def _make_syslib_node(
    soname: str, ldd_output: str, command: str, *, apt: str | None = None
) -> Node:
    """Fresh probe-discovered SystemLib for a soname reported by ldd."""
    check = f"ldconfig -p | grep {soname}"
    node = make_syslib_node(
        soname,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        apt=apt,
        evidence=_first_line_with(ldd_output, soname),
        provenance="ldd (observed)",
    )
    return node.with_attempt(Attempt(command=command, outcome="failed", check=check))


def _first_line_with(text: str, needle: str, max_chars: int = 500) -> str:
    """First line of ``text`` containing ``needle``, truncated; else head of text.

    Guards ``text is None`` (``(text or "")``) to mirror ``probe._first_line_with``
    so the two copies cannot drift into different crash behavior (review LOW).
    """
    for line in (text or "").splitlines():
        if needle in line:
            return line.strip()[:max_chars]
    return (text or "").strip()[:max_chars]


# === make_syslib_node: the single SystemLib node shape (was syslib.py) ===
def make_syslib_node(
    soname: str,
    *,
    discovered_by: DiscoveredBy,
    state: State,
    apt: str | None = None,
    evidence: str | None = None,
    provenance: str | None = None,
) -> Node:
    """Build a ``SystemLib`` node keyed by canonical soname.

    ``apt`` (when given) fills both ``fix_candidates`` and ``chosen_fix`` as
    ``apt:<name>``; a ``None`` apt leaves them empty (need surfaced, apt name
    unknown). The check is always ``ldconfig -p | grep <soname>``. Callers set
    ``discovered_by``/``state`` (RESOLVER/UNKNOWN for a pre-install prior,
    PROBE/MISSING for an ldd observation) and append any ``Attempt`` themselves.
    """
    return Node(
        id=syslib_id(soname),
        type=NodeType.SYSTEM_LIB,
        name=soname,
        layer=Layer.SYSTEM,
        discovered_by=discovered_by,
        state=state,
        check_command=f"ldconfig -p | grep {soname}",
        evidence=evidence,
        fix_candidates=(f"apt:{apt}",) if apt else (),
        chosen_fix=f"apt:{apt}" if apt else None,
        provenance=provenance,
    )


# === failure_signatures: stderr -> ObservedNeed extraction ===
_HDR_EXTS = (".h", ".hh", ".hpp", ".hxx", ".H", ".tcc", ".ipp")
_HDR = r"[\w./+-]+\.(?:h|hh|hpp|hxx|H|tcc|ipp)"

HEADER_RES = (
    re.compile(rf"(?:fatal error:\s*)?({_HDR})\s*:\s*No such file or directory"),  # gcc/g++/cc
    re.compile(rf"fatal error:\s*'({_HDR})'\s*file not found"),                    # clang, quoted
    re.compile(rf"'({_HDR})'\s*file not found"),                                   # clang driver
)

BINARY_RES = (
    re.compile(r"(?:^|\n)(?:\S*sh: )?([A-Za-z0-9_][\w.+-]*): command not found\b"),          # shell
    re.compile(r"/bin/(?:sh|dash): \d+: ([A-Za-z0-9_][\w.+-]*): not found\b"),               # dash numbered
    re.compile(r"([A-Za-z0-9_][\w.+-]*) executable (?:was )?not found\b"),                   # setuptools
    re.compile(r"[Tt]he ['\"]([A-Za-z0-9_][\w.+-]*)['\"] executable (?:was |is )?not found\b"),  # meson/skbuild
    re.compile(r"configure: error: Cannot find ([A-Za-z0-9_][\w.+-]*)"),                     # autoconf "Cannot find X"
    re.compile(r"configure: error: ([A-Za-z0-9_][\w.+-]*) not found\b"),                     # autoconf "X not found"
    re.compile(r"checking for ([A-Za-z0-9_][\w.+()-]*)\.\.\.\s*(?:not found|no)\b"),         # autoconf probe (…no)
    re.compile(r"Could not find ([A-Za-z0-9_][\w.+-]*)\b(?!\s*[:=])"),                       # cmake find_program
    re.compile(r"Program(?: or command)? ['\"]?([A-Za-z0-9_][\w.+-]*)['\"]? not found or not executable"),  # meson
    re.compile(r"which: no ([A-Za-z0-9_][\w.+-]*) in \("),                                   # which
    re.compile(r"error: command ['\"]([A-Za-z0-9_][\w.+-]*)['\"] failed:\s*No such file or directory\b"),  # distutils errno=2
)

PKGCONFIG_RES = (
    re.compile(r"No package ['\"]([A-Za-z0-9][\w.+-]*)['\"] found"),                          # pkg-config (quotes REQUIRED)
    re.compile(r"Package ([A-Za-z0-9][\w.+-]*) was not found in the pkg-config search path"), # pkg-config (unquoted tail is safe)
    re.compile(r"Package ['\"]([A-Za-z0-9][\w.+-]*)['\"], required by ['\"][\w:.+-]+['\"], not found"),  # transitive
    re.compile(r'Dependency "([A-Za-z0-9][\w.+-]*)" not found, tried (?=[a-z, ]*pkgconfig)[a-z, ]+'),    # meson (pkgconfig-gated)
    re.compile(r"Dependency '([A-Za-z0-9][\w.+-]*)' not found(?!, tried)"),                   # meson simple fallback
    re.compile(r"--\s*No package '([A-Za-z0-9][\w.+-]*)' found"),                             # cmake pkg_check_modules echo
    re.compile(r"None of the required ['\"]([A-Za-z0-9][\w.+;-]*)['\"] found"),               # meson alternatives (split on ';')
)

LINKER_RES = (
    re.compile(r"(?m)^\s*/?(?:usr/bin/)?ld(?:\.(?:bfd|gold|lld))?:\s*cannot find -l([\w.+-]+)"),
)


def _norm_header(name: str) -> str:
    """Keep the name as printed; basename only an absolute or traversal path."""
    if name.startswith("/") or name.startswith("../") or "/../" in name:
        return os.path.basename(name)
    return name


def _line_of(text: str, pos: int, max_chars: int = 500) -> str:
    """The single line of ``text`` containing offset ``pos`` (evidence)."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    return text[start:end].strip()[:max_chars]


def extract_needs(stderr: str, *, context_hint: str = "build") -> list[ObservedNeed]:
    """See module docstring. Dedup by (kind, name), first-occurrence text order."""
    text = stderr or ""
    hits: list[tuple[int, str, str]] = []  # (position, kind, name)

    for rx in HEADER_RES:
        for m in rx.finditer(text):
            hits.append((m.start(1), "header", _norm_header(m.group(1))))
    for rx in BINARY_RES:
        for m in rx.finditer(text):
            name = m.group(1)
            kind = "header" if name.endswith(_HDR_EXTS) else "binary"
            hits.append((m.start(1), kind, name))
    for rx in PKGCONFIG_RES:
        for m in rx.finditer(text):
            for alt in m.group(1).split(";"):
                alt = alt.strip()
                if alt:
                    hits.append((m.start(1), "pkgconfig", alt))
    for rx in SONAME_RES:
        for m in rx.finditer(text):
            hits.append((m.start(1), "soname", m.group(1)))
    for rx in LINKER_RES:
        for m in rx.finditer(text):
            hits.append((m.start(1), "linker_lib", m.group(1)))

    hits.sort(key=lambda h: h[0])
    seen: set[tuple[str, str]] = set()
    needs: list[ObservedNeed] = []
    for pos, kind, name in hits:
        key = (kind, name)
        if key in seen:
            continue
        seen.add(key)
        context = context_hint if kind == "binary" else default_context(kind)
        needs.append(
            ObservedNeed(kind=kind, name=name, context=context, evidence=_line_of(text, pos))
        )
    return needs
