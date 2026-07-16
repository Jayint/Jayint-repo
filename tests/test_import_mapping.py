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

from python_deps.import_mapping import (  # noqa: E402
    MappingResult,
    UNRESOLVED_SOURCE,
    declared_metadata_match,
    is_unresolved,
    map_import_to_package,
    unresolved_result,
)


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


def test_github_import_maps_to_pygithub():
    # bare identity fallback would wrongly return "github" (a defunct/wrong
    # PyPI sdist); the correct distribution is PyGithub.
    r = map_import_to_package("github", declared_package_names=set())
    assert r.package_name == "PyGithub"
    assert r.source == "collision_table"


def test_crypto_import_maps_to_pycryptodome():
    r = map_import_to_package("Crypto", declared_package_names=set())
    assert r.package_name == "pycryptodome"
    assert r.source == "collision_table"


def test_is_unresolved_true_for_unresolved_source():
    r = unresolved_result("somemod")
    assert r.import_name == "somemod"
    assert r.package_name is None
    assert r.source == UNRESOLVED_SOURCE
    assert r.trust == "none"
    assert is_unresolved(r) is True


def test_is_unresolved_false_for_a_real_mapping():
    r = MappingResult("yaml", "PyYAML", "collision_table", "high")
    assert is_unresolved(r) is False


def test_unmapped_import_is_unresolved_not_identity():
    # An import with no curated-table entry and no declared match must NOT be
    # guessed as its own name (the old identity fallback); it is unresolved.
    r = map_import_to_package("box", declared_package_names=set())
    assert r.package_name is None
    assert r.source == "unresolved"
    assert is_unresolved(r) is True


# --------------------------------------------------------------------------- #
# FIX 2 (B3) — declared_metadata_match factored out of map_import_to_package
# so `depgraph.repair.generate_candidates` can reuse the SAME rung as an
# evidence-grounded candidate source (never a fresh guess).
# --------------------------------------------------------------------------- #
def test_declared_metadata_match_returns_normalized_hit():
    assert declared_metadata_match("freezegun", {"freezegun"}) == "freezegun"


def test_declared_metadata_match_none_when_no_hit():
    assert declared_metadata_match("freezegun", {"other-pkg"}) is None


def test_declared_metadata_match_none_when_declared_is_none():
    assert declared_metadata_match("freezegun", None) is None


def test_declared_metadata_match_normalizes_separators():
    # `import django_filters` should match a manifest-declared "django-filters".
    assert (
        declared_metadata_match("django_filters", {"django-filters"})
        == "django-filters"
    )


def test_declared_metadata_match_uses_top_level_only():
    assert declared_metadata_match("dateutil.parser", {"dateutil"}) == "dateutil"


def test_map_import_to_package_still_uses_declared_metadata_match_rung():
    # map_import_to_package's own declared_metadata rung must remain intact
    # after the refactor that factors it out for repair.py's reuse.
    r = map_import_to_package("myinternalthing", declared_package_names={"MyInternalThing"})
    assert r.package_name == "MyInternalThing"
    assert r.source == "declared_metadata"
    assert r.trust == "medium"
