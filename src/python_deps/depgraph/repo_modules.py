"""Sys.path-accurate repo module walk.

Answers ONE question: which top-level module names does this repository
actually define? That is NOT "which .py stems exist" — since Python 3, absolute
imports mean ``jupyterhub/traitlets.py`` is ``jupyterhub.traitlets`` and bare
``import traitlets`` resolves to PyPI, never to that file.

The rule is CPython/pytest's basedir algorithm:

    From a .py file, climb while the directory has ``__init__.py``, OR while the
    directory's PARENT has ``__init__.py`` (a directory with no ``__init__.py``
    whose parent has one is a PEP 420 namespace package -- NOT a sys.path root).
    The first directory failing both is the sys.path root; the dotted name is the
    path from there. Never climb above the repo root.

The parent-climb clause is load-bearing: ``src/flask/sansio/`` has no
``__init__.py`` but is a real subpackage (``flask.sansio.app``). Without it the
walk stops there and mints the top-level ``app`` -- exactly the generic-name
pollution this module exists to eliminate.

CONSUMER WARNING. This set is NARROWER than ``scan.local_module_names``. It is
correct for DIAGNOSIS (deciding whether a failing import is ours) but MUST NOT
replace the construction-time drop in ``scan.scan_to_nodes``: there, a
false-external reaches Phase-A's identity candidate ladder, which will install
an identically-named real PyPI distribution (typer's ``items``, netbox's
``extras`` are both real packages). See
``docs/superpowers/specs/2026-07-13-local-module-resolution-fixes.md``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from python_deps.depgraph.scan import SKIP_WALK_DIRS


@dataclass(frozen=True)
class ModuleDef:
    """One importable module defined by the repo."""

    sys_path_root: str   # repo-relative dir that must be on sys.path ("." for root)
    dotted: str          # importable name from that root, e.g. "flask.sansio.app"
    path: str            # repo-relative file path, e.g. "src/flask/sansio/app.py"


def _has_init(directory: Path) -> bool:
    return (directory / "__init__.py").is_file()


def _module_for(file_path: Path, repo: Path) -> ModuleDef | None:
    parts: list[str] = []
    current = file_path.parent
    # The repo root is ALWAYS a terminal sys.path root: `while current != repo`
    # is what makes "never climb above the repo root" structural rather than a
    # check we could forget. A repo whose own root has __init__.py must NOT have
    # its own directory name consumed as a package segment -- that would walk out
    # of the tree and `relative_to(repo)` below would raise ValueError.
    while current != repo:
        # Climb through a package dir, and through a PEP 420 namespace dir (no
        # __init__.py of its own, but its parent has one -> still inside a
        # package, not a sys.path root). Without the second clause,
        # `src/flask/sansio/` reads as a root and mints the top-level `app`.
        if _has_init(current) or _has_init(current.parent):
            parts.insert(0, current.name)
            current = current.parent
            continue
        break

    stem = file_path.stem
    segments = parts if stem == "__init__" else parts + [stem]
    dotted = ".".join(segments)
    if not dotted:
        return None

    root = "." if current == repo else current.relative_to(repo).as_posix()
    return ModuleDef(
        sys_path_root=root,
        dotted=dotted,
        path=file_path.relative_to(repo).as_posix(),
    )


def repo_modules(repo_path: str) -> tuple[ModuleDef, ...]:
    """Every module the repo defines, with its sys.path root and dotted name.

    Uncapped by design -- ``import_graph._iter_python_files`` caps at 1000 files
    and netbox has 1,184, which would drop its core ``extras`` app from the set.
    """
    repo = Path(repo_path)
    found: list[ModuleDef] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_WALK_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            module = _module_for(Path(dirpath) / filename, repo)
            if module is not None:
                found.append(module)
    return tuple(found)


def top_level_names(repo_path: str) -> frozenset[str]:
    """The bare names a repo module can actually be imported by.

    ``jupyterhub/traitlets.py`` contributes ``jupyterhub`` -- NOT ``traitlets``.
    """
    return frozenset(m.dotted.split(".", 1)[0] for m in repo_modules(repo_path))
