"""Unit tests for map_import_to_package (src/python_deps/import_mapping.py).

Focused tests for the curated collision table — distinct from test_naming.py
which tests the higher-level package_roots() pipeline in naming.py.
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

from python_deps.import_mapping import map_import_to_package  # noqa: E402


def test_socketio_import_maps_to_python_socketio():
    r = map_import_to_package("socketio")
    assert r.package_name == "python-socketio"
    assert r.source == "collision_table"


def test_factory_import_maps_to_factory_boy():
    r = map_import_to_package("factory")
    assert r.package_name == "factory-boy"
    assert r.source == "collision_table"


def test_curated_entry_beats_a_conflicting_manifest_declaration():
    # even if the manifest declares the bare squatter, the curated mapping wins
    r = map_import_to_package("socketio", declared_package_names={"socketio"})
    assert r.package_name == "python-socketio"


def test_image_does_not_map_to_pillow():
    # the `image` PyPI distribution is NOT Pillow; Pillow is `PIL`/`pil`
    assert map_import_to_package("image").package_name != "Pillow"


def test_pil_still_maps_to_pillow():
    assert map_import_to_package("pil").package_name == "Pillow"


def test_pdfminer_import_maps_to_pdfminer_six():
    r = map_import_to_package("pdfminer")
    assert r.package_name == "pdfminer.six"
    assert r.source == "collision_table"


def test_fpdf_import_maps_to_modern_fpdf2_provider():
    r = map_import_to_package("fpdf")
    assert r.package_name == "fpdf2"
    assert r.source == "collision_table"
