"""Python package-closure layer (``pkg_layer/``) — eval-only; graph never imports it.

Sealed by Phase 1 of the src/ stage-refactor: the shared read/util/model helpers
that graph construction depends on moved OUT of here into the construction island —
``import_mapping``/``failure_classifier`` -> ``graph/python/util``,
``evidence``/``import_graph`` -> ``graph/python/read``, ``models`` ->
``graph/python/models.py``. What remains is the four-plane ``pkg_layer/`` package
closure (Contract/Closure/Usage/Environment) consumed by ``eval`` and its tests;
it depends on ``graph`` and is out of graph's scope, so it stays here.
"""
from __future__ import annotations

__all__: list[str] = []
