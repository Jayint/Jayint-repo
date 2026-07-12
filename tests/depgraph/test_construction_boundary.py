"""The precise (diagnosis-only) module set must NEVER reach construction.

`repo_modules.top_level_names` is NARROWER than `scan.local_module_names`. That is
correct for DIAGNOSIS (a false-local is a silent give-up) and CATASTROPHIC for
CONSTRUCTION (a false-external reaches Phase-A's identity candidate ladder, which
will ACCEPT and install an identically-named real PyPI distribution -- typer's
`items` and netbox's `extras` are both real packages).

This test is the guard rail. If it fails, do NOT relax it.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph import build, roots, scan

_FORBIDDEN = ("top_level_names", "stem_collisions", "repo_modules")


def _strip_comments(source: str) -> str:
    """Drop ``#``-comment text before the substring check.

    ``scan.py``'s ``SKIP_WALK_DIRS`` docstring (Task 1, ``586817c``) legitimately
    cross-references ``repo_modules`` in prose -- explaining that the two walks
    must prune identically -- and that mention must not false-positive this
    guard. An actual `` import repo_modules`` or ``.top_level_names(`` call is
    real code and is never inside a ``#`` comment, so stripping comments only
    removes the prose case, never a genuine reference.
    """
    return "\n".join(line.split("#", 1)[0] for line in source.splitlines())


def test_construction_never_uses_the_diagnosis_only_precise_set():
    for module in (scan, build, roots):
        source = _strip_comments(inspect.getsource(module))
        for name in _FORBIDDEN:
            assert name not in source, (
                f"{module.__name__} references {name!r}. The precise module set is "
                f"DIAGNOSIS-ONLY. Using it in construction makes Phase-A install a "
                f"wrong PyPI package (typer `items`, netbox `extras`)."
            )


def test_scan_to_nodes_still_uses_the_broad_set():
    """The conservative drop must stay conservative."""
    source = inspect.getsource(scan.scan_to_nodes)
    assert "_local_module_names(repo_path)" in source
