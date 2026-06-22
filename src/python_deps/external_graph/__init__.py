"""ExternalDependencyGraphSlice package.

Public re-exports so that the existing import path
'from src.python_deps.external_graph import ...' keeps working UNCHANGED.

Internal layout:
  dto.py        — ExternalDependencyGraphSlice DTO + sorting + is_empty + to_dict
  rendering.py  — to_flat_hint_impl (flat-bullet renderer)
  budget.py     — trim_to_budget_impl + cap helpers
  builder.py    — build_external_dependency_graph_slice (thin pipeline)
  projectors.py — failure-type projectors + ConstraintGraph + SolverResult projectors

See docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md for the full design.
"""
from .builder import build_external_dependency_graph_slice
from .dto import (
    ExternalDependencyGraphSlice,
    _BUDGET_BYTES,
    _MAX_EDGES,
    _MAX_NODES,
    _public_nodes,
    _sorted_edges,
    _sorted_nodes,
)

__all__ = [
    # Primary public API
    "ExternalDependencyGraphSlice",
    "build_external_dependency_graph_slice",
    # Constants (also used in tests via _BUDGET_BYTES)
    "_BUDGET_BYTES",
    "_MAX_NODES",
    "_MAX_EDGES",
    # Internal helpers re-exported for any test that imports them directly
    "_public_nodes",
    "_sorted_nodes",
    "_sorted_edges",
]
