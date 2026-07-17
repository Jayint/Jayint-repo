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

from graph.python.read.scan import SKIP_WALK_DIRS


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


def _init_dir_source_paths(repo_path: str) -> dict[str, str]:
    """Repo-relative ``__init__.py`` path, keyed by the directory basename the
    BROAD walk harvests for it (``scan.local_module_names`` adds
    ``os.path.basename(dirpath)`` for every directory containing
    ``__init__.py`` -- including the repo root itself).

    Used only to give a value to the residual names :func:`stem_collisions`
    cannot resolve to a dotted module name -- see there for why the repo root
    is the one directory whose own basename never becomes a
    :class:`ModuleDef` leaf. Deterministic tie-break: if two directories ever
    shared a basename, the lexicographically-first path wins.
    """
    repo = Path(repo_path)
    paths: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_WALK_DIRS]
        if "__init__.py" not in filenames:
            continue
        name = os.path.basename(dirpath)
        rel = (Path(dirpath) / "__init__.py").relative_to(repo).as_posix()
        previous = paths.get(name)
        if previous is None or rel < previous:
            paths[name] = rel
    return paths


def stem_collisions(repo_path: str) -> dict[str, str]:
    """Names the BROAD walk harvests that are NOT importable top-levels.

    ``scan.local_module_names`` collects every ``.py`` stem and ``__init__`` dir
    basename anywhere in the tree -- INCLUDING the repo's own root directory
    when the root itself has an ``__init__.py`` (a project checked out into a
    directory that happens to look like a package). The difference between
    that set and :func:`top_level_names` is the COLLISION ZONE: names like
    wagtail's ``azure`` (really ``wagtail...backends.azure``), typer's
    ``items`` (really ``tutorial001.items``) -- and that repo-root name.

    The repo-root case is a collision, not a top-level, for the same reason as
    the others: it is NOT importable by that name. Importing it would require
    the repo's *parent* directory on ``sys.path``, which never happens --
    pytest puts the repo ROOT on ``sys.path``, making its CHILDREN importable,
    never the root itself (``_module_for`` returns ``None`` for the root's own
    ``__init__.py`` precisely because its dotted name would be empty). If this
    name fell through to EXTERNAL instead, the repair loop would ``pip
    install`` a same-named PyPI package to fix what is really a
    PYTHONPATH/rootdir problem -- the exact wrong-install failure this module
    exists to prevent.

    A collision is NOT decidable statically. ``azure`` is a real missing PyPI
    package; ``items`` is a sibling script reachable only because its directory
    lands on ``sys.path[0]`` when ``main.py`` is run directly. Both look
    identical to any tree walk -- the difference is HOW the importer was loaded,
    a runtime fact. The router therefore routes collisions to ``AMBIGUOUS`` and
    attaches this mapping as evidence, rather than deciding.

    Keys are EXACTLY::

        {n for n in local_module_names(repo) - top_level_names(repo) if "." not in n}

    -- NOT the full set difference. The sole consumer (``classify_locality``,
    and the ``is_local_import`` it replaces) looks a key up by
    ``import_name.split(".", 1)[0]``. That expression can return a name
    containing any character EXCEPT a dot, so the key space is exactly the
    dotless BROAD names, and the ONLY safe filter is a dot filter.

    A dotted BROAD name is genuinely unreachable: ``pkg/foo.bar.py`` contributes
    the BROAD name ``foo.bar`` (``scan.local_module_names`` takes the filename
    minus ``.py``), but no ``split(".", 1)[0]`` can ever produce ``foo.bar``, so
    such a key could never be matched. Dropping it is correct.

    **Do NOT tighten this to ``str.isidentifier()``.** That was tried and it
    opened a hole. A non-identifier name is NOT unreachable: ``import foo-bar``
    is a SyntaxError, but ``importlib.import_module("foo-bar")`` is legal and
    raises ``ModuleNotFoundError: No module named 'foo-bar'`` -- whose top-level
    segment is ``foo-bar``, a perfectly good lookup key. Under an identifier
    filter, ``pkg/foo-bar.py`` would leave ``foo-bar`` out of the collision map,
    the router would classify it ``EXTERNAL``, and the repair loop would install
    a PyPI package named ``foo-bar``. The old over-broad guard suppressed that
    name; the dot filter preserves the suppression, an identifier filter does not.

    The filter is applied identically in both loops below, so no dotted name can
    enter the dict by either path. The leaf-derived loop covers every dotless
    name sourced from a ``.py`` file and every non-root package directory's own
    basename (a directory's own ``__init__.py`` always inserts that directory's
    name into its dotted path, so its basename always surfaces as *some*
    module's leaf) -- so whatever dotless BROAD name still isn't a key after that
    loop can only be the repo-root name, added directly from the ``__init__.py``
    path that produced it.

    Returns ``{bare_name: value}``. ``value`` is the real dotted module name
    when one exists (e.g. ``"wagtail.contrib.backends.azure"``); for the
    repo-root residual -- which has no importable dotted name -- it is instead
    the repo-relative path of the file that produced the name (e.g.
    ``"__init__.py"``). Both read correctly when interpolated into the repair
    prompt as ``the repo DOES define the file {value!r}``.

    Precedence is deterministic but is NOT simple global lexicographic-first:
    the leaf-derived loop runs first and always wins over the repo-root path
    fallback, even when the fallback's own value would sort first -- a real
    dotted module name is strictly better repair evidence than a bare
    ``__init__.py`` path, so once a name is keyed by the leaf loop the
    residual loop never revisits it. Only WITHIN the leaf loop, among several
    leaf-derived candidates for the same bare name, does the
    lexicographically-first dotted name win.
    """
    from graph.python.read.scan import local_module_names

    modules = repo_modules(repo_path)
    tops = frozenset(m.dotted.split(".", 1)[0] for m in modules)
    broad = local_module_names(repo_path)

    evidence: dict[str, str] = {}
    for module in modules:
        # The LEAF segment is what the broad walk harvests: a .py stem
        # (`azure` from `...backends.azure`) or a package dir basename (`backends`
        # from its own `__init__.py`, whose dotted name ends in `backends`).
        # A module's TOP-level segment is by definition in `tops`, so it can never
        # be a collision — only the leaf can. The dot filter excludes a name that
        # could never BE the top-level segment of any import (the only thing a key
        # is matched against) -- see the docstring's `foo.bar.py` example. It is
        # deliberately NOT `isidentifier()`: see the docstring's `foo-bar` hole.
        leaf = module.dotted.rsplit(".", 1)[-1]
        if leaf in broad and leaf not in tops and "." not in leaf:
            previous = evidence.get(leaf)
            if previous is None or module.dotted < previous:
                evidence[leaf] = module.dotted

    # Residual: BROAD names the leaf loop above cannot reach. Provably just the
    # repo-root case (see docstring) among dotless names -- but computed here
    # rather than hardcoded, so the invariant holds even if that proof's premises
    # ever shift. Dotted residues (e.g. a dotted-stem file's broad name) are
    # dropped here too: they can never be looked up by
    # `import_name.split(".", 1)[0]`, so keeping them would be unreachable dead
    # data. Dotless non-identifiers (`foo-bar`) are KEPT -- they ARE reachable,
    # via importlib.import_module.
    residual = {n for n in broad - tops - evidence.keys() if "." not in n}
    if residual:
        init_paths = _init_dir_source_paths(repo_path)
        for name in sorted(residual):
            path = init_paths.get(name)
            if path is not None:
                evidence[name] = path
    return evidence
