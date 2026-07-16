"""Unit tests for ``_symbols_from_ast`` (src/python_deps/import_graph.py).

Captures, per top-level import, the attributes/from-names the code uses on it.
Those used-symbols later feed the install-lane LLM dist-guesser (a look-alike
disambiguator).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Put <repo>/src on the path so ``python_deps.*`` resolves without installation
# (mirrors the pattern used by tests/depgraph/conftest.py and other top-level
# test files that import from python_deps).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.import_graph import _symbols_from_ast  # noqa: E402


def test_attribute_access():
    src = "import cv2\ncv2.imread('x')\ncv2.VideoCapture(0)\n"
    assert _symbols_from_ast(src)["cv2"] == {"VideoCapture", "imread"}


def test_from_import_names():
    src = "from cv2 import imread, VideoCapture\n"
    assert _symbols_from_ast(src)["cv2"] == {"VideoCapture", "imread"}


def test_alias_resolved_to_top_level():
    src = "import numpy as np\nnp.array([1])\n"
    assert _symbols_from_ast(src)["numpy"] == {"array"}


def test_no_symbols_for_bare_import():
    assert _symbols_from_ast("import cv2\n") == {"cv2": set()}
