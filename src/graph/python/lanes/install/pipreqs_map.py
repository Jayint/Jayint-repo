"""Vendored pipreqs import->distribution table as an untrusted candidate source.

The table (``data/pipreqs_mapping.txt``, from bndr/pipreqs, Apache-2.0) is a
best-effort community map; entries are NEVER trusted directly — every candidate
this module proposes is RECORD-grounded downstream (``repair.choose_provider``).
Only pipreqs' *table* is adopted; its ``data.get(pkg, pkg)`` identity fallback is
NOT (that is the wrong-install / self-install-false-green vector).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from python_deps.import_mapping import top_level_import_name

# Package-data lives at the graph package root (graph/data/), not beside this
# module — anchor to the "graph" ancestor so the path is robust to the module's
# depth within the package.
_GRAPH_ROOT = next(p for p in Path(__file__).parents if p.name == "graph")
_MAPPING_PATH = _GRAPH_ROOT / "data" / "pipreqs_mapping.txt"


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    table: dict[str, str] = {}
    text = _MAPPING_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        imp, dist = line.split(":", 1)
        # Key stays exact-case (real import spelling: Crypto, PIL, ...); the dist
        # VALUE is normalized to PEP 503 canonical lowercase so downstream RECORD
        # grounding gets a stable, case-insensitive name (e.g. PyYAML -> pyyaml).
        imp, dist = imp.strip(), dist.strip().lower()
        if imp and dist:
            table.setdefault(imp, dist)  # first wins (deterministic)
    return table


def pipreqs_candidates(import_name: str) -> list[str]:
    """``[distribution]`` for a known top-level import name, else ``[]``.

    Exact-case match on the top-level segment (pipreqs keys are real import
    spellings, e.g. ``Crypto``/``PIL``). Never echoes the import name back.
    """
    top = top_level_import_name(import_name)
    hit = _load().get(top)
    return [hit] if hit else []
