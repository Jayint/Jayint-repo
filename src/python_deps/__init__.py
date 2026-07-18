"""python_deps — residual empty namespace (src/ stage-refactor §10).

Nothing lives here now. Phase 1 moved the shared read/util/model helpers graph
construction depends on into the construction island (``import_mapping``/
``failure_classifier`` -> ``graph/python/util``, ``evidence``/``import_graph`` ->
``graph/python/read``, ``models`` -> ``graph/python/models.py``). Phase 2.5 then shed
the last resident -- the four-plane ``pkg_layer/`` eval prototype, whose verifier-roots
idea had already shipped to production ``graph/python/lanes/install/roots.py``.

Kept only as the import target for ``tests/test_purity.py``; Phase 2.5 T2 dissolves it.
"""
from __future__ import annotations

__all__: list[str] = []
