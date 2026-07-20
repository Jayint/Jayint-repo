"""Task 2 — static import scan -> Import + Test nodes (scan.py)."""

from __future__ import annotations

from pathlib import Path

from graph.model import TEST_NODE_ID, import_id
from graph.python.read.scan import scan_to_nodes
from graph.model import (
    DiscoveredBy,
    EdgeType,
    Layer,
    NodeType,
    State,
)


def _write_fixture_repo(root: Path) -> None:
    """A tiny repo: one external native import, one aliased external import,
    and one stdlib import that must be excluded."""
    (root / "vision.py").write_text(
        "import cv2\nimport os\n\n\ndef load():\n    return cv2\n",
        encoding="utf-8",
    )
    (root / "imaging.py").write_text(
        "from PIL import Image\n\n\ndef open_image(path):\n    return Image.open(path)\n",
        encoding="utf-8",
    )


def test_external_imports_become_import_nodes(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)

    graph = scan_to_nodes(str(tmp_path))

    cv2 = graph.get(import_id("cv2"))
    pil = graph.get(import_id("PIL"))
    assert cv2 is not None
    assert pil is not None

    for node in (cv2, pil):
        assert node.type is NodeType.IMPORT
        assert node.layer is Layer.NAMING
        assert node.discovered_by is DiscoveredBy.STATIC_SCAN
        assert node.state is State.UNKNOWN

    assert cv2.name == "cv2"
    assert cv2.check_command == 'python -c "import cv2"'
    assert pil.check_command == 'python -c "import PIL"'


def test_scan_mints_stdlib_raw_classifier_drops_it(tmp_path: Path) -> None:
    """THE FLIP: scan does NOT consult stdlib (that host signal would drop a
    host-only-stdlib name that is a real TARGET external — §17). ``os`` is minted RAW
    and the classifier's TARGET-stdlib rung drops it via ``apply_routing``."""
    from graph.python.route.classify import classify, apply_routing

    _write_fixture_repo(tmp_path)

    raw = scan_to_nodes(str(tmp_path))
    assert raw.get(import_id("os")) is not None      # scan mints stdlib RAW

    routing = classify(str(tmp_path), target_stdlib=frozenset({"os"}), declared=frozenset())
    routed = apply_routing(raw, routing)
    assert routed.get(import_id("os")) is None        # classifier (TARGET stdlib) drops it
    assert routed.get(import_id("cv2")) is not None    # externals survive


def test_provenance_records_source_file(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)

    graph = scan_to_nodes(str(tmp_path))

    cv2 = graph.get(import_id("cv2"))
    assert cv2 is not None
    assert cv2.provenance is not None
    assert "vision.py" in cv2.provenance


def test_scan_no_longer_mints_the_test_hub(tmp_path: Path) -> None:
    """THE FLIP: ``scan`` no longer mints the ``Test`` goal node or the flat
    ``Test -> Import`` hub — the goal node is minted downstream by
    ``_add_project_node`` and the imports are connected via the config-lane spine.
    ``scan`` now returns ONLY external Import nodes and NO edges."""
    _write_fixture_repo(tmp_path)

    graph = scan_to_nodes(str(tmp_path))

    assert graph.get(TEST_NODE_ID) is None                       # no Test node
    assert all(n.type is NodeType.IMPORT for n in graph.nodes)   # imports only
    assert graph.edges == ()                                     # no hub edges
    # scan mints RAW (incl. stdlib ``os``); the classifier drops stdlib downstream.
    assert {n.id for n in graph.nodes} == {import_id("cv2"), import_id("PIL"), import_id("os")}


def test_scan_mints_stdlib_imports_raw(tmp_path: Path) -> None:
    """Scan no longer consults stdlib: a stdlib-only file yields raw Import nodes for
    ``os``/``sys`` (the classifier's TARGET-stdlib rung drops them later)."""
    (tmp_path / "plain.py").write_text("import os\nimport sys\n", encoding="utf-8")

    graph = scan_to_nodes(str(tmp_path))

    assert {n.name for n in graph.nodes if n.type is NodeType.IMPORT} == {"os", "sys"}
    assert graph.edges == ()   # no hub edges


def _external_after_routing(repo: str) -> frozenset[str]:
    """THE names that reach the install lane after routing: ``classify.external``.
    Post-flip the SCOPING/drops moved from ``scan`` to the classifier, so scoping is
    verified through the classifier (an out-of-scope name is never clear-external)."""
    from graph.python.route.classify import classify
    return classify(repo, target_stdlib=frozenset({"os", "sys"}), declared=frozenset()).external


def _routed_import_names(repo: str) -> set[str]:
    """Import-node names surviving ``apply_routing`` (unroutable names removed)."""
    from graph.python.route.classify import classify, apply_routing
    routing = classify(repo, target_stdlib=frozenset({"os", "sys"}), declared=frozenset())
    g = apply_routing(scan_to_nodes(repo), routing)
    return {n.name for n in g.nodes if n.type is NodeType.IMPORT}


def test_scan_scopes_out_examples_and_docs(tmp_path: Path) -> None:
    """THE FLIP relocated scoping to the classifier: ``scan`` mints examples/docs
    imports RAW, and the classifier keeps them OUT of the install lane (never
    clear-external), so an out-of-scope name is still never a dep."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("import requests\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("import pytest\n", encoding="utf-8")
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "demo.py").write_text(
        "import blueprintapp\n", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "conf.py").write_text("import sphinx_only\n", encoding="utf-8")

    external = _external_after_routing(str(tmp_path))
    assert "requests" in external  # src kept
    assert "pytest" in external  # tests kept
    assert "blueprintapp" not in external  # examples-only -> not clear-external (dropped)
    assert "sphinx_only" not in external  # docs-only -> not clear-external (dropped)


def test_scan_scopes_out_tools_dir(tmp_path: Path) -> None:
    """Imports seen ONLY under a repo-root tools/ dev-tooling dir never reach the
    install lane (closes Finding B for vizro: `import github` lives in tools/pycafe/,
    CI/docs tooling outside every installable package). Post-flip the classifier owns
    this scoping."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("import requests\n", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "helper.py").write_text("import click\n", encoding="utf-8")

    external = _external_after_routing(str(tmp_path))
    assert "requests" in external  # src kept
    assert "click" not in external  # tools-only -> not clear-external (dropped)


def test_scan_mints_local_fixture_raw_and_classifier_removes_typing(tmp_path: Path) -> None:
    """THE FLIP (route-not-drop): ``scan`` mints first-party/local names RAW (the four
    pre-flip drops relocate to the classifier). A ``_``-prefixed typing-only module is
    minted by scan and then REMOVED by ``apply_routing`` (rung-0 drop). A local fixture
    package is minted and ROUTED (never clear-external, so it never installs its PyPI
    namesake)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "import requests\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import _typeshed\n",
        encoding="utf-8",
    )
    # a local fixture package nested under tests/ (flask's blueprintapp pattern)
    fixtures = tmp_path / "tests" / "test_apps" / "blueprintapp"
    fixtures.mkdir(parents=True)
    (fixtures / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_x.py").write_text(
        "import blueprintapp\nimport requests\n", encoding="utf-8"
    )

    # scan mints RAW: _typeshed and blueprintapp are present (drops relocated).
    raw = {n.name for n in scan_to_nodes(str(tmp_path)).nodes if n.type is NodeType.IMPORT}
    assert {"requests", "_typeshed", "blueprintapp"} <= raw

    # After routing: _typeshed removed (rung-0), blueprintapp NOT clear-external.
    routed = _routed_import_names(str(tmp_path))
    external = _external_after_routing(str(tmp_path))
    assert "requests" in routed and "requests" in external
    assert "_typeshed" not in routed          # rung-0 private drop -> removed
    assert "blueprintapp" not in external     # local -> never installs its namesake
