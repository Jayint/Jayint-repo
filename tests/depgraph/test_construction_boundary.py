"""The precise (diagnosis-only) module set must NEVER reach construction.

``repo_modules.top_level_names`` is NARROWER than ``scan.local_module_names``
(wagtail: 4 names vs 757). That is correct for DIAGNOSIS -- there a false-*local*
is a silent give-up on a real environment failure -- and CATASTROPHIC for
CONSTRUCTION: a false-*external* reaches Phase-A's identity candidate ladder,
which will ACCEPT and install an identically-named real PyPI distribution.
typer's ``items`` and netbox's ``extras`` are both real packages on PyPI.

Construction's over-breadth is therefore the correct, conservative bias.
Narrowing it is the failure that killed two prior designs of this feature.

Two guards, one structural and one behavioral. If either fails, do NOT relax it.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from graph.core import build
from graph.python.lanes.install import roots
from graph.python.read import scan

_FORBIDDEN = frozenset({"top_level_names", "stem_collisions", "repo_modules"})


def _referenced_names(source: str) -> set[str]:
    """Names a module actually REFERENCES in code -- not in comments or prose.

    AST-based on purpose. A substring scan over the raw source cannot tell code
    from documentation, and ``scan.py`` legitimately mentions ``repo_modules`` in
    a comment (the two walks must prune identically, so they are cross-referenced).
    Stripping ``#`` text to work around that is worse than the disease: it is not
    Python-aware, so a line like ``marker = "#"; x = repo_modules.top_level_names(p)``
    would be truncated at the hash INSIDE the string literal and the real call
    would sail through the guard.

    Collects every import (plain, aliased, from-import, relative) and every
    attribute base, which together cover the ways the precise set could be reached.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.update(node.module.split("."))
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def test_construction_never_references_the_diagnosis_only_precise_set():
    """Structural guard: no code path in construction may even reach the precise set."""
    for module in (scan, build, roots):
        referenced = _referenced_names(inspect.getsource(module))
        leaked = sorted(_FORBIDDEN & referenced)
        assert not leaked, (
            f"{module.__name__} references {leaked}. The precise module set is "
            f"DIAGNOSIS-ONLY. Using it in construction makes Phase-A install a "
            f"wrong PyPI package (typer `items`, netbox `extras` are real dists)."
        )


def test_ast_guard_actually_catches_a_reintroduction():
    """The guard above is worthless if it cannot fail. Prove each leak shape trips it.

    The last case is the one a substring-with-comment-stripping guard MISSES: the
    hash lives inside a string literal, so naive stripping truncates the line and
    the real call disappears.
    """
    leaks = (
        "from graph import repo_modules",
        "from graph.python.read.repo_modules import top_level_names",
        "import graph.python.read.repo_modules as rm",
        "local = rm.top_level_names(repo_path)",
        'marker = "#"; local = repo_modules.top_level_names(repo_path)',
    )
    for src in leaks:
        assert _FORBIDDEN & _referenced_names(src), f"guard failed to catch: {src!r}"

    # ...and does NOT trip on the legitimate prose cross-reference in scan.py.
    assert not (_FORBIDDEN & _referenced_names("# PUBLIC: `repo_modules` prunes identically\nx = 1"))


def test_scan_to_nodes_drops_a_nested_module_that_shadows_a_real_pypi_name(tmp_path):
    """Behavioral guard -- the one that matters.

    `pkg/items.py` is the module `pkg.items`; `items` is NOT an importable
    top-level. But `items` IS a real distribution on PyPI, and `scan_imports`
    classifies the bare `import items` as EXTERNAL (verified: it appears in the
    external findings for this exact tree). So the BROAD local-name drop in
    `scan_to_nodes` is the ONLY thing standing between this repo and Phase-A
    installing the wrong package.

    Swap the broad set for the precise set here and this test goes red -- which is
    the entire point. A source-text assertion that `_local_module_names(repo_path)`
    still appears would pass even if the call were left dead and its result unused.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "items.py").write_text("VALUE = 1\n")
    (tmp_path / "pkg" / "main.py").write_text("import items\nimport requests\n")

    node_ids = {n.id for n in scan.scan_to_nodes(str(tmp_path)).nodes}

    assert "import:items" not in node_ids, (
        "scan_to_nodes minted an Import node for `items`, a nested module (pkg.items) "
        "that shadows a real PyPI distribution. Phase-A's identity ladder will now "
        "ACCEPT and install it. Construction MUST keep using the over-broad "
        "scan.local_module_names, never the precise diagnosis-only set."
    )
    assert "import:requests" in node_ids, (
        "the drop is over-eager: a genuine external import vanished, which would "
        "make this test pass for the wrong reason"
    )
