"""Contract Graph — a planner-facing reasoning layer inside WorldModelMap.

Eager package re-exports restored after the concise-contract-graph v2 rewrite.
We use PEP 562 lazy __getattr__ so that importing a submodule (e.g. contracts.graph)
does NOT trigger loading projection.py or render.py at package-init time, which
would create a circular import with world_model.py.  Callers that do
  from src.envstate.contracts import refresh_host_graph
get the real function on first access and subsequent accesses are free.
"""
from __future__ import annotations

__all__ = [
    "refresh_host_graph",
    "render_graph_for_planner",
    "serialize_graph_for_maintainer",
]

_LAZY: dict[str, str] = {
    "refresh_host_graph": ".projection",
    "render_graph_for_planner": ".render",
    "serialize_graph_for_maintainer": ".render",
}


def __getattr__(name: str) -> object:
    if name in _LAZY:
        import importlib
        mod = importlib.import_module(_LAZY[name], package=__name__)
        obj = getattr(mod, name)
        # cache on the module so repeated access is fast
        globals()[name] = obj
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
