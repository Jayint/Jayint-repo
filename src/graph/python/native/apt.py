"""apt provider resolution + release-aware apt-name verification.

Folds the two apt-facing native leaves (3c-3):
  * ``os_resolver`` — the ``ObservedNeed`` -> apt provider pipeline (table fast-path
    then apt-file fallback), the ``ObservedNeed`` / ``ProviderCandidate`` shapes, and
    ``capability_id`` / ``check_command_for`` / ``resolve`` / ``PROVIDER_TABLE``;
  * ``apt_verify`` — release-aware apt-name verification (the t64 ABI remap) that
    rewrites stale predicted/table names against the target image.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, replace

from graph.contracts.executor import Executor
from graph.ids import capability_id as _capability_id
from graph.model import DepGraph, Node


# === os_resolver: ObservedNeed -> apt provider (capability-keyed) ===
VALID_KINDS: frozenset[str] = frozenset(
    {"soname", "header", "binary", "pkgconfig", "linker_lib"}
)


@dataclass(frozen=True)
class ObservedNeed:
    kind: str            # "soname" | "header" | "binary" | "pkgconfig"
    name: str
    context: str = ""    # "build" | "runtime" | "unknown"; "" -> derive from kind
    owner_node_id: str | None = None
    evidence: str = ""
    strength: str = "strong"   # "strong" | "curated" | "weak"

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"invalid kind: {self.kind!r}")


@dataclass(frozen=True)
class ProviderCandidate:
    manager: str         # "apt" in V1
    package: str
    source: str          # "table" | "apt-file"
    matched_path: str = ""
    check_command: str = ""
    score: int = 0


def capability_id(need: ObservedNeed) -> str:
    """Capability node id for a need — what predict and observe reconcile on."""
    return _capability_id(need.kind, need.name)


def default_context(kind: str) -> str:
    """Structural context per kind; ``binary`` is the only ambiguous kind."""
    return {
        "header": "build",
        "pkgconfig": "build",
        "soname": "runtime",
        "linker_lib": "build",
    }.get(kind, "unknown")


# capability -> apt provider. Self-contained literal (unifies the former
# NATIVE_LIB_TO_APT (sonames) and TOOL_TO_APT (tools/headers) plus PoC-verified
# build-dep entries; those tables were deleted in Task 7). All confirmed on
# debian:bookworm / ubuntu:24.04 (see spec PoC results).
PROVIDER_TABLE: dict[tuple[str, str], str] = {
    # runtime sonames (was NATIVE_LIB_TO_APT)
    ("soname", "libGL.so.1"): "libgl1",
    ("soname", "libgthread-2.0.so.0"): "libglib2.0-0",
    ("soname", "libglib-2.0.so.0"): "libglib2.0-0",
    ("soname", "libSM.so.6"): "libsm6",
    ("soname", "libXext.so.6"): "libxext6",
    ("soname", "libXrender.so.1"): "libxrender1",
    ("soname", "libxcb.so.1"): "libxcb1",
    ("soname", "libpq.so.5"): "libpq5",
    ("soname", "libgomp.so.1"): "libgomp1",
    ("soname", "libGLU.so.1"): "libglu1-mesa",
    # build-time tools/headers (was TOOL_TO_APT)
    ("binary", "pg_config"): "libpq-dev",
    ("binary", "mysql_config"): "default-libmysqlclient-dev",
    ("binary", "gcc"): "build-essential",
    ("binary", "g++"): "build-essential",
    ("binary", "make"): "build-essential",
    ("binary", "cc"): "build-essential",
    ("header", "Python.h"): "python3-dev",
    # PoC-verified build-dep entries
    ("header", "libpq-fe.h"): "libpq-dev",
    ("pkgconfig", "cairo"): "libcairo2-dev",
    ("pkgconfig", "glib-2.0"): "libglib2.0-dev",
    ("header", "openssl/ssl.h"): "libssl-dev",
    ("header", "portaudio.h"): "portaudio19-dev",
    # re-added build-dep providers (container-verified bookworm+trixie 2026-07-03)
    ("header", "sql.h"): "unixodbc-dev",
    ("header", "ldap.h"): "libldap-dev",
    ("header", "sasl/sasl.h"): "libsasl2-dev",
    ("binary", "curl-config"): "libcurl4-openssl-dev",
    ("pkgconfig", "dbus-1"): "libdbus-1-dev",
    ("header", "snappy.h"): "libsnappy-dev",
    # the pkg-config binary itself (container-verified: /usr/bin/pkg-config is
    # provided by pkgconf on both debian:bookworm and debian:trixie)
    ("binary", "pkg-config"): "pkgconf",
}


# Path prefixes kept per kind (apt-file substring-matches, so its output is noisy).
_KIND_KEEP_PREFIXES = {
    "header": ("/usr/include/",),
    "binary": ("/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/"),
}


def parse_apt_file_rows(stdout: str) -> list[tuple[str, str]]:
    """``apt-file search`` output -> [(package, path)], preserving order."""
    rows: list[tuple[str, str]] = []
    for line in (stdout or "").splitlines():
        if ":" not in line:
            continue
        pkg, _, path = line.partition(":")
        pkg, path = pkg.strip(), path.strip()
        if pkg and path:
            rows.append((pkg, path))
    return rows


def filter_by_kind(rows: list[tuple[str, str]], need: ObservedNeed) -> list[tuple[str, str]]:
    """Keep only rows whose path shape fits the need kind."""
    if need.kind == "soname":
        # A multiarch/system lib dir with basename == the exact soname.
        return [
            (p, path) for (p, path) in rows
            if os.path.basename(path) == need.name
            and (path.startswith("/usr/lib/") or path.startswith("/lib/"))
        ]
    if need.kind == "pkgconfig":
        target = f"{need.name}.pc"
        return [
            (p, path) for (p, path) in rows
            if os.path.basename(path) == target and "/pkgconfig/" in path
        ]
    if need.kind == "linker_lib":
        target = f"lib{need.name}.so"
        return [
            (p, path) for (p, path) in rows
            if os.path.basename(path) == target
            and (path.startswith("/usr/lib/") or path.startswith("/lib/"))
        ]
    prefixes = _KIND_KEEP_PREFIXES.get(need.kind, ())
    target = os.path.basename(need.name)  # slashed headers -> match on basename
    return [
        (p, path) for (p, path) in rows
        if os.path.basename(path) == target and path.startswith(prefixes)
    ]


def _prefers_dev(need: ObservedNeed) -> bool:
    ctx = need.context or default_context(need.kind)
    if need.kind in ("header", "pkgconfig", "linker_lib"):
        return True
    if need.kind == "soname":
        return False
    return ctx == "build"  # binary


def rank(rows: list[tuple[str, str]], need: ObservedNeed) -> list[tuple[str, str]]:
    """Order candidates: canonical path, -dev preference, shorter name."""
    prefers_dev = _prefers_dev(need)
    slashed = need.kind == "header" and "/" in need.name

    def key(row: tuple[str, str]) -> tuple:
        pkg, path = row
        is_dev = pkg.endswith("-dev")
        depth = path.count("/")
        if slashed:
            canonical = path == f"/usr/include/{need.name}"
        elif need.kind == "header":
            canonical = path == f"/usr/include/{need.name}"
        else:
            canonical = True
        return (
            0 if canonical else 1,            # canonical path wins
            0 if is_dev == prefers_dev else 1,  # -dev preference honored
            depth,                            # shallower (less vendored) wins
            len(pkg), pkg,                    # shorter name breaks ties
        )

    return sorted(rows, key=key)


def check_command_for(need: ObservedNeed, matched_path: str = "") -> str:
    """Host check that certifies the capability (design 3.1)."""
    if need.kind == "soname":
        return f"ldconfig -p | grep {need.name}"
    if need.kind == "pkgconfig":
        return f"pkg-config --exists {need.name}"
    if need.kind == "binary":
        return f"command -v {need.name}"
    if need.kind == "linker_lib":
        target = shlex.quote(f"lib{need.name}.so")
        return f"find /usr/lib /lib -name {target} 2>/dev/null | grep -q ."
    # header
    if matched_path:
        return f"test -f {matched_path}"
    # No exact path (table hit / prediction): search the standard include roots.
    # A slashed header keeps its suffix (openssl/ssl.h -> */openssl/ssl.h); a bare
    # header matches by basename anywhere under the include roots (covers e.g.
    # /usr/include/postgresql/libpq-fe.h and /usr/include/python3.11/Python.h).
    roots = "/usr/include /usr/local/include"
    if "/" in need.name:
        pat = shlex.quote("*/" + need.name)
        return f"find {roots} -path {pat} 2>/dev/null | grep -q ."
    return f"find {roots} -name {shlex.quote(need.name)} 2>/dev/null | grep -q ."


def _candidate(
    need: ObservedNeed, package: str, *, source: str, matched_path: str = "",
) -> ProviderCandidate:
    return ProviderCandidate(
        manager="apt",
        package=package,
        source=source,
        matched_path=matched_path,
        check_command=check_command_for(need, matched_path),
    )


# Multiarch triplet probe — uses Python's own sysconfig (always present in the
# target image) instead of ``gcc -print-multiarch``: gcc is absent in slim base
# images, which is exactly where the multiarch-path filter must still work.
_MULTIARCH_CMD = (
    "python -c \"import sysconfig; "
    "print(sysconfig.get_config_var('MULTIARCH') or '')\""
)


def multiarch_triplet(executor: Executor) -> str | None:
    """Container's multiarch triplet (``x86_64-linux-gnu``), or None on failure."""
    result = executor.run(_MULTIARCH_CMD)
    triplet = (result.stdout or "").strip() if result.ok else ""
    return triplet or None


# Timeouts for the three-step lazy apt-file provisioning sequence.
_APT_UPDATE_TIMEOUT = 120   # apt-get update (~10-20 s cold, bounded at 120)
_APT_INSTALL_TIMEOUT = 120  # apt-get install -y apt-file (small binary)
_APT_FILE_UPDATE_TIMEOUT = 180  # apt-file update (~50 MB Contents index)


def ensure_apt_file(executor: Executor) -> bool:
    """Install and index apt-file in the container if absent.

    Uses ``command -v apt-file`` as the readiness check — the container
    filesystem is the cache (once installed, the binary is on PATH and the
    check succeeds on every subsequent call, so the install block is skipped).
    Returns True when apt-file is ready to use.  Returns False on any failure
    (no network, non-Debian, apt-get absent); the caller then returns
    (None, "unresolved") — never worse than today.
    """
    if executor.run("command -v apt-file").ok:
        return True
    for cmd, timeout in [
        ("apt-get update", _APT_UPDATE_TIMEOUT),
        ("apt-get install -y apt-file", _APT_INSTALL_TIMEOUT),
        ("apt-file update", _APT_FILE_UPDATE_TIMEOUT),
    ]:
        if not executor.run(cmd, timeout=timeout).ok:
            return False
    return True


def resolve(need: ObservedNeed, executor: "Executor | None" = None) -> list[ProviderCandidate]:
    """Ordered provider candidates for ``need``. Pure — no install, no state.

    Table fast-path first (no container). On a miss, the apt-file fallback runs
    only when an ``executor`` is present (observe path); prediction
    (``executor=None``) that misses the table returns ``[]`` — the caller then
    seeds an unresolved node rather than fabricating a package.
    """
    apt = PROVIDER_TABLE.get((need.kind, need.name))
    if apt is not None:
        return [_candidate(need, apt, source="table")]
    if executor is None or not ensure_apt_file(executor):
        return []
    if need.kind == "pkgconfig":
        query = f"{need.name}.pc"
    elif need.kind == "linker_lib":
        query = f"lib{need.name}.so"
    else:
        query = need.name
    result = executor.run(f"apt-file search {shlex.quote(query)}")
    if not result.ok:
        return []
    rows = rank(filter_by_kind(parse_apt_file_rows(result.stdout), need), need)
    return [
        _candidate(need, pkg, source="apt-file", matched_path=path)
        for pkg, path in rows
    ]


# === apt_verify: release-aware apt-name verification (t64 remap) ===
def apt_name_installable(stdout: str) -> bool:
    """True when ``apt-cache show <name>`` returned a real package record.

    A missing package yields empty stdout (+ an ``N: Unable to locate`` notice on
    stderr), so the presence of a ``Package:`` field is the reliable signal.
    """
    return any(line.startswith("Package:") for line in (stdout or "").splitlines())


def t64_variant(name: str) -> str | None:
    """The Debian time_t-64 transition appends ``t64`` to the runtime-lib package
    (``libglib2.0-0`` -> ``libglib2.0-0t64``). ``None`` when already a t64 name."""
    if name.endswith("t64"):
        return None
    return name + "t64"


_SHOWPKG_REVERSE_PROVIDES = "Reverse Provides:"


def parse_showpkg_reverse_provides(stdout: str) -> list[str]:
    """Extract provider package names from ``apt-cache showpkg`` stdout.

    Locates the ``Reverse Provides:`` section and returns the list of provider
    package names found there (one per ``<name> <version>`` entry, order
    preserved, duplicates included — callers must dedup). Returns an empty list
    when the section is absent or empty.
    """
    lines = (stdout or "").splitlines()
    in_section = False
    providers: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_section:
            if stripped.startswith(_SHOWPKG_REVERSE_PROVIDES):
                in_section = True
            continue
        # A line ending with ":" is the next section header — stop. (An empty
        # string can never satisfy ``endswith(":")``, so no extra guard is needed
        # — review LOW-1.)
        if stripped.endswith(":"):
            break
        if stripped:
            parts = stripped.split()
            if parts:
                providers.append(parts[0])
    return providers


def resolve_virtual_provider(name: str, executor: Executor) -> str | None:
    """Real provider of a virtual/renamed package via ``apt-cache showpkg``
    'Reverse Provides' (e.g. ``libglib2.0-0`` -> ``libglib2.0-0t64``), else None.

    ``showpkg`` always exits 0; the decision is based on output content, not
    return code. Multiple 'Reverse Provides' lines for different versions of the
    *same* provider are deduplicated. Returns the unique provider name when
    exactly one distinct provider exists, else None (zero or more-than-one).
    """
    result = executor.run(f"apt-cache showpkg {shlex.quote(name)}")
    providers = parse_showpkg_reverse_provides(result.stdout)
    unique = list(dict.fromkeys(providers))  # dedup, preserve insertion order
    return unique[0] if len(unique) == 1 else None


def resolve_installable_apt_name(candidate: str, executor: Executor) -> str:
    """Return an apt package name that actually exists on the target image.

    Resolution order:

    1. Candidate as-is (``apt-cache show``).
    2. Virtual-provider lookup via ``apt-cache showpkg`` Reverse Provides —
       authoritative remap for virtual/renamed packages (e.g. the t64 ABI
       transition) without relying on a suffix heuristic.
    3. t64 suffix heuristic (``<name>t64``) as a last-resort fallback.

    Best-effort: returns the original candidate unchanged when none resolves
    (never worse than the table name).
    """
    # 1. Candidate installable as-is.
    result = executor.run(f"apt-cache show {shlex.quote(candidate)}")
    if apt_name_installable(result.stdout):
        return candidate

    # 2. Virtual-provider resolution via showpkg (authoritative remap).
    provider = resolve_virtual_provider(candidate, executor)
    if provider is not None:
        result = executor.run(f"apt-cache show {shlex.quote(provider)}")
        if apt_name_installable(result.stdout):
            return provider

    # 3. t64 suffix heuristic (last resort; kept for robustness).
    t64 = t64_variant(candidate)
    if t64 is not None:
        result = executor.run(f"apt-cache show {shlex.quote(t64)}")
        if apt_name_installable(result.stdout):
            return t64

    return candidate


def _apt_package(node: Node) -> str | None:
    """The apt package in a node's first ``apt:`` fix-candidate, else None."""
    for fix in node.fix_candidates:
        if fix.startswith("apt:"):
            return fix[len("apt:") :]
    return None


def reconcile_apt_names(graph: DepGraph, executor: Executor) -> DepGraph:
    """Verify every node's ``apt:`` fix-candidate against the target image and
    remap stale names (e.g. the t64 transition). Returns a NEW graph.

    Runs ``apt-get update`` once so apt metadata is available on slim images
    (which ship empty lists). No-op — and no ``apt-get update`` — when the graph
    has no apt fix-candidates, so pure-Python repos pay nothing.

    Only the ``fix_candidates`` (the actionable part) are rewritten; a node named
    by its apt package (a seed prediction) also has its ``name``/``chosen_fix``
    and its ``dpkg -s <name>`` ``check_command`` updated (else certification would
    forever check the stale name and report MISSING after the correct package is
    installed), while a soname-named node (e.g. ``libGL.so.1``) keeps its identity
    and its ``ldconfig``-based check (which is release-independent).
    """
    apt_nodes = [(n, pkg) for n in graph.nodes if (pkg := _apt_package(n))]
    if not apt_nodes:
        return graph

    executor.run("apt-get update")  # best-effort; slim images ship empty lists
    resolved: dict[str, str] = {}
    new = graph
    for node, pkg in apt_nodes:
        if pkg not in resolved:
            resolved[pkg] = resolve_installable_apt_name(pkg, executor)
        fixed = resolved[pkg]
        if fixed == pkg:
            continue
        changes: dict = {"fix_candidates": (f"apt:{fixed}",)}
        if node.name == pkg:  # named by its apt package -> a seed prediction
            changes["name"] = fixed
        if node.chosen_fix == f"apt:{pkg}":
            changes["chosen_fix"] = f"apt:{fixed}"
        # Remap a seed node's apt-name check too; leave soname/ldconfig checks
        # (and any non-dpkg check) untouched.
        if node.check_command == f"dpkg -s {pkg}":
            changes["check_command"] = f"dpkg -s {fixed}"
        new = new.with_node(replace(node, **changes))
    return new
