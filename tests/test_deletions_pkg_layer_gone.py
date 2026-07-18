"""Sentinel: the pkg_layer cluster (Phase 2.5 T1) and the python_deps package
(Phase 2.5 T2) were shed in the src/ stage-refactor.

The parked four-plane prototype (``src/python_deps/pkg_layer/``), its only non-test
consumer (``src/eval/graph_fidelity/pkg_layer_ab.py``), and their tests were deleted --
the verifier-roots idea had already shipped into production
``graph/python/lanes/install/roots.py`` (§10). Phase 2.5 T2 then dissolved the residual
``python_deps`` package itself (its pure construction code had moved into ``graph/`` in
Phase 1). Filesystem-based (imports nothing) so it holds regardless of pytest partition.
Both must stay gone: a re-add or a fresh importer trips this.
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SELF = pathlib.Path(__file__).resolve()


def _import_lines(text: str):
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            yield s


# -- Phase 2.5 T1: the pkg_layer eval cluster --------------------------------------
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


# -- Phase 2.5 T2: the python_deps package itself ----------------------------------
_PYDEPS_IMPORT = re.compile(r"^(?:import|from)\s+python_deps\b")


def test_python_deps_package_is_gone() -> None:
    assert not (_ROOT / "src" / "python_deps").exists()


def test_no_python_deps_importers() -> None:
    offenders = []
    for base in ("src", "tests"):
        for py in (_ROOT / base).rglob("*.py"):
            if "__pycache__" in py.parts or py.resolve() == _SELF:
                continue
            for s in _import_lines(py.read_text(encoding="utf-8", errors="ignore")):
                if _PYDEPS_IMPORT.match(s):
                    offenders.append(f"{py.relative_to(_ROOT)}: {s}")
    assert offenders == [], f"python_deps package is dissolved -- no importer may remain: {offenders}"
