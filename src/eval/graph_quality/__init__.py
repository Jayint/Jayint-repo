"""Graph-quality eval -- see docs/superpowers/specs/2026-07-13-graph-quality-eval-design.md."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]  # src/eval/graph_quality/ -> repo root (depth 3)
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
