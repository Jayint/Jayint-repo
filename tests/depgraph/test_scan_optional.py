"""P0.2 — try/except-ImportError guarded imports are tagged ``optional``.

The scan tags an Import node ``data["optional"] = True`` when the import is
lexically guarded by a ``try`` whose ``except`` catches ImportError-family (or a
bare / ``Exception`` handler).  This is a pure *tag*: it changes no flagging
behaviour (that is P0.3).  A hard runtime need for the same name dominates a
guarded one (mixed-dominance → NOT optional).
"""

from __future__ import annotations

from pathlib import Path

from python_deps.depgraph.ids import import_id
from python_deps.depgraph.scan import scan_to_nodes


def test_optional_import_tagged(tmp_path: Path) -> None:
    """``try: import ujson / except ImportError`` → Import node is optional."""
    (tmp_path / "opt.py").write_text(
        "try:\n"
        "    import ujson\n"
        "except ImportError:\n"
        "    ujson = None\n",
        encoding="utf-8",
    )

    graph = scan_to_nodes(str(tmp_path))

    node = graph.get(import_id("ujson"))
    assert node is not None
    assert node.data.get("optional") is True


def test_optional_import_modulenotfounderror_and_from(tmp_path: Path) -> None:
    """``ModuleNotFoundError`` handler + ``from x import y`` shape also count."""
    (tmp_path / "opt.py").write_text(
        "try:\n"
        "    from ujson import dumps\n"
        "except ModuleNotFoundError:\n"
        "    dumps = None\n",
        encoding="utf-8",
    )

    graph = scan_to_nodes(str(tmp_path))

    node = graph.get(import_id("ujson"))
    assert node is not None
    assert node.data.get("optional") is True


def test_hard_import_not_tagged(tmp_path: Path) -> None:
    """A plain module-top ``import requests`` leaves ``data`` falsy."""
    (tmp_path / "app.py").write_text("import requests\n", encoding="utf-8")

    graph = scan_to_nodes(str(tmp_path))

    node = graph.get(import_id("requests"))
    assert node is not None
    assert not node.data.get("optional")


def test_mixed_dominance_hard_wins(tmp_path: Path) -> None:
    """``requests`` imported hard in a.py and guarded in b.py → NOT optional."""
    (tmp_path / "a.py").write_text("import requests\n", encoding="utf-8")
    (tmp_path / "b.py").write_text(
        "try:\n"
        "    import requests\n"
        "except ImportError:\n"
        "    requests = None\n",
        encoding="utf-8",
    )

    graph = scan_to_nodes(str(tmp_path))

    node = graph.get(import_id("requests"))
    assert node is not None
    assert not node.data.get("optional")


def test_wrong_handler_not_tagged(tmp_path: Path) -> None:
    """``try/except ValueError`` around an import is NOT a guard → not optional."""
    (tmp_path / "app.py").write_text(
        "try:\n"
        "    import foo\n"
        "except ValueError:\n"
        "    foo = None\n",
        encoding="utf-8",
    )

    graph = scan_to_nodes(str(tmp_path))

    node = graph.get(import_id("foo"))
    assert node is not None
    assert not node.data.get("optional")


def test_regex_fallback_defaults_not_optional(tmp_path: Path) -> None:
    """A file with a syntax error falls back to regex import scanning; with no
    AST context the imports default to ``optional=False`` (never a false tag)."""
    (tmp_path / "broken.py").write_text(
        "import regexmod\n"
        "\n"
        "def broken(\n",  # unterminated def -> SyntaxError -> regex fallback
        encoding="utf-8",
    )

    graph = scan_to_nodes(str(tmp_path))

    node = graph.get(import_id("regexmod"))
    assert node is not None
    assert not node.data.get("optional")
