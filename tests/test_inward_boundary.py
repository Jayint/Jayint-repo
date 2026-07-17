"""Phase-1 inward-boundary tripwires (src/ stage-refactor §4D / §7.4 / §10).

Filesystem-based on purpose — these scan the ``src/`` tree as TEXT and import
none of the packages under test, so they hold regardless of pytest partition or
collection order (unlike ``test_purity.py``, which imports ``graph``/``python_deps``
to reach their ``__file__``). Three seals:

1. **graph/ is LLM-client-free** — the load-bearing "construction is LLM-free"
   invariant (spec §4B/§5). The LLM enters construction ONLY through injected
   guesser callables (``llm_dist_guess``), never as an ambient import.
2. **the agnostic seam is Python-free** — ``graph/contracts/provider.py`` (the
   ``EcosystemProvider`` Protocol) and ``graph/model.py`` (the ``DepGraph`` model)
   import no ``graph.python.*``, so a future Rust provider depends on the Protocol +
   model without pulling Python (§4D isolation). NB: ``graph/contracts/registry.py``
   legitimately imports the concrete ``PythonProvider`` for its dispatch tuple —
   the invariant is on the Protocol/model, NOT the whole ``contracts/`` dir.
3. **no old-path imports** — nothing under ``src/`` imports the deleted
   ``ecosystems.*`` package or the five ``python_deps.*`` root utils moved into
   ``graph/python`` in Phase 1. Bans a re-export shim and proves the old homes are
   truly gone (§10 no-leftovers).
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_GRAPH = _SRC / "graph"


def _py_files(base: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


_IMPORT_LINE = re.compile(r"^[ \t]*(?:from|import)[ \t]+\S.*$", re.MULTILINE)


def _import_lines(text: str) -> list[str]:
    return [m.group(0) for m in _IMPORT_LINE.finditer(text)]


# 1 ── graph/ imports no LLM client ─────────────────────────────────────────────
_LLM_CLIENT = re.compile(
    r"(?:from|import)\s+(?:\S*\.)?(?:llm_response|openai|anthropic|litellm|llm_client)\b"
    r"|(?:from|import)\s+(?:src\.)?llm\b"
)


def test_graph_imports_no_llm_client() -> None:
    offenders = []
    for py in _py_files(_GRAPH):
        for line in _import_lines(py.read_text(encoding="utf-8", errors="ignore")):
            if _LLM_CLIENT.search(line):
                offenders.append(f"{py.relative_to(_ROOT)}: {line.strip()}")
    assert offenders == [], f"graph must be LLM-client-free (spec §4B/§5): {offenders}"


# 2 ── the agnostic seam (Protocol + model) is graph.python-free ─────────────────
_GRAPH_PYTHON = re.compile(r"(?:from|import)\s+(?:src\.)?graph\.python\b")


def test_agnostic_seam_is_python_free() -> None:
    seam = [_GRAPH / "contracts" / "provider.py", _GRAPH / "model.py"]
    offenders = []
    for py in seam:
        assert py.is_file(), f"expected agnostic-seam file missing: {py}"
        for line in _import_lines(py.read_text(encoding="utf-8", errors="ignore")):
            if _GRAPH_PYTHON.search(line):
                offenders.append(f"{py.relative_to(_ROOT)}: {line.strip()}")
    assert offenders == [], (
        "the EcosystemProvider Protocol + DepGraph model must not import "
        f"graph.python (§4D Rust isolation): {offenders}"
    )


# 3 ── no old-path imports (ecosystems + the 5 moved python_deps utils) ──────────
_OLD_PATH = re.compile(
    r"(?<![\w.])ecosystems\.(?:base|registry|python)\b"
    r"|(?:from|import)\s+ecosystems\b"
    r"|(?<![\w.])python_deps\.(?:import_mapping|failure_classifier|evidence|import_graph|models)\b"
)


def test_no_ecosystems_or_moved_python_deps_imports() -> None:
    offenders = []
    for py in _py_files(_SRC):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for m in _OLD_PATH.finditer(text):
            offenders.append(f"{py.relative_to(_ROOT)}: …{m.group(0).strip()}…")
    assert offenders == [], f"old-home imports must be gone (§10 no-shim): {offenders}"
