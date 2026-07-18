"""Phase-2 outward-boundary tripwires (src/ stage-refactor §4A/§4C/§8/§10).

Filesystem-based on purpose — these scan the ``src/`` tree as TEXT and import
none of the packages under test, so they hold regardless of pytest partition or
collection order (see ``test_inward_boundary.py`` for the same rationale). Four seals:

1. **agent/ imports only its three seams** — the repair arm may reach only ``graph``
   (the graph API + the ``Executor`` protocol in ``graph.contracts.executor``), the
   ``src.llm`` client leaf, the ``src.constants`` leaf, and its own siblings. The
   "three fakes" litmus (§4A): if it imported the outer loop, the old homes, or a raw
   LLM client, it would no longer be unit-testable with {graph, Runner, llm} fakes.
2. **no graph -> orchestrate edge** — ``graph/`` is the island (§4C direction test):
   ``orchestrate`` imports ``graph``/``agent`` and calls them in order; neither imports it.
3. **graph/ imports no Docker SDK** — construction does no container orchestration via
   the ``docker`` package. (``graph/executors.py``'s ``DockerExecutor`` shells out with
   ``subprocess``, never ``import docker``; physically moving it to the run stage is a
   Phase-3 inject-refactor. The graph-is-LLM-free half lives in ``test_inward_boundary``.)
4. **no old-home imports** — nothing under ``src/`` imports the deleted ``envstate`` or
   ``react_repair`` packages. Bans a re-export shim and proves the old homes are gone (§10).
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_GRAPH = _SRC / "graph"
_AGENT = _SRC / "agent"


def _py_files(base: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


_IMPORT_LINE = re.compile(r"^[ \t]*(?:from|import)[ \t]+\S.*$", re.MULTILINE)


def _import_lines(text: str) -> list[str]:
    return [m.group(0) for m in _IMPORT_LINE.finditer(text)]


# 1 ── agent/ imports only {graph, Executor, src.llm, src.constants, agent} ──────
# Any import that names a repo-internal package must resolve to the allowed set;
# everything else (stdlib / third-party) is ignored by the internal-package gate.
_INTERNAL_IMPORT = re.compile(
    r"^[ \t]*(?:from|import)[ \t]+"
    r"((?:src\.)?(?:graph|agent|orchestrate|envstate|react_repair|eval|python_deps|"
    r"ecosystems|manifest_builder|bench_emit|sandbox|synthesizer|llm|constants|"
    r"run_oracle|repo2run_dataset)\b[\w.]*)"
)
_AGENT_ALLOWED = re.compile(r"^(?:src\.)?graph\b|^src\.agent\b|^src\.llm\b|^src\.constants\b")


def test_agent_imports_only_the_allowed_seams() -> None:
    offenders = []
    for py in _py_files(_AGENT):
        for line in _import_lines(py.read_text(encoding="utf-8", errors="ignore")):
            m = _INTERNAL_IMPORT.match(line)
            if m and not _AGENT_ALLOWED.match(m.group(1)):
                offenders.append(f"{py.relative_to(_ROOT)}: {line.strip()}")
    assert offenders == [], (
        "agent/ may import only graph, the Executor protocol, src.llm and src.constants "
        f"(§4A three-fakes litmus): {offenders}"
    )


# 2 ── no graph/* imports orchestrate/* (the direction tripwire) ──────────────────
_GRAPH_TO_ORCH = re.compile(r"^[ \t]*(?:from|import)[ \t]+(?:src\.)?orchestrate\b")


def test_graph_does_not_import_orchestrate() -> None:
    offenders = []
    for py in _py_files(_GRAPH):
        for line in _import_lines(py.read_text(encoding="utf-8", errors="ignore")):
            if _GRAPH_TO_ORCH.match(line):
                offenders.append(f"{py.relative_to(_ROOT)}: {line.strip()}")
    assert offenders == [], f"graph/ must not import orchestrate/ (§4C direction): {offenders}"


# 3 ── graph/ imports no docker SDK ───────────────────────────────────────────────
_DOCKER_SDK = re.compile(r"^[ \t]*(?:from|import)[ \t]+docker\b")


def test_graph_imports_no_docker_sdk() -> None:
    offenders = []
    for py in _py_files(_GRAPH):
        for line in _import_lines(py.read_text(encoding="utf-8", errors="ignore")):
            if _DOCKER_SDK.search(line):
                offenders.append(f"{py.relative_to(_ROOT)}: {line.strip()}")
    assert offenders == [], f"graph/ must not import the docker SDK (§8): {offenders}"


# 4 ── no imports of the deleted envstate / react_repair homes ─────────────────────
_OLD_HOME = re.compile(r"^[ \t]*(?:from|import)[ \t]+src\.(?:envstate|react_repair)\b")


def test_no_old_home_imports() -> None:
    offenders = []
    for py in _py_files(_SRC):
        for line in _import_lines(py.read_text(encoding="utf-8", errors="ignore")):
            if _OLD_HOME.match(line):
                offenders.append(f"{py.relative_to(_ROOT)}: {line.strip()}")
    assert offenders == [], f"the envstate/react_repair homes are gone — no shim (§10): {offenders}"
