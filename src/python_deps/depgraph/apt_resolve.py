"""Dynamic soname -> apt resolution (curated table first, apt-file fallback).

Pure parser + thin executor orchestrator, mirroring resolve.py. The curated
``tables.apt_for_soname`` is the offline authority; ``apt-file search`` resolves
sonames the table does not know about. Build tools/headers deliberately stay on
the curated table (it encodes metapackages apt-file cannot return), so only the
soname path has a dynamic fallback. Debian/Ubuntu only.
"""

from __future__ import annotations

import os
import re
import shlex

from python_deps.depgraph.executor import Executor
from python_deps.depgraph.tables import apt_for_soname


def parse_apt_file_search(stdout: str, soname: str, triplet: str | None) -> str | None:
    """Pick the apt package that ships exactly the multiarch ``soname``.

    ``apt-file search`` does a substring match, so its output is noisy (subdirs,
    cross-compile dirs, ``-gdb.py`` autoload scripts). Keep only a line whose path
    is ``/usr/lib/<triplet>/<soname>`` (basename == soname exactly). When
    ``triplet`` is unknown, accept exactly one multiarch dir under ``/usr/lib``.
    Prefer a runtime package over ``-dev``/``-dbg``, then the shortest name.
    """
    candidates: list[str] = []
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        pkg, _, path = line.partition(":")
        pkg = pkg.strip()
        path = path.strip()
        if not pkg or os.path.basename(path) != soname:
            continue
        if triplet is not None:
            if path != f"/usr/lib/{triplet}/{soname}":
                continue
        elif not re.fullmatch(rf"/usr/lib/[^/]+/{re.escape(soname)}", path):
            continue
        if pkg not in candidates:
            candidates.append(pkg)
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.endswith("-dev"), p.endswith("-dbg"), len(p), p))
    return candidates[0]


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


def resolve_soname_apt(soname: str, executor: Executor) -> tuple[str | None, str]:
    """Resolve a ``.so`` soname to an apt package: table first, then apt-file.

    The curated table is authoritative and offline, so a hit short-circuits before
    any executor call. On a miss, query ``apt-file search`` in the container and
    filter to the exact multiarch path. Any failure (apt-file absent, no match)
    returns ``(None, "unresolved")`` — never worse than today's table-only path.
    """
    hit = apt_for_soname(soname)
    if hit:
        return hit, "table"
    triplet = multiarch_triplet(executor)
    result = executor.run(f"apt-file search {shlex.quote(soname)}")
    if not result.ok:
        return None, "unresolved"
    pkg = parse_apt_file_search(result.stdout, soname, triplet)
    if pkg:
        return pkg, "apt-file"
    return None, "unresolved"
