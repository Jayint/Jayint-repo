"""python_deps and graph must never import envstate (the Slice-C boundary)."""
from __future__ import annotations

import pathlib
import re

import graph
import python_deps

_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:src\.)?envstate\b", re.MULTILINE)


def test_no_envstate_import_in_pure_layers():
    roots = [pathlib.Path(python_deps.__file__).parent, pathlib.Path(graph.__file__).parent]
    offenders = []
    for root in roots:
        for py in root.rglob("*.py"):
            for m in _IMPORT.finditer(py.read_text()):
                offenders.append(f"{py}: {m.group(0).strip()}")
    assert not offenders, offenders
