"""Sentinel: the ``pkg_layer`` eval cluster was shed in Phase 2.5 of the src/ stage-refactor.

The parked four-plane prototype (``src/python_deps/pkg_layer/``), its only non-test
consumer (``src/eval/graph_fidelity/pkg_layer_ab.py``), and their tests were deleted --
the verifier-roots idea had already shipped into production
``graph/python/lanes/install/roots.py`` (§10). Filesystem-based (imports nothing) so it
holds regardless of pytest partition. The cluster must stay gone: a re-add or a fresh
importer trips this. Task 2 extends this file to guard the ``python_deps`` package itself.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SELF = pathlib.Path(__file__).resolve()


def _import_lines(text: str):
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            yield s


def test_pkg_layer_cluster_is_gone() -> None:
    assert not (_ROOT / "src" / "python_deps" / "pkg_layer").exists()
    assert not (_ROOT / "src" / "eval" / "graph_fidelity" / "pkg_layer_ab.py").exists()
    assert not (_ROOT / "tests" / "pkg_layer").exists()
    assert not (_ROOT / "tests" / "eval" / "graph_fidelity" / "test_pkg_layer_ab.py").exists()


def test_no_pkg_layer_importers() -> None:
    offenders = []
    for base in ("src", "tests"):
        for py in (_ROOT / base).rglob("*.py"):
            if "__pycache__" in py.parts or py.resolve() == _SELF:
                continue
            for s in _import_lines(py.read_text(encoding="utf-8", errors="ignore")):
                if "python_deps.pkg_layer" in s or "pkg_layer_ab" in s:
                    offenders.append(f"{py.relative_to(_ROOT)}: {s}")
    assert offenders == [], f"pkg_layer cluster must have no importers: {offenders}"
