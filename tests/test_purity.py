"""graph must never import envstate (the Slice-C boundary).

python_deps was dissolved in Phase 2.5 of the src/ stage-refactor -- its pure
construction code moved into graph/ in Phase 1, so this is now a graph-only concern.
(The envstate half is itself going stale since Phase 2 dissolved envstate; retargeting
it to the graph-must-not-import-orchestrate/agent boundary is the Phase-2 T8
follow-up, tracked by tests/test_outward_boundary.py -- out of scope here.)
"""
from __future__ import annotations

import pathlib
import re

import graph

_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:src\.)?envstate\b", re.MULTILINE)


def test_no_envstate_import_in_pure_layers():
    roots = [pathlib.Path(graph.__file__).parent]
    offenders = []
    for root in roots:
        for py in root.rglob("*.py"):
            for m in _IMPORT.finditer(py.read_text()):
                offenders.append(f"{py}: {m.group(0).strip()}")
    assert not offenders, offenders
