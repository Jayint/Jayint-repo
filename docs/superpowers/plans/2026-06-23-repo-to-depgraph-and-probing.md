# Repo → Concrete Dependency Graph + Probing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a repo path and a base image, deterministically produce a **concrete, host-certified dependency graph** (the model in `docs/DESIGN-static-probe-certified-dependency-graph.md` §5): `Test/Import/Package/SystemLib/Tool` nodes joined by `requires` edges, where each node's `state` (`unknown|missing|satisfied`) is flipped **only** by running its `check_command`, and where `SystemLib`/`Tool` nodes are discovered by **probing** (real `pip install` + `python -c "import X"` + `ldd` in a container).

**Scope — IN:** static scan → Import nodes; import→distribution mapping → Package roots; a **real resolver (`uv`)** producing a pinned closure → Package nodes + transitive `requires`; an **Executor** that runs commands in a container; a **probe** stage that discovers `SystemLib`/`Tool` nodes from install/import/ldd output; a **Certifier** that runs `check_command`s and writes `state`; GraphML export viewable in the existing `docs/sample-dependency-graph-visualization.html`.

**Scope — OUT (explicit non-goals, deferred):** the LLM Planner / any LLM call (handlers are deterministic-only here; a table miss leaves a node `missing` with evidence); `Runtime` nodes (need a test run — Task 9 is an optional stretch); `conflicts_with` + Z3 resolution (the existing `z3_adapter.py` stack stays untouched); `alternative_to` swaps; Dockerfile emission / clean-rebuild promotion; the agent loop. This plan ends at "repo in → certified graph out (provisional, scratch-container scope)."

**Architecture:** A new package `src/python_deps/depgraph/` holding a typed, immutable concrete graph (frozen dataclasses; every mutation returns a new graph, per the repo's immutability rule) plus a staged builder. It **reuses** `python_deps.import_graph.scan_imports` (stage 1), `python_deps.import_mapping.map_import_to_package` (stage 2), and `python_deps.failure_classifier.NATIVE_LIBRARY_RE` (probe classification). It **does not** reuse `models.py` (constraint-solver oriented, no `state`) or `external_graph/` (dict nodes, prompt-facing) — those remain for the diagnosis/solver path. The Z3/metadata resolver (`resolver.py`, `z3_adapter.py`) is left intact and unused by this path.

**Tech Stack:** Python 3, frozen dataclasses + enums, pytest. New runtime dep: `uv` binary (invoked via the Executor; not imported). `docker` CLI for the container Executor (gated; tests use a fake/local Executor). Tests: `.venv/bin/python -m pytest tests/depgraph/ -q`. Conventional-commit messages. Suite green at the end of each task.

**Testability stance:** the Executor is an interface. Unit tests inject a `FakeExecutor` that returns canned `CommandResult`s keyed by command substring — so scan/map/resolve/probe/certify are all testable with **no Docker and no network**. Exactly one integration test (`@pytest.mark.docker`) exercises a real container and is skipped when Docker is absent.

---

## Shared Interfaces (keystone — every task conforms to these exact names)

### `src/python_deps/depgraph/schema.py` — enums, Node, Edge, DepGraph
```python
import enum
from dataclasses import dataclass, field, replace

class NodeType(enum.Enum):
    TEST = "Test"; IMPORT = "Import"; PACKAGE = "Package"
    SYSTEM_LIB = "SystemLib"; TOOL = "Tool"; RUNTIME = "Runtime"

class EdgeType(enum.Enum):
    REQUIRES = "requires"
    ALTERNATIVE_TO = "alternative_to"   # reserved; not emitted in this plan
    CONFLICTS_WITH = "conflicts_with"   # reserved; not emitted in this plan

class State(enum.Enum):                  # certification axis — host flips only
    UNKNOWN = "unknown"; MISSING = "missing"; SATISFIED = "satisfied"

class DiscoveredBy(enum.Enum):
    GOAL = "goal"; STATIC_SCAN = "static_scan"; RESOLVER = "resolver"
    PROBE = "probe"; RUNTIME = "runtime"

class Layer(enum.Enum):
    INTERPRETER = "interpreter"; SYSTEM = "system"; TOOLCHAIN = "toolchain"
    PIP = "pip"; NAMING = "naming"; RUNTIME = "runtime"; TESTS = "tests"

EDGE_RULES = {  # relation -> (allowed src types, allowed dst types)
    "requires": (
        frozenset({"Test", "Import", "Package"}),
        frozenset({"Import", "Package", "SystemLib", "Tool", "Runtime"}),
    ),
}

@dataclass(frozen=True)
class Attempt:
    command: str
    outcome: str            # "succeeded" | "failed" | "unknown"
    check: str = ""
    cycle: int = 0

@dataclass(frozen=True)
class Node:
    id: str
    type: NodeType
    name: str
    layer: Layer
    discovered_by: DiscoveredBy
    state: State = State.UNKNOWN
    version: str | None = None
    check_command: str | None = None
    evidence: str | None = None
    fix_candidates: tuple[str, ...] = ()
    chosen_fix: str | None = None
    attempts: tuple[Attempt, ...] = ()
    provenance: str | None = None
    discovered_cycle: int = 0
    certified_cycle: int | None = None
    # immutable updates:
    def with_state(self, state, *, evidence=None, cycle=None) -> "Node": ...
    def with_attempt(self, attempt: Attempt) -> "Node": ...

@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    relation: EdgeType = EdgeType.REQUIRES
    origin: str | None = None   # "scan" | "resolver" | "probe"

@dataclass(frozen=True)
class DepGraph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    def get(self, node_id: str) -> Node | None: ...
    def with_node(self, node: Node) -> "DepGraph": ...     # add or replace by id
    def with_edge(self, edge: Edge) -> "DepGraph": ...     # dedup by (src,dst,relation)
    def requires_of(self, node_id: str) -> tuple[Node, ...]: ...   # successors via requires
    def required_by(self, node_id: str) -> tuple[Node, ...]: ...   # predecessors
    def to_dict(self) -> dict: ...
```

### `src/python_deps/depgraph/ids.py`
```python
def slug(text: str) -> str
def import_id(name: str) -> str        # f"import:{name}"
def package_id(name: str, version: str | None) -> str   # f"pkg:{name}=={version}" or f"pkg:{name}"
def syslib_id(soname: str) -> str      # f"syslib:{soname}"
def tool_id(tool: str) -> str          # f"tool:{tool}"
TEST_NODE_ID = "test:repo_tests_pass"
```

### `src/python_deps/depgraph/executor.py`
```python
@dataclass(frozen=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    @property
    def ok(self) -> bool: return self.returncode == 0

class Executor(typing.Protocol):
    def run(self, command: str, *, timeout: int = 300) -> CommandResult: ...

class LocalSubprocessExecutor:          # runs in the current venv/host (CI-friendly)
    def run(self, command, *, timeout=300) -> CommandResult: ...

class DockerExecutor:                   # docker run a long-lived container, exec into it
    def __init__(self, image: str, *, network: bool = True): ...
    def __enter__(self) -> "DockerExecutor": ...   # create container
    def __exit__(self, *exc): ...                  # remove container
    def run(self, command, *, timeout=300) -> CommandResult: ...
```

### `src/python_deps/depgraph/tables.py`
```python
# .so soname -> apt package (extends, does not import, failure_classifier)
NATIVE_LIB_TO_APT: dict[str, str]     # "libGL.so.1" -> "libgl1", ...
# build tool / header -> apt package
TOOL_TO_APT: dict[str, str]           # "pg_config" -> "libpq-dev", "gcc" -> "build-essential", ...
# distributions whose import may need a system lib (whom to deep-probe)
NATIVE_RISK_PACKAGES: frozenset[str]  # opencv-python, psycopg2, lxml, ...
def apt_for_soname(soname: str) -> str | None
def apt_for_tool(tool: str) -> str | None
```

### `src/python_deps/depgraph/build.py` — the orchestrator
```python
def build_dep_graph(repo_path: str, executor: Executor, *,
                    base_python: str = "3.11") -> DepGraph
# stages: scan -> map -> resolve -> seed Package/Import/Test nodes + requires
#         -> install closure -> probe (SystemLib/Tool) -> certify -> return graph
```

### `src/python_deps/depgraph/export.py`
```python
def to_graphml(graph: DepGraph) -> str   # same key schema as docs/sample-dependency-graph.graphml
```

---

## Tasks (dependency-ordered; TDD: write the failing test first)

### Task 1 — Graph model (`schema.py`, `ids.py`)
- [ ] `tests/depgraph/test_schema.py`: Node/Edge/DepGraph construct; `with_state`/`with_attempt` return **new** frozen instances (originals unchanged); `with_node` replaces by id; `with_edge` dedups; `requires_of`/`required_by` traverse correctly; `EDGE_RULES` rejects an illegal `requires` (e.g. `SystemLib -> Package`).
- [ ] Implement `schema.py` + `ids.py` to pass. No I/O, no deps.
- [ ] `to_dict()` round-trips enums to their `.value`.

### Task 2 — Stage 1: Import nodes from static scan (`scan.py`)
- [ ] `tests/depgraph/test_scan.py`: on a tiny fixture repo (a temp dir with `import cv2` / `from PIL import Image` / `import os`), produce Import nodes for `cv2`, `PIL` (stdlib `os` excluded), each `type=IMPORT, layer=NAMING, discovered_by=STATIC_SCAN, state=UNKNOWN`, `provenance` = the file, `check_command = python -c "import <name>"`. A `Test` node (`TEST_NODE_ID`, `discovered_by=GOAL`, `check_command="python -m pytest -q"`) with `requires` edges to every Import.
- [ ] Implement `scan.py::scan_to_nodes(repo_path) -> DepGraph` wrapping `python_deps.import_graph.scan_imports` (reuse its stdlib/project-local classification; keep only `external`).

### Task 3 — Stage 2: import → distribution mapping (`naming.py`, extend `import_mapping`)
- [ ] `tests/depgraph/test_naming.py`: `cv2 -> opencv-python`, `PIL -> Pillow`, `sklearn -> scikit-learn`, `bs4 -> beautifulsoup4`, `fitz -> PyMuPDF`, `yaml -> PyYAML`, plus identity fallback for unknown; declared-manifest names win over the curated table.
- [ ] Extend `python_deps.import_mapping.CURATED_IMPORT_TO_PACKAGE` (currently 6 rows) with the common native/aliased set (at minimum: `fitz`, `Image`, `OpenSSL`, `psycopg2`, `MySQLdb`, `lxml`, `cv2` already present). Keep it a curated table; no env reverse-lookup in this plan.
- [ ] `naming.py::package_roots(graph, declared_names) -> list[(import_id, dist_name)]` (pure; uses existing `map_import_to_package`).

### Task 4 — Executor (`executor.py`)
- [ ] `tests/depgraph/test_executor.py`: `LocalSubprocessExecutor.run("python -c \"print(1)\"")` → `ok`, stdout `1`; non-zero command → `returncode != 0`, stderr captured; `FakeExecutor` (test helper in `tests/depgraph/conftest.py`) returns canned results keyed by substring.
- [ ] Implement `LocalSubprocessExecutor` (subprocess, shell, timeout, captures rc/stdout/stderr) and the `Executor` Protocol + `CommandResult`.
- [ ] Implement `DockerExecutor` (context manager: `docker run -d ... sleep infinity`; `run` = `docker exec`; `__exit__` removes the container). Not unit-tested here (covered by the gated integration test in Task 9).

### Task 5 — Stage 3: real resolver → Package nodes (`resolve.py`)
- [ ] `tests/depgraph/test_resolve.py` (FakeExecutor): given roots `[opencv-python, Pillow]`, the resolver invokes `uv pip compile` (assert the command shape) and parses a canned pinned output (`opencv-python==4.9.0.80`, `numpy==1.26.4`, `Pillow==10.3.0`, ...) into `Package` nodes (`type=PACKAGE, layer=PIP, discovered_by=RESOLVER, version=...`, `check_command="python -m pip show <name>"`, `fix_candidates=("pip:<name>",)`). Each root Import gets a `requires` edge to its Package; transitive deps become `requires` edges Package→Package where derivable from the resolver output.
- [ ] Implement `resolve.py::resolve_closure(roots, executor) -> (list[Node], list[Edge])` shelling `uv pip compile` via the Executor and parsing the pinned `name==version` lines. (Transitive Package→Package edges: parse `uv pip compile --universal`/annotation comments if present; otherwise emit only Import→Package and a flat Package set — annotate the limitation in code.)

### Task 6 — Curated native tables (`tables.py`)
- [ ] `tests/depgraph/test_tables.py`: `apt_for_soname("libGL.so.1") == "libgl1"`; `apt_for_tool("pg_config") == "libpq-dev"`, `apt_for_tool("gcc") == "build-essential"`, `apt_for_tool("Python.h") == "python3-dev"`; `NATIVE_RISK_PACKAGES` contains `opencv-python`, `psycopg2`, `lxml`, `mysqlclient`.
- [ ] Implement `tables.py` with the seed tables from design doc §11 (`NATIVE_LIB_TO_APT`, `TOOL_TO_APT`, `NATIVE_RISK_PACKAGES`). Unknown soname/tool → `None` (no LLM fallback in this plan; node stays `missing` with evidence).

### Task 7 — Stage 4: probing → SystemLib / Tool nodes (`probe.py`)
- [ ] `tests/depgraph/test_probe.py` (FakeExecutor):
  - **build-time gap:** canned `pip install` stderr `pg_config executable not found` → a `Tool` node `tool:pg_config` (`layer=TOOLCHAIN, discovered_by=PROBE, state=MISSING`, `evidence` = the line, `fix_candidates=("apt:libpq-dev",)`), with a `requires` edge from the `psycopg2` Package.
  - **run-time gap:** canned `python -c "import cv2"` stderr `ImportError: libGL.so.1: cannot open shared object file` → a `SystemLib` node `syslib:libGL.so.1` (`layer=SYSTEM`, `fix_candidates=("apt:libgl1",)`), `requires` edge from the `opencv-python` Package.
  - **clean import:** rc 0 → no SystemLib node; the import node's probe is just recorded.
- [ ] Implement `probe.py`:
  - `install_closure(graph, executor)` → one `pip install` of all pinned Packages; parse stderr for tool/header gaps via `TOOL_TO_APT` keys + `python_deps.failure_classifier` patterns.
  - `import_probe(graph, executor)` → for each Import (and each `NATIVE_RISK_PACKAGES` member), `python -c "import X"`; on `ImportError: lib*.so` (reuse `failure_classifier.NATIVE_LIBRARY_RE`) create a `SystemLib` node + `requires` edge from the owning Package, mapping the soname via `apt_for_soname`.
  - Records an `Attempt` on each affected node. (No remediation loop here — discovery only. Certification in Task 8 is what proves the fix once applied; the apply/re-probe loop is the agent loop, out of scope.)

### Task 8 — Stage 5: certification (`certify.py`) + orchestrator (`build.py`) + export
- [ ] `tests/depgraph/test_certify.py` (FakeExecutor): `certify(graph, node_id, executor, cycle=n)` runs the node's `check_command`; rc 0 → `state=SATISFIED, certified_cycle=n`; rc≠0 → `state=MISSING` with stderr as `evidence`; a node with no `check_command` is left `UNKNOWN`. `certify_all` certifies in layer order (interpreter→system→toolchain→pip→naming→tests). Re-certifying a previously satisfied node after a (simulated) mutation flips it back — assert the revocation path exists.
- [ ] Implement `certify.py` (host-only state writes; never infers from install success — only from the check rc).
- [ ] `tests/depgraph/test_build.py` (FakeExecutor end-to-end): `build_dep_graph(fixture_repo, fake)` returns a graph with the expected Import/Package/SystemLib/Tool/Test nodes, `requires` topology, and certified states matching the canned outputs. Assert `discovered_by` stamping per stage.
- [ ] Implement `build.py::build_dep_graph` wiring stages 1→5 (scan → map → resolve → seed → install → probe → certify), stamping `discovered_cycle`.
- [ ] `tests/depgraph/test_export.py`: `to_graphml(graph)` is well-formed XML with the **same keys** as `docs/sample-dependency-graph.graphml` (`label,type,layer,state,discovered_by,check,fix,evidence` + `relation`), so it renders in the existing HTML viewer. Implement `export.py`.

### Task 9 — (stretch / gated) real container e2e + optional Runtime nodes
- [ ] `tests/depgraph/test_integration_docker.py` marked `@pytest.mark.docker` (skip if `docker` absent): `build_dep_graph` against `python:3.11-slim` on a small native repo (imports `cv2`); assert `syslib:libGL.so.1` is discovered `MISSING`, then after the test applies `apt-get install -y libgl1` via the same `DockerExecutor`, re-certification flips it `SATISFIED`. This is the only test that touches Docker/network.
- [ ] (optional) `runtime.py`: after a (real) `pytest` run via the Executor, classify `KeyError: <ENV>` / connection-refused into `Runtime` nodes (`requires` edge from `Test`). Deferred unless Task 1–8 land cleanly.

---

## Definition of done
- `repo_path + Executor → DepGraph` with certified `Test/Import/Package/SystemLib/Tool` nodes and `requires` topology, host-certified `state`, GraphML-exportable and viewable in the existing HTML.
- All unit tests pass with `FakeExecutor` — no Docker/network required for the suite; the one Docker test is gated.
- No changes to `models.py`, `external_graph/`, `resolver.py`, `z3_adapter.py` (the diagnosis/solver path is untouched).
- Out-of-scope items (LLM Planner, Runtime/conflicts/alternatives beyond stretch, Dockerfile finalize, clean-rebuild promotion, agent loop) are not started; certificates produced here are explicitly **provisional (scratch-container scope)** per design doc §4.6.

## Open decisions to confirm before/at Task 5
1. **Resolver:** `uv pip compile` (fast, hash-pinnable) vs `pip install --dry-run --report`. Plan assumes `uv`; confirm `uv` is acceptable as a new binary dep.
2. **Transitive Package→Package edges:** parse from `uv` annotations now, or ship flat Package set first and add edges in a follow-up. Plan ships flat-with-annotation-if-available.
3. **Default Executor for `build_dep_graph` in non-test use:** `DockerExecutor(base_image)` — confirm the base image default (`python:3.11-slim`).
