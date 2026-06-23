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
