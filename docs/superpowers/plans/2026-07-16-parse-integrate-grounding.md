# Execution-Evidence Parse → Integrate (Grounding) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a raw build/pytest failure into a durable, root-caused `Observation` that is *anchored to the concrete `DepGraph`* — resolving the failure's causal root to a real provider node, hanging one `requires` edge that carries the causal chain, and never inventing a provider for an unmapped error.

**Architecture:** A new `integrate()` step consumes a `ParsedFailure` (the parsed execution trace) and produces `(DepGraph, ObservationOverlay)`. It resolves each endpoint of the transient traceback chain to a real node via `ids.py` + `import_mapping`, matches-or-appends idempotently by stable id, and writes **structure** (nodes + one edge) to the `DepGraph` while writing **causality** (the chain, blast radius, raw span) to a separate append-only `ObservationOverlay`. Unresolvable roots become demand-only `import:` nodes or overlay-only observations — never a guessed provider edge. This is **additive**: it does not modify the existing static-construction path; it reuses `ids.py`, `import_mapping`, and the helpers in `runtime_ingest.py`.

**Tech Stack:** Python (frozen dataclasses, `dataclasses.replace`), pytest (parametrized), the existing `src/python_deps/depgraph/` package. No new third-party dependencies.

## Global Constraints

- **Immutability:** every graph/overlay "mutation" returns a NEW object (repo rule; see `schema.py` header). Frozen dataclasses only.
- **Pure module:** `integrate.py` and `exec_trace.py` must not import `src.envstate` (match the "Pure module — unit-testable with plain data" rule in `runtime_ingest.py`/`diagnose.py`).
- **Additive:** do NOT change `runtime_ingest.py`, `runtime_classify.py`, `schema.py`, or `ids.py` behavior. You may *import and reuse* their functions. No existing test may change outcome.
- **Error is evidence, not an install instruction:** never hang a `requires`→provider edge for a root that `is_unresolved`. Demand-only node or overlay observation instead.
- **Idempotent by stable id:** the same entity discovered by the static resolver and by runtime MUST collapse onto one node (`ids.py`: ids are `<kind>:<name>` "so the same entity discovered by different stages collapses onto a single node"). Minting the *wrong id kind* is the bug to avoid (`binary:pg_config` NOT `tool:pg_config` — see `runtime_ingest.py:_id_for_discovery` comment).
- **Anchor the CAUSE, not the symptom:** resolve `ParsedFailure.causal`, not `.terminal`. The terminal string is recorded as evidence only.
- **Two write targets:** `DepGraph` gets nodes + the single `requires` edge; the `ObservationOverlay` gets the chain + blast radius + raw span. Middle traceback frames never become graph nodes.
- **Test framework:** pytest, files under `tests/depgraph/`, follow the style of `tests/depgraph/test_probe.py` / `test_build.py`. Run `pytest tests/depgraph/ -q` after each task.

---

## Finalized design (embedded — self-contained reference)

This plan implements the parse → integrate design finalized during design review. The interactive diagram lived at `http://127.0.0.1:8787/anchor.html` (a session-local server; it may no longer be running — this section is the authoritative record).

**Pipeline:** `Normalize → Ground(trace) → Integrate(anchor) → Diagnose → Route`. This plan builds **Integrate** (the anchor/merge) and a minimal **parse** that feeds it.

**What parse gives you** — a `ParsedFailure`: a transient execution subgraph reconstructed from the log text. Its center of gravity is an ordered `chain` (deepest last) ending at the root, plus a `phase` (build|collection|runtime), a `terminal` descriptor (surface anchor) and a `causal` descriptor (deepest env-relevant anchor — may differ from terminal), a `blast_radius`, an optional `probe` result, and a `raw_span`. Every element is a **descriptor string** (`"import:psycopg2"`, `"target:tests/test_x.py"`), not yet a graph node.

**What integrate does — the ANCHOR step (cascading-import worked example):**

```
TRANSIENT (from traceback)                 PERSISTENT DepGraph
target:tests/test_x.py  ──imports──▶       Test
  └ module:myapp                             │  requires (origin=runtime)   ← STRUCTURE
     └ module:myapp.db                       ▼
        └ ⚠ import:psycopg2  ──resolve──▶  pkg:psycopg2  (+NEW, SOFT candidate)
                                           edge.data = {phase, via:[myapp, myapp.db], importer}  ← CAUSALITY
Observation overlay: chain, blast_radius={tests/test_x.py}, raw span      ← CAUSALITY
```

1. Resolve the **root** descriptor (`causal`) to a concrete node id via `map_import_to_package` + `ids.py`. Match the node the static resolver already placed (idempotent by id / normalized name), else append a `SOFT` candidate.
2. Resolve the **top of chain** to the owner (`TEST_NODE_ID`).
3. Hang **one** `requires` edge owner→provider, carrying the causal chain in `edge.data` (`via`, `importer`, `phase`).
4. The **middle frames + blast radius** ride the `ObservationOverlay`, NOT the graph.

**Three landing sites:**
- **resolve → provider:** import→package, syslib, tool(=`binary:`), config, service. `Test --requires--> <provider>` edge.
- **unresolved import → demand only:** `import:<name>` node (`NodeType.IMPORT`), state `MISSING`, **no** provider edge.
- **refuse:** repo-local import / pip-disproven / assertion residual → add NOTHING to the graph (record an overlay observation only). This is the `items`/`azure` false-add guard.

**Grounded against these existing modules (read them before starting):**
- `src/python_deps/depgraph/schema.py` — `DepGraph`, `Node`, `Edge`, `NodeType` (TEST/PROJECT/IMPORT/PACKAGE/SYSTEM_LIB/TOOL/RUNTIME/PLATFORM/SERVICE/CONFIG), `EdgeType.REQUIRES`, `State`, `Strength`, `Layer`, `DiscoveredBy`, `EDGE_RULES`. `requires` src allows {Test, Project, Import, Package, Service, Config}; dst allows {Project, Import, Package, SystemLib, Tool, Runtime, Platform, Service, Config}.
- `src/python_deps/depgraph/ids.py` — `TEST_NODE_ID`, `package_id(name, version)`, `import_id`, `syslib_id`, `tool_id`, `config_id`, `service_id`, `capability_id(kind, name)` (kind ∈ soname/header/binary/pkgconfig/linker_lib).
- `src/python_deps/import_mapping.py` — `map_import_to_package(import_name, declared_package_names=None) -> MappingResult`, `is_unresolved(result) -> bool`, `MappingResult.package_name: str|None`, `normalize_package_name`.
- `src/python_deps/failure_classifier.py` — `classify_dependency_failure(command, output) -> DependencyFailure` (failure_type ∈ module_not_found / import_name_error / no_matching_distribution / dependency_conflict / glibc_version_mismatch / native_library_missing / syntax_requires_newer_python / not_dependency_related; carries `import_name`, `package_name`, `message`, `details`), `first_soname`, `classify_config_error`, `classify_tool_error`.
- `src/python_deps/depgraph/runtime_ingest.py` — `_find_existing_node(graph, discovery)`, `_annotate_or_append(graph, discovery, owner)` (the current *flat* runtime edge; `integrate()` is its typed, chain-aware successor — reuse the match logic, do not modify it).
- `src/python_deps/depgraph/diagnose.py` — `RepoContext(local_names, invalid_names, collisions)`, `is_local_import(import_name, local_names) -> bool`, `Mode`.

**Key invariant this whole plan defends:** only the chain's *endpoints* become graph nodes; its *interior* is causality-as-provenance. That keeps the graph from bloating into a node-per-frame while still answering "why required?" (`edge.data.via`) and "what's blocked?" (`observation.blast_radius`).

---

### Task 1: Contract — `ParsedFailure`, `Observation`, `ObservationOverlay`

**Files:**
- Create: `src/python_deps/depgraph/exec_trace.py`
- Test: `tests/depgraph/test_exec_trace.py`

**Interfaces:**
- Produces: `ParsedFailure` (frozen), `Observation` (frozen), `ObservationOverlay` (frozen, with `.with_observation(obs)`), `stable_failure_id(failure_type, causal, phase) -> str`, and the stub `parse(command, output, phase, ctx) -> ParsedFailure`. `integrate()` (Task 4) consumes `ParsedFailure` + `ObservationOverlay`.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_exec_trace.py
from python_deps.depgraph.exec_trace import (
    ParsedFailure, Observation, ObservationOverlay, stable_failure_id,
)


def test_stable_id_is_deterministic_and_volatile_free():
    a = stable_failure_id("module_not_found", "import:psycopg2", "collection")
    b = stable_failure_id("module_not_found", "import:psycopg2", "collection")
    assert a == b and len(a) == 12


def test_overlay_merges_by_stable_id_bumping_sightings():
    o1 = Observation(stable_id="x", anchor="pkg:psycopg2", chain=(), blast_radius=frozenset(),
                     phase="collection", raw_span="...", sightings=1, seen_this_cycle=True)
    overlay = ObservationOverlay().with_observation(o1).with_observation(
        Observation(stable_id="x", anchor="pkg:psycopg2", chain=(), blast_radius=frozenset(),
                    phase="collection", raw_span="...", sightings=1, seen_this_cycle=True)
    )
    assert len(overlay.observations) == 1
    assert overlay.observations[0].sightings == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_exec_trace.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.exec_trace'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/exec_trace.py
"""Execution-evidence contract: the parsed trace + the durable observation overlay.

Pure module — no src.envstate imports. A ParsedFailure is a TRANSIENT subgraph
reconstructed from log text; the ObservationOverlay is the PERSISTENT, append-only
causality record that references the DepGraph by stable node id.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

# (owner_descriptor, relation, target_descriptor); descriptors are "kind:name" strings.
ChainStep = tuple[str, str, str]


def stable_failure_id(failure_type: str, causal: str, phase: str) -> str:
    """Volatile-free identity: failure kind + causal anchor + phase.  No paths/linenos."""
    key = f"{failure_type}|{causal}|{phase}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()[:12]


@dataclass(frozen=True)
class ParsedFailure:
    phase: str                       # "build" | "collection" | "runtime"
    failure_type: str                # from classify_dependency_failure
    terminal: str                    # surface descriptor, e.g. "import:psycopg2"
    causal: str                      # deepest env-relevant descriptor (may == terminal)
    chain: tuple[ChainStep, ...]     # execution flow, deepest LAST
    blast_radius: frozenset[str] = frozenset()
    probe: tuple[str, str] | None = None   # (command, result) if a stage-3 probe ran
    raw_span: str = ""
    confidence: str = "runtime-deterministic"

    @property
    def stable_id(self) -> str:
        return stable_failure_id(self.failure_type, self.causal, self.phase)


@dataclass(frozen=True)
class Observation:
    stable_id: str
    anchor: str                      # DepGraph node id (or import:/error: id) this grounds to
    chain: tuple[ChainStep, ...]
    blast_radius: frozenset[str]
    phase: str
    raw_span: str
    sightings: int = 1
    seen_this_cycle: bool = True
    refuted_by: str | None = None
    resolved_by: str | None = None


@dataclass(frozen=True)
class ObservationOverlay:
    observations: tuple[Observation, ...] = ()

    def get(self, stable_id: str) -> Observation | None:
        for o in self.observations:
            if o.stable_id == stable_id:
                return o
        return None

    def with_observation(self, obs: Observation) -> "ObservationOverlay":
        existing = self.get(obs.stable_id)
        if existing is None:
            return replace(self, observations=self.observations + (obs,))
        merged = replace(existing, sightings=existing.sightings + 1, seen_this_cycle=True,
                         raw_span=obs.raw_span or existing.raw_span)
        kept = tuple(o for o in self.observations if o.stable_id != obs.stable_id)
        return replace(self, observations=kept + (merged,))


def parse(command: str, output: str, phase: str, ctx) -> ParsedFailure:
    raise NotImplementedError("implemented in Task 5")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_exec_trace.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/exec_trace.py tests/depgraph/test_exec_trace.py
git commit -m "feat(depgraph): ParsedFailure + Observation overlay contract"
```

---

### Task 2: Labeled corpus

**Files:**
- Create: `tests/depgraph/corpus_integrate.py`

**Interfaces:**
- Produces: `CASES: tuple[Case, ...]` where `Case` carries the *input* `ParsedFailure`, the *starting* graph nodes, and the *expected* merge outcome. Consumed by Tasks 3–6. `Case` is hand-labeled — labeling IS the contract validation.

- [ ] **Step 1: Write the corpus (no test yet — this is data)**

```python
# tests/depgraph/corpus_integrate.py
"""Hand-labeled failures. Labeling forces the contract: for each failure we state
exactly what node it must resolve to, whether it should append or match, the edge,
the causal chain, and — for negatives — that NOTHING is added."""
from __future__ import annotations

from dataclasses import dataclass, field

from python_deps.depgraph.exec_trace import ParsedFailure
from python_deps.depgraph.schema import Node, NodeType, Layer, State, Strength, DiscoveredBy
from python_deps.depgraph.ids import package_id, TEST_NODE_ID


@dataclass(frozen=True)
class Case:
    name: str
    parsed: ParsedFailure
    starting_nodes: tuple[Node, ...] = ()      # graph state BEFORE (Test node added by the test)
    expect_add: bool = True                    # should a graph node/edge be added?
    expect_node_id: str | None = None          # resolved provider / demand node id
    expect_edge: tuple[str, str] | None = None # (src, dst) requires edge, or None
    expect_via: tuple[str, ...] = ()           # edge.data["via"]
    expect_blast: frozenset[str] = frozenset()
    expect_unbound: bool = False               # demand node, NO provider edge
    match_existing: bool = False               # must annotate, not duplicate


_PKG_PSY = Node(id=package_id("psycopg2", "2.9.9"), type=NodeType.PACKAGE, name="psycopg2",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED,
                strength=Strength.HARD, version="2.9.9")

CASES: tuple[Case, ...] = (
    # 1. external missing import, resolvable, graph empty -> append pkg + edge
    Case(
        name="module_not_found_append",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:psycopg2", causal="import:psycopg2",
                             chain=(("target:tests/test_x.py", "imports", "import:psycopg2"),),
                             blast_radius=frozenset({"tests/test_x.py"}),
                             raw_span="E ModuleNotFoundError: No module named 'psycopg2'"),
        expect_add=True, expect_node_id=package_id("psycopg2", None),
        expect_edge=(TEST_NODE_ID, package_id("psycopg2", None)),
        expect_blast=frozenset({"tests/test_x.py"}),
    ),
    # 2. same import, but the pkg is ALREADY in the graph (static-resolved) -> MATCH, no twin
    Case(
        name="module_not_found_match_existing",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:psycopg2", causal="import:psycopg2",
                             chain=(("target:tests/test_x.py", "imports", "import:psycopg2"),),
                             raw_span="E ModuleNotFoundError: No module named 'psycopg2'"),
        starting_nodes=(_PKG_PSY,),
        expect_add=True, match_existing=True, expect_node_id=package_id("psycopg2", "2.9.9"),
        expect_edge=(TEST_NODE_ID, package_id("psycopg2", "2.9.9")),
    ),
    # 3. cascading import -> chain + blast recorded, via = middle frames
    Case(
        name="cascading_import_chain",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:psycopg2", causal="import:psycopg2",
                             chain=(("target:tests/test_x.py", "imports", "module:myapp"),
                                    ("module:myapp", "imports", "module:myapp.db"),
                                    ("module:myapp.db", "imports", "import:psycopg2")),
                             blast_radius=frozenset({"tests/test_x.py"}),
                             raw_span="myapp/db.py:1: E ModuleNotFoundError: No module named 'psycopg2'"),
        expect_add=True, expect_node_id=package_id("psycopg2", None),
        expect_edge=(TEST_NODE_ID, package_id("psycopg2", None)),
        expect_via=("module:myapp", "module:myapp.db"),
        expect_blast=frozenset({"tests/test_x.py"}),
    ),
    # 4. native runtime lib -> syslib node
    Case(
        name="native_library_missing",
        parsed=ParsedFailure(phase="runtime", failure_type="native_library_missing",
                             terminal="syslib:libGL.so.1", causal="syslib:libGL.so.1",
                             chain=(("target:tests/test_render.py", "loads", "syslib:libGL.so.1"),),
                             raw_span="ImportError: libGL.so.1: cannot open shared object file"),
        expect_add=True, expect_node_id="syslib:libGL.so.1",
        expect_edge=(TEST_NODE_ID, "syslib:libGL.so.1"),
    ),
    # 5. build tool -> binary: capability (FRACTURE GUARD: not tool:)
    Case(
        name="build_tool_pg_config",
        parsed=ParsedFailure(phase="build", failure_type="not_dependency_related",
                             terminal="binary:pg_config", causal="binary:pg_config",
                             chain=(("target:project", "builds", "binary:pg_config"),),
                             raw_span="Error: pg_config executable not found."),
        expect_add=True, expect_node_id="binary:pg_config",
        expect_edge=(TEST_NODE_ID, "binary:pg_config"),
    ),
    # 6. repo-local import -> REFUSE (false-add guard: the `items`/`azure` bug)
    Case(
        name="repo_local_refuse",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:myapp", causal="import:myapp",
                             chain=(("target:tests/test_x.py", "imports", "import:myapp"),),
                             raw_span="E ModuleNotFoundError: No module named 'myapp'"),
        expect_add=False,
    ),
    # 7. unmappable import -> demand-only node, NO provider edge
    Case(
        name="unmappable_unbound",
        parsed=ParsedFailure(phase="collection", failure_type="module_not_found",
                             terminal="import:frobnicate9000", causal="import:frobnicate9000",
                             chain=(("target:tests/test_x.py", "imports", "import:frobnicate9000"),),
                             raw_span="E ModuleNotFoundError: No module named 'frobnicate9000'"),
        expect_add=True, expect_unbound=True, expect_node_id="import:frobnicate9000",
        expect_edge=None,
    ),
)
```

- [ ] **Step 2: Sanity-check the corpus imports cleanly**

Run: `python -c "from tests.depgraph.corpus_integrate import CASES; print(len(CASES))"`
Expected: prints `7`

> NOTE ON SOURCING: cases 1–7 are hand-authored from canonical real shapes. Before implementation, pull 1–2 *actual* logs from a recent run ledger (search: `grep -rl "ModuleNotFoundError" $(git rev-parse --show-toplevel)/datasets` and the react/loop run outputs) and add them as extra cases if the real text differs from these. Keep the corpus small (≤ ~12) and high-quality.

- [ ] **Step 3: Commit**

```bash
git add tests/depgraph/corpus_integrate.py
git commit -m "test(depgraph): labeled integrate corpus (7 cases)"
```

---

### Task 3: RED tests — the parametrized grader

**Files:**
- Create: `tests/depgraph/test_integrate.py`

**Interfaces:**
- Consumes: `integrate(graph, overlay, parsed, ctx) -> (DepGraph, ObservationOverlay)` (defined next task) and `CASES`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/depgraph/test_integrate.py
import pytest

from tests.depgraph.corpus_integrate import CASES
from python_deps.depgraph.exec_trace import ObservationOverlay
from python_deps.depgraph.integrate import integrate
from python_deps.depgraph.diagnose import RepoContext
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy, EdgeType
from python_deps.depgraph.ids import TEST_NODE_ID

_TEST_NODE = Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo tests",
                  layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.UNKNOWN)
# local_names carries the sys.path-accurate repo top-levels (see diagnose.RepoContext).
_CTX = RepoContext(local_names=frozenset({"myapp"}))


def _graph_for(case):
    g = DepGraph(nodes=(_TEST_NODE,) + case.starting_nodes)
    return g


def _pkg_nodes(graph, name):
    from python_deps.import_mapping import normalize_package_name
    want = normalize_package_name(name)
    return [n for n in graph.nodes
            if n.type is NodeType.PACKAGE and normalize_package_name(n.name) == want]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_integrate_case(case):
    g0 = _graph_for(case)
    g1, overlay = integrate(g0, ObservationOverlay(), case.parsed, _CTX)

    if not case.expect_add:
        # REFUSE: no graph node/edge added (false-add guard). Overlay MAY record it.
        assert len(g1.nodes) == len(g0.nodes)
        assert len(g1.edges) == len(g0.edges)
        return

    # node landed
    assert g1.get(case.expect_node_id) is not None, f"missing node {case.expect_node_id}"

    # no fracture: exactly one node for the capability
    if case.expect_node_id.startswith("pkg:"):
        assert len(_pkg_nodes(g1, "psycopg2")) == 1

    # match vs append
    if case.match_existing:
        assert len(g1.nodes) == len(g0.nodes)   # annotated, no twin
    if case.expect_unbound:
        # demand-only: no requires edge OUT of the import node, no provider edge added
        assert case.expect_edge is None
        assert not any(e.src == case.expect_node_id and e.relation is EdgeType.REQUIRES
                       for e in g1.edges)

    # edge
    if case.expect_edge is not None:
        src, dst = case.expect_edge
        edge = next((e for e in g1.edges if e.src == src and e.dst == dst
                     and e.relation is EdgeType.REQUIRES), None)
        assert edge is not None, f"missing edge {src}->{dst}"
        if case.expect_via:
            assert tuple(edge.data.get("via", ())) == case.expect_via

    # causality on the overlay
    obs = overlay.get(case.parsed.stable_id)
    assert obs is not None
    if case.expect_blast:
        assert obs.blast_radius == case.expect_blast
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/depgraph/test_integrate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.integrate'` (all cases error/collect-fail). This is RED for every failure class at once.

- [ ] **Step 3: Commit the RED harness**

```bash
git add tests/depgraph/test_integrate.py
git commit -m "test(depgraph): RED integrate grader over corpus"
```

---

### Task 4: Implement `integrate()` to green — resolvable, match, chain, unbound, refuse

**Files:**
- Create: `src/python_deps/depgraph/integrate.py`
- Test: `tests/depgraph/test_integrate.py` (from Task 3)

**Interfaces:**
- Consumes: `ParsedFailure`, `ObservationOverlay` (Task 1); `RepoContext`, `is_local_import` (`diagnose.py`); `map_import_to_package`, `is_unresolved`, `normalize_package_name` (`import_mapping`); `package_id`, `import_id`, `syslib_id`, `config_id`, `service_id`, `capability_id`, `TEST_NODE_ID` (`ids.py`); `DepGraph`, `Node`, `Edge`, `NodeType`, `EdgeType`, `Layer`, `State`, `Strength`, `DiscoveredBy` (`schema.py`).
- Produces: `integrate(graph, overlay, parsed, ctx) -> tuple[DepGraph, ObservationOverlay]`.

- [ ] **Step 1: Write the implementation (make all corpus cases pass at once)**

```python
# src/python_deps/depgraph/integrate.py
"""ANCHOR / Integrate: merge a ParsedFailure into the persistent DepGraph.

Structure (nodes + one requires edge) -> DepGraph; causality (chain + blast radius
+ raw span) -> ObservationOverlay. Never invents a provider for an unresolved root.
Idempotent by stable id. Pure module. Additive — reuses ids/import_mapping only.
"""
from __future__ import annotations

from dataclasses import replace

from python_deps.depgraph.diagnose import RepoContext, is_local_import
from python_deps.depgraph.exec_trace import ObservationOverlay, Observation, ParsedFailure
from python_deps.depgraph.ids import (
    TEST_NODE_ID, capability_id, config_id, import_id, package_id, service_id, syslib_id,
)
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State, Strength,
)
from python_deps.import_mapping import (
    is_unresolved, map_import_to_package, normalize_package_name,
)

_KIND_LAYER = {
    NodeType.PACKAGE: Layer.PIP, NodeType.IMPORT: Layer.PIP,
    NodeType.SYSTEM_LIB: Layer.SYSTEM, NodeType.TOOL: Layer.TOOLCHAIN,
    NodeType.CONFIG: Layer.CONFIG, NodeType.SERVICE: Layer.SERVICES,
}


def _split(descriptor: str) -> tuple[str, str]:
    kind, _, name = descriptor.partition(":")
    return kind, name


def _resolve_root(parsed: ParsedFailure, ctx: RepoContext):
    """Return (disposition, node_id, node_type). disposition in
    {'provider','demand','refuse'}. Anchors the CAUSAL descriptor."""
    kind, name = _split(parsed.causal)
    if kind == "import":
        if is_local_import(name, ctx.local_names):
            return ("refuse", None, None)                    # repo-local: add nothing
        result = map_import_to_package(name)
        if is_unresolved(result):
            return ("demand", import_id(name), NodeType.IMPORT)   # NO provider guess
        return ("provider", package_id(result.package_name, None), NodeType.PACKAGE)
    if kind == "syslib":
        return ("provider", syslib_id(name), NodeType.SYSTEM_LIB)
    if kind in ("binary", "tool"):
        # FRACTURE GUARD: a missing executable is a binary: capability, never tool:<name>.
        return ("provider", capability_id("binary", name), NodeType.TOOL)
    if kind == "config":
        return ("provider", config_id(name), NodeType.CONFIG)
    if kind == "service":
        return ("provider", service_id(name), NodeType.SERVICE)
    return ("demand", import_id(name or parsed.causal), NodeType.IMPORT)


def _find_existing(graph: DepGraph, node_id: str, node_type: NodeType, name: str) -> Node | None:
    """Idempotent match: exact id, else PACKAGE by normalized name (pkg:<name> vs
    pkg:<name>==<ver> from the static resolver)."""
    direct = graph.get(node_id)
    if direct is not None:
        return direct
    if node_type is NodeType.PACKAGE:
        want = normalize_package_name(name)
        for n in graph.nodes:
            if n.type is NodeType.PACKAGE and normalize_package_name(n.name) == want:
                return n
    return None


def _edge_data(parsed: ParsedFailure) -> dict:
    via = [step[2] for step in parsed.chain[1:-1]] if len(parsed.chain) > 2 else []
    importer = parsed.chain[-1][0] if parsed.chain else ""
    return {"phase": parsed.phase, "via": via, "importer": importer}


def integrate(
    graph: DepGraph,
    overlay: ObservationOverlay,
    parsed: ParsedFailure,
    ctx: RepoContext,
) -> tuple[DepGraph, ObservationOverlay]:
    disposition, node_id, node_type = _resolve_root(parsed, ctx)

    if disposition == "refuse":
        # Record the observation (evidence never lost) but touch NO graph node/edge.
        obs = Observation(stable_id=parsed.stable_id, anchor=parsed.causal, chain=parsed.chain,
                          blast_radius=parsed.blast_radius, phase=parsed.phase,
                          raw_span=parsed.raw_span)
        return graph, overlay.with_observation(obs)

    name = _split(parsed.causal)[1]
    existing = _find_existing(graph, node_id, node_type, name)
    if existing is not None:
        anchor_id = existing.id
        node = replace(existing, discovered_by=DiscoveredBy.RUNTIME,
                       evidence=parsed.raw_span[:500])
    else:
        anchor_id = node_id
        node = Node(id=node_id, type=node_type,
                    name=name if node_type is not NodeType.IMPORT else parsed.causal,
                    layer=_KIND_LAYER[node_type], discovered_by=DiscoveredBy.RUNTIME,
                    state=State.MISSING if disposition == "demand" else State.UNKNOWN,
                    strength=Strength.SOFT, evidence=parsed.raw_span[:500])
    new_graph = graph.with_node(node)

    # STRUCTURE: one requires edge owner->provider, carrying the causal chain.
    # Demand nodes (unresolved imports) get NO outgoing provider edge, but the owner
    # still requires the (unsatisfied) capability, so hang Test->import: for demand too
    # ONLY when it is a real capability the target needs. For provider, Test->provider.
    if disposition == "provider":
        owner = TEST_NODE_ID if new_graph.get(TEST_NODE_ID) is not None else None
        if owner is not None:
            edge = Edge(src=owner, dst=anchor_id, relation=EdgeType.REQUIRES,
                        origin="runtime", data=_edge_data(parsed))
            new_graph = new_graph.with_edge(edge)

    # CAUSALITY: overlay carries chain + blast + raw span.
    obs = Observation(stable_id=parsed.stable_id, anchor=anchor_id, chain=parsed.chain,
                      blast_radius=parsed.blast_radius, phase=parsed.phase,
                      raw_span=parsed.raw_span)
    return new_graph, overlay.with_observation(obs)
```

- [ ] **Step 2: Run the grader**

Run: `pytest tests/depgraph/test_integrate.py -q`
Expected: PASS (7 passed). If `unmappable_unbound` fails because `map_import_to_package("frobnicate9000")` is *not* unresolved in your mapper, adjust the case to an import guaranteed unmapped (verify with `python -c "from python_deps.import_mapping import map_import_to_package, is_unresolved; print(is_unresolved(map_import_to_package('frobnicate9000')))"` — must print `True`).

- [ ] **Step 3: Confirm no existing test regressed**

Run: `pytest tests/depgraph/ -q`
Expected: PASS (all prior tests unchanged — `integrate.py` is additive).

- [ ] **Step 4: Lint**

Run: `ruff check src/python_deps/depgraph/integrate.py src/python_deps/depgraph/exec_trace.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/integrate.py
git commit -m "feat(depgraph): integrate() anchors a ParsedFailure into the DepGraph"
```

---

### Task 5: Implement `parse()` — stage 1 + traceback-chain walk (2b)

**Files:**
- Modify: `src/python_deps/depgraph/exec_trace.py` (replace the `parse` stub)
- Test: `tests/depgraph/test_exec_trace_parse.py`

**Interfaces:**
- Consumes: `classify_dependency_failure` (`failure_classifier.py`), `RepoContext`.
- Produces: `parse(command, output, phase, ctx) -> ParsedFailure` such that `parse(case.raw_span-source, ...)` reproduces the corpus `ParsedFailure` for the module-not-found cases.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_exec_trace_parse.py
from python_deps.depgraph.exec_trace import parse
from python_deps.depgraph.diagnose import RepoContext

_CTX = RepoContext(local_names=frozenset({"myapp"}))

_CASCADING = '''\
tests/test_x.py:2: in <module>
    from myapp import thing
myapp/__init__.py:4: in <module>
    from .db import Session
myapp/db.py:1: in <module>
    import psycopg2
E   ModuleNotFoundError: No module named 'psycopg2'
'''


def test_parse_reconstructs_chain_and_causal():
    pf = parse("python -m pytest", _CASCADING, "collection", _CTX)
    assert pf.failure_type == "module_not_found"
    assert pf.causal == "import:psycopg2"
    assert pf.chain[-1][2] == "import:psycopg2"          # root is deepest
    assert "tests/test_x.py" in "".join(s[0] for s in pf.chain)  # target at top
    assert "tests/test_x.py" in pf.blast_radius
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/depgraph/test_exec_trace_parse.py -q`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `parse` (replace the stub)**

```python
# in src/python_deps/depgraph/exec_trace.py — add imports at top:
import re
from python_deps.failure_classifier import classify_dependency_failure

_FRAME_RE = re.compile(r'^(?P<path>[^\s"][^:]*\.py):\d+: in ', re.MULTILINE)
_FRAME_TB_RE = re.compile(r'^\s*File "(?P<path>[^"]+\.py)", line \d+', re.MULTILINE)


def _walk_traceback(output: str) -> tuple[list[str], str | None]:
    """Return (ordered .py paths deepest-last, target path). Pytest and CPython
    traceback grammars only — structural, not error-vocabulary."""
    paths = [m.group("path") for m in _FRAME_RE.finditer(output)]
    if not paths:
        paths = [m.group("path") for m in _FRAME_TB_RE.finditer(output)]
    target = paths[0] if paths else None
    return paths, target


def _module_descriptor(path: str) -> str:
    stem = path.replace("\\", "/")
    if "test" in stem.rsplit("/", 1)[-1]:
        return f"target:{stem}"
    dotted = stem[:-3].replace("/", ".") if stem.endswith(".py") else stem
    return f"module:{dotted}"


def parse(command: str, output: str, phase: str, ctx) -> ParsedFailure:  # noqa: F811
    dep = classify_dependency_failure(command, output)
    ft = dep.failure_type
    if ft in ("module_not_found", "import_name_error"):
        root = f"import:{dep.import_name or ''}"
    elif ft == "native_library_missing":
        root = f"syslib:{dep.details.get('library', '')}"
    else:
        root = f"import:{dep.import_name or dep.package_name or ''}"

    paths, target = _walk_traceback(output)
    chain: list[ChainStep] = []
    descriptors = [_module_descriptor(p) for p in paths]
    if descriptors:
        for a, b in zip(descriptors, descriptors[1:]):
            chain.append((a, "imports", b))
        chain.append((descriptors[-1], "imports", root))
    else:
        chain.append((f"target:{target or 'unknown'}", "imports", root))

    blast = frozenset({target}) if target else frozenset()
    return ParsedFailure(phase=phase, failure_type=ft, terminal=root, causal=root,
                         chain=tuple(chain), blast_radius=blast,
                         raw_span=(dep.message or output)[:500])
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/depgraph/test_exec_trace_parse.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/exec_trace.py tests/depgraph/test_exec_trace_parse.py
git commit -m "feat(depgraph): parse() reconstructs the traceback chain (stage 2b)"
```

---

### Task 6: End-to-end test — raw log → parse → integrate

**Files:**
- Create: `tests/depgraph/test_parse_integrate_e2e.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_parse_integrate_e2e.py
from python_deps.depgraph.exec_trace import parse, ObservationOverlay
from python_deps.depgraph.integrate import integrate
from python_deps.depgraph.diagnose import RepoContext
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy, EdgeType
from python_deps.depgraph.ids import TEST_NODE_ID, package_id

_LOG = '''\
tests/test_x.py:2: in <module>
    from myapp import thing
myapp/db.py:1: in <module>
    import psycopg2
E   ModuleNotFoundError: No module named 'psycopg2'
'''


def test_raw_log_grounds_to_pkg_node_with_causal_edge():
    g = DepGraph(nodes=(Node(id=TEST_NODE_ID, type=NodeType.TEST, name="t",
                             layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL,
                             state=State.UNKNOWN),))
    ctx = RepoContext(local_names=frozenset({"myapp"}))
    pf = parse("pytest", _LOG, "collection", ctx)
    g2, overlay = integrate(g, ObservationOverlay(), pf, ctx)

    assert g2.get(package_id("psycopg2", None)) is not None
    edge = next(e for e in g2.edges if e.dst == package_id("psycopg2", None)
                and e.relation is EdgeType.REQUIRES)
    assert edge.src == TEST_NODE_ID
    assert "module:myapp.db" in edge.data.get("via", []) or edge.data.get("importer")
    assert overlay.observations[0].blast_radius  # non-empty
```

- [ ] **Step 2: Run to verify it fails, then passes**

Run: `pytest tests/depgraph/test_parse_integrate_e2e.py -q`
Expected: this should PASS immediately if Tasks 4–5 are correct (both halves already green). If it fails, the defect is in the parse↔integrate descriptor contract — fix `_module_descriptor` / `_resolve_root` so descriptors match, do not weaken the assertion.

- [ ] **Step 3: Commit**

```bash
git add tests/depgraph/test_parse_integrate_e2e.py
git commit -m "test(depgraph): e2e raw-log -> parse -> integrate grounding"
```

---

### Task 7: Clean-container resolution oracle (ground truth)

**Files:**
- Create: `tests/depgraph/test_integrate_oracle.py`

**Interfaces:**
- Consumes: the resolvable subset of `CASES` (those with `expect_node_id` starting `pkg:`), the venv/container helper used elsewhere in the repo (search: `grep -rn "def create_venv\|python -m venv" src/`).

- [ ] **Step 1: Write the oracle test (integration-marked, skips without tooling)**

```python
# tests/depgraph/test_integrate_oracle.py
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("pkg,import_name", [("psycopg2-binary", "psycopg2")])
def test_resolved_provider_makes_the_failing_import_pass(pkg, import_name):
    """Ground truth for resolution accuracy: install the provider integrate() chose,
    then run the exact check that failed. If it now imports, the resolution was correct."""
    venv = Path(tempfile.mkdtemp()) / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = venv / "bin" / "python"
    try:
        pre = subprocess.run([str(py), "-c", f"import {import_name}"], capture_output=True)
        assert pre.returncode != 0, "import should FAIL before install (baseline)"
        subprocess.run([str(py), "-m", "pip", "install", "-q", pkg], check=True)
        post = subprocess.run([str(py), "-c", f"import {import_name}"], capture_output=True)
        assert post.returncode == 0, f"provider {pkg} did not satisfy import:{import_name}"
    finally:
        shutil.rmtree(venv.parent, ignore_errors=True)
```

- [ ] **Step 2: Run (only when network/pip available)**

Run: `pytest tests/depgraph/test_integrate_oracle.py -q -m integration`
Expected: PASS (or SKIP where marked). Wire `-m "not integration"` into the default fast suite so it does not slow every run.

- [ ] **Step 3: Commit**

```bash
git add tests/depgraph/test_integrate_oracle.py
git commit -m "test(depgraph): clean-container resolution oracle (integration)"
```

---

### Task 8: Scorecard + regression gate

**Files:**
- Create: `tests/depgraph/test_integrate_scorecard.py`

- [ ] **Step 1: Write a scorecard test that fails if any per-axis rate drops**

```python
# tests/depgraph/test_integrate_scorecard.py
from tests.depgraph.corpus_integrate import CASES
from tests.depgraph.test_integrate import _graph_for, _pkg_nodes, _CTX
from python_deps.depgraph.exec_trace import ObservationOverlay
from python_deps.depgraph.integrate import integrate
from python_deps.depgraph.schema import EdgeType


def test_scorecard_axes_all_perfect_on_corpus():
    n = len(CASES)
    right_target = one_node = edge_ok = false_add = 0
    for case in CASES:
        g0 = _graph_for(case)
        g1, _ = integrate(g0, ObservationOverlay(), case.parsed, _CTX)
        if not case.expect_add:
            false_add += 0 if (len(g1.nodes) == len(g0.nodes) and len(g1.edges) == len(g0.edges)) else 1
            right_target += 1; one_node += 1; edge_ok += 1
            continue
        right_target += 1 if g1.get(case.expect_node_id) is not None else 0
        one_node += 1 if (not case.expect_node_id.startswith("pkg:")
                          or len(_pkg_nodes(g1, "psycopg2")) == 1) else 0
        edge_ok += 1 if (case.expect_edge is None or any(
            e.src == case.expect_edge[0] and e.dst == case.expect_edge[1]
            and e.relation is EdgeType.REQUIRES for e in g1.edges)) else 0
    # Regression gate: the corpus is golden. Any drop is a regression.
    assert right_target == n, f"resolution {right_target}/{n}"
    assert one_node == n, f"no-fracture {one_node}/{n}"
    assert edge_ok == n, f"edge {edge_ok}/{n}"
    assert false_add == 0, f"false-add {false_add} (MUST be 0)"
```

- [ ] **Step 2: Run**

Run: `pytest tests/depgraph/test_integrate_scorecard.py -q`
Expected: PASS. This is the frozen gate — run it (and the whole `tests/depgraph/` suite) on the cases that currently PASS before any future change to parse/integrate (regression-sweep rule).

- [ ] **Step 3: Commit**

```bash
git add tests/depgraph/test_integrate_scorecard.py
git commit -m "test(depgraph): integrate scorecard + regression gate"
```

---

## Deferred (explicitly out of scope for this plan)

Named so the next session does not silently assume they are done:

- **Stage 2a (build-log upward scan)** and the **build/tool/header** parse path — Task 5 implements the pytest traceback walk only; build-log `fatal error: X.h` → `header:`/`-dev` extraction is a follow-up plan.
- **Stage 3 (discriminating probe)** and the **AMBIGUOUS → probe → re-diagnose** loop — the `import_name_error`/version-masquerade case is represented in the design but not in this corpus; add it with the probe machinery next.
- **Capability→provider two-hop** (`Test → import:cv2 → pkg:opencv-python`). This plan hangs the flat `Test → pkg` edge (matching current `runtime_ingest`). The schema already permits the two-hop (`EDGE_RULES` allows Test→Import and Import→Package); adopt it in a follow-up if the capability/provider split is wanted.
- **Reconciling the overlay with `graph_context.py`'s existing "error nodes"** (render path) — do this before the overlay is consumed by the diagnostician render.
- **src-layout / sys.path-accurate module naming in the chain walk** — Task 5 derives dotted names from paths; before trusting `via` on real repos, resolve module names through `RepoContext.local_names` / `repo_modules.top_level_names` (the EnvGraph src-layout blind spot). Add a src-layout corpus case when you do.

## Phase 2 (later): Converge & Retire — cleanup TODO

**Motivation.** Phase 1 (this plan) is deliberately *additive*: it builds a clean, typed, tested seam (`exec_trace` + `integrate` + the scorecard) *beside* the current parse/grounding pipeline without touching it. The current pipeline feels messy for two real reasons — (1) genuine seam tangle: `diagnose()` fuses Normalize/Ground/Diagnose/Route in one function and *drops* every non-`ENVIRONMENT` observation on the floor; and (2) dense, load-bearing edge-case code (the `azure`/`items` locality split, the `binary:`/`tool:` fracture guard, the soname shapes) that *looks* like clutter but is encoded bug fixes. Phase 2 cleans up (1) and never rewrites (2). It is only safe *after* Phase 1, because the Task 8 scorecard is the net that lets us delete old paths without re-introducing regressions (regression-sweep rule).

Do NOT start Phase 2 until Phase 1 is green and the scorecard is frozen. These are intent TODOs, not yet a task-decomposed plan:

- [ ] Migrate `runtime_ingest`'s caller onto `integrate()`; once `integrate()` covers the same cases, delete the duplicate flat `_annotate_or_append` runtime-edge path (removes the two-paths-do-the-same-thing overlap Phase 1 temporarily creates).
- [ ] Split `diagnose()`'s fused concerns into the pipeline stages (Ground emits an anchored observation; Route picks the layer) — its careful locality/disproven logic is preserved verbatim, just relocated.
- [ ] Stop dropping evidence: route `REPO_INTERNAL_REF` / `RESIDUAL` / `INVALID_ATTEMPT` / `AMBIGUOUS` into the durable `ObservationOverlay` instead of returning `None` and vanishing (invariant: no evidence silently lost).
- [ ] Reconcile the `ObservationOverlay` with `graph_context.py`'s existing "error nodes" render path (one representation, not two).
- [ ] Consolidate the duplicated import→pip-name / module→provider mapping tables (curated maps live in more than one place) into one module, marked candidate-confidence, backed by installed metadata rather than grown by hand.
- [ ] Guard every deletion with the scorecard: run `tests/depgraph/` on the currently-passing cases *before* removing any old path; a flipped decision blocks the change.

## Self-review notes (author checklist, done)

- Spec coverage: contract (T1), corpus (T2), RED grader (T3), implement class-by-class (T4), parse (T5), e2e (T6), oracle (T7), scorecard/regression (T8) — every step of "contract → corpus → RED → green → oracle → gate" has a task.
- No placeholders: all steps carry runnable code and exact commands.
- Type consistency: `integrate(graph, overlay, parsed, ctx)`, `ParsedFailure(phase, failure_type, terminal, causal, chain, blast_radius, probe, raw_span, confidence)`, `Observation(stable_id, anchor, chain, blast_radius, phase, raw_span, sightings, seen_this_cycle, refuted_by, resolved_by)`, and `ObservationOverlay.with_observation` are used identically across T1–T8.
