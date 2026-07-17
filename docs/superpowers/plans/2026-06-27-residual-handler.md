# Residual Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the residual handler from `docs/superpowers/specs/2026-06-27-residual-handler-design.md` to the implemented topological-wave executor — an LLM classifier tier, culprit-owner edge attribution, an honest give-up for non-environment errors, and a divergence stop — all routed through the existing single runtime-ingest OBSERVE writer.

**Architecture:** The runtime-ingest path already maps `(command, output)` ledger events through a `classifiers` sequence into graph deltas (`runtime_classify.py` + `runtime_ingest.py`). This plan (a) adds a temperature-0, schema-constrained **LLM classifier** as a *second* callable in that sequence, called only on a deterministic-regex miss; (b) threads a **culprit owner id** so a discovered dep hangs off the owning package instead of the global Test node; (c) detects **non-env residuals** (REPO_BUG/FLAKY/UNKNOWN → no node) and **divergence** (a residual mapping to an already-SATISFIED node) and routes both to an **honest give-up** that never sets `done_flag`. The pure `python_deps` modules stay LLM-free; the LLM bridge lives in `src/envstate/` (the layer allowed to depend on both `python_deps` and the LLM client) and is injected.

**Tech Stack:** Python 3, pytest, `python_deps.depgraph` (`runtime_classify.py`, `runtime_ingest.py`, `schema.py`, `emit.py`), `src/envstate` (`orchestrator.py`, `build_agent.py`, `llm_response.py`, `jsonutil.py`).

**Spec:** `docs/superpowers/specs/2026-06-27-residual-handler-design.md` — read §3 (G1–G3), §5 (capability predicate), §6 (classifier), §7/§7.1 (attribution + ledger pairing), §8 (termination). Appendix A/B are background.

## Global Constraints

- **NO COMMITS. NO `git add`.** Leave the working tree dirty. Every task's final step is "run the tests; do NOT commit." This overrides the writing-plans skill's commit step (matches the sibling `2026-06-27-topological-wave-executor.md` convention).
- **Default-off byte-identical.** The deterministic-only path must be unchanged. The LLM classifier and the give-up routing fire **only** under `enable_graph_scheduler`; the runtime-ingest off-path guard (`if not enable_runtime_feedback or current_map.dep_graph is None: return`, `orchestrator.py:191-192`) must remain the first statements. Re-run `tests/test_orchestrator_v1.py`, `tests/test_run_v1_dep_emit.py`, and `tests/test_runtime_feedback_wiring.py` after every orchestrator task.
- **`python_deps` stays pure.** `runtime_classify.py` / `runtime_ingest.py` must not import `src.envstate` or any LLM client. The LLM classifier lives in `src/envstate/llm_classifier.py` and is *injected* into `ingest_runtime_failures` via its existing `classifiers` parameter.
- **Host certifies; nothing else flips `state`.** New code proposes nodes (state UNKNOWN/MISSING); only `certify_refresh` flips a node SATISFIED. The LLM classifier emits a `Discovery`; it never writes graph state or `done_flag`.
- **No node on LLM authority alone.** Every env `Discovery` the LLM emits must carry a non-empty `check_command` (except SERVICE, which is advisory with `check_command=None`, mirroring the deterministic classifier). A classification with no real check returns `None` → no node.
- **Honest give-up never fakes success.** A non-env or divergent residual routes to `return current_map, "planner_giveup"` — `done_flag` is never set on that path.
- **Immutability.** `DepGraph`, `Node`, `Edge`, `Discovery`, `WorldModelMap` are frozen — return new copies (`with_node`, `with_edge`, `merge_map`), never mutate.

## Phase Overview

- **Phase 1 (Tasks 1–3)** — pure data-layer changes in `python_deps`: the `requires_of` field, owner-edge threading, the divergence helper. Each is additive and independently unit-testable with plain data; no LLM, no orchestrator.
- **Phase 2 (Task 4)** — the injectable LLM classifier (`src/envstate/llm_classifier.py`), tested with a fake completion function (no network).
- **Phase 3 (Tasks 5–6)** — wire the classifier and the honest give-up into `run_v1`'s `_runtime_ingest_phase`, gated under `enable_graph_scheduler`.
- **Task 7** — full-suite green + e2e validation.

---

## File Structure

- `src/python_deps/depgraph/runtime_classify.py` — add `requires_of: str | None = None` to `Discovery` (Task 1).
- `src/python_deps/depgraph/runtime_ingest.py` — add `owner_node_id` threading to `ingest_runtime_failures` + `_annotate_or_append` (Task 2); add pure `diverged_node_ids(graph, discoveries)` helper (Task 3).
- `src/envstate/llm_classifier.py` — **new**: `make_llm_classifier(complete_fn, *, note_out_of_scope=None)` factory returning a `(command, output) -> Discovery | None` callable (Task 4).
- `src/envstate/orchestrator.py` — inject the LLM classifier into `_runtime_ingest_phase` (Task 5); route divergence + out-of-scope to honest give-up (Task 6).
- Tests live beside their targets: `tests/depgraph/` for pure modules, `tests/` for anything importing `src.envstate`.

---

## Task 1: `Discovery.requires_of` — the owner field

**Files:**
- Modify: `src/python_deps/depgraph/runtime_classify.py:29-37` (the `Discovery` dataclass)
- Test: `tests/depgraph/test_runtime_parsers.py` (add one test)

**Interfaces:**
- Produces: `Discovery` gains `requires_of: str | None = None` — the id of the node this discovery is a dependency *of* (the §7 owner). Defaulting to `None` keeps every existing construction site valid and every deterministic Discovery unchanged.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_runtime_parsers.py
from python_deps.depgraph.runtime_classify import Discovery  # noqa: E402 (if not already imported)
from python_deps.depgraph.schema import NodeType, Layer       # noqa: E402 (if not already imported)


def test_discovery_requires_of_defaults_none_and_accepts_owner():
    d = Discovery(
        node_type=NodeType.SYSTEM_LIB, name="libpq.so.5", layer=Layer.SYSTEM,
        evidence="x", check_command="ldconfig -p | grep -q libpq.so.5",
    )
    assert d.requires_of is None                      # default
    d2 = Discovery(
        node_type=NodeType.SYSTEM_LIB, name="libpq.so.5", layer=Layer.SYSTEM,
        evidence="x", check_command="c", requires_of="pkg:psycopg2",
    )
    assert d2.requires_of == "pkg:psycopg2"           # carries the owner
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_runtime_parsers.py::test_discovery_requires_of_defaults_none_and_accepts_owner -q`
Expected: FAIL — `Discovery.__init__() got an unexpected keyword argument 'requires_of'`.

- [ ] **Step 3: Implement the field**

In `src/python_deps/depgraph/runtime_classify.py`, the `Discovery` dataclass (lines 29-37) currently ends with `data: dict = field(default_factory=dict)`. Add the field after it:

```python
@dataclass(frozen=True)
class Discovery:
    node_type: NodeType           # PACKAGE | SYSTEM_LIB | TOOL | CONFIG | SERVICE
    name: str                     # dist / soname / tool / VAR / service-kind
    layer: Layer
    evidence: str                 # failure excerpt that revealed the requirement
    check_command: str | None     # None only for SERVICE (advisory)
    confidence: str = "runtime-deterministic"
    data: dict = field(default_factory=dict)
    requires_of: str | None = None   # owner node id this is a dependency OF (spec §7)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_runtime_parsers.py -q`
Expected: PASS (all green — the field is additive).

- [ ] **Step 5: Confirm no regression in ingest**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_runtime_ingest.py -q`
Expected: PASS — `_node_for_discovery` ignores `requires_of`; nothing reads it yet.

- [ ] **Step 6: Verify; do NOT commit.**

---

## Task 2: Owner-edge threading in `ingest_runtime_failures`

**Files:**
- Modify: `src/python_deps/depgraph/runtime_ingest.py:89-120` (`_annotate_or_append`) and `:127-159` (`ingest_runtime_failures`)
- Test: `tests/depgraph/test_runtime_ingest_owner_edge.py` (create)

**Interfaces:**
- Consumes: `Discovery.requires_of` (Task 1); `DepGraph.get`, `with_edge` (`schema.py`).
- Produces:
  - `_annotate_or_append(graph, d, owner_node_id: str | None = None) -> DepGraph` — the REQUIRES edge now hangs from the **owner** when one is known and present in the graph, else falls back to `TEST_NODE_ID`. Owner precedence: `owner_node_id` param > `d.requires_of` > `TEST_NODE_ID`.
  - `ingest_runtime_failures(graph, observations, classifiers=(classify_observation,), owner_node_id: str | None = None) -> tuple[DepGraph, list[Discovery]]` — threads `owner_node_id` to every annotate. Return arity is unchanged (still a 2-tuple) — existing callers stay valid.

This is the spec §7 fix. The static probe (`probe.py:281-284`) already emits `Edge(src=owning_pkg, dst=syslib, REQUIRES)`; this brings the runtime path's *data layer* to parity.

> **Scope of attribution — read this.** Two owner sources feed the new `src`: (1) the LLM classifier's `Discovery.requires_of` (Task 4), which **is** wired into the live path (Task 5) and is the working attribution for single-package errors; and (2) the `owner_node_id` *parameter*, which is the data-layer hook for the per-node repair path (`depgraph_live.repair_failed_nodes`, which holds `node.id`). **The live orchestrator does NOT yet pass `owner_node_id`** — the runtime tap reads the whole ledger with no per-event owner, so repair-path threading needs ledger-event ownership tagging that is **out of scope for this plan** (a documented follow-on). Do not claim the spec's "rides the per-node repair path" is fully delivered; this plan delivers the `requires_of` attribution + the parameter hook. The `owner_node_id` unit tests exercise the hook directly; they must not be read as proof the live repair path is wired.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_runtime_ingest_owner_edge.py
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy, EdgeType,
)
from python_deps.depgraph.runtime_classify import Discovery  # noqa: E402
from python_deps.depgraph.runtime_ingest import _annotate_or_append  # noqa: E402
from python_deps.depgraph.ids import TEST_NODE_ID, syslib_id  # noqa: E402


def _test_node():
    return Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL)


def _psycopg2():
    return Node(id="pkg:psycopg2", type=NodeType.PACKAGE, name="psycopg2",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.SATISFIED, check_command="python3 -c 'import psycopg2'",
                version="2.9")


def _syslib_discovery(owner=None):
    return Discovery(node_type=NodeType.SYSTEM_LIB, name="libpq.so.5", layer=Layer.SYSTEM,
                     evidence="libpq.so.5: cannot open shared object",
                     check_command="ldconfig -p | grep -q libpq.so.5", requires_of=owner)


def _edge(graph, dst):
    return next((e for e in graph.edges
                 if e.dst == dst and e.relation is EdgeType.REQUIRES), None)


def test_requires_of_owner_present_hangs_edge_on_culprit():
    g = DepGraph().with_node(_test_node()).with_node(_psycopg2())
    out = _annotate_or_append(g, _syslib_discovery(owner="pkg:psycopg2"))
    e = _edge(out, syslib_id("libpq.so.5"))
    assert e is not None and e.src == "pkg:psycopg2"   # culprit, not Test


def test_owner_param_overrides_and_hangs_on_culprit():
    g = DepGraph().with_node(_test_node()).with_node(_psycopg2())
    out = _annotate_or_append(g, _syslib_discovery(owner=None), owner_node_id="pkg:psycopg2")
    e = _edge(out, syslib_id("libpq.so.5"))
    assert e is not None and e.src == "pkg:psycopg2"


def test_owner_absent_from_graph_falls_back_to_test():
    g = DepGraph().with_node(_test_node())            # no psycopg2 node
    out = _annotate_or_append(g, _syslib_discovery(owner="pkg:psycopg2"))
    e = _edge(out, syslib_id("libpq.so.5"))
    assert e is not None and e.src == TEST_NODE_ID     # safe fallback


def test_no_owner_defaults_to_test():
    g = DepGraph().with_node(_test_node())
    out = _annotate_or_append(g, _syslib_discovery(owner=None))
    e = _edge(out, syslib_id("libpq.so.5"))
    assert e is not None and e.src == TEST_NODE_ID     # existing behavior preserved


def test_non_package_owner_falls_back_to_test_not_dropped():
    # The LLM may name a SYSTEM_LIB owner (e.g. "libssl needed by libpq"). A
    # SystemLib is NOT a legal requires-src, so an unguarded with_edge would raise
    # and ingest would silently drop the discovery. The guard must fall back to Test
    # AND still create the node + edge (nothing dropped).
    libpq = Node(id="syslib:libpq.so.5", type=NodeType.SYSTEM_LIB, name="libpq.so.5",
                 layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RUNTIME,
                 state=State.MISSING, check_command="ldconfig -p | grep -q libpq.so.5")
    g = DepGraph().with_node(_test_node()).with_node(libpq)
    d = Discovery(node_type=NodeType.SYSTEM_LIB, name="libssl.so.3", layer=Layer.SYSTEM,
                  evidence="libssl.so.3: cannot open shared object",
                  check_command="ldconfig -p | grep -q libssl.so.3",
                  requires_of="syslib:libpq.so.5")            # illegal requires-src
    out = _annotate_or_append(g, d)
    assert out.get(syslib_id("libssl.so.3")) is not None      # node NOT dropped
    e = _edge(out, syslib_id("libssl.so.3"))
    assert e is not None and e.src == TEST_NODE_ID            # safe fallback, no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_runtime_ingest_owner_edge.py -q`
Expected: FAIL — the `owner_node_id`-param test raises `TypeError: ... unexpected keyword argument 'owner_node_id'`; the `requires_of` culprit test fails with `AssertionError` (pre-impl the edge always hangs off `TEST_NODE_ID`, not the culprit). (The two `*_falls_back_to_test` / `*_defaults_to_test` cases legitimately pass both before and after — they assert the fallback — so the file's overall result is still FAIL.)

- [ ] **Step 3: Implement the edge-source change**

In `src/python_deps/depgraph/runtime_ingest.py`, change `_annotate_or_append` (the edge block at lines 113-118). Replace:

```python
    new_graph = graph.with_node(new_node)

    # Hang edge Test --requires--> node with origin="runtime".
    # with_edge is idempotent (deduped by (src, dst, relation) key).
    test_node = new_graph.get(TEST_NODE_ID)
    if test_node is not None:
        edge = Edge(src=TEST_NODE_ID, dst=target_id, relation=EdgeType.REQUIRES, origin="runtime")
        new_graph = new_graph.with_edge(edge)

    return new_graph
```

with (also update the signature on line 89):

```python
    new_graph = graph.with_node(new_node)

    # Hang the REQUIRES edge from the CULPRIT owner when one is known, present, AND a
    # legal requires-src type; else fall back to the global Test node (spec §7).
    # Owner precedence: explicit owner_node_id > d.requires_of > TEST_NODE_ID.
    # CRITICAL: EDGE_RULES["requires"] only allows src in {Test, Project, Import,
    # Package} (schema.py). If the LLM sets requires_of to e.g. a syslib id, an
    # unguarded with_edge would RAISE, and ingest's per-observation try/except
    # (runtime_ingest.py:156-157) would SILENTLY DROP the whole discovery. Validate
    # the src type first and fall back to Test if it is not a legal requires-src.
    _VALID_REQUIRES_SRC = {"Test", "Project", "Import", "Package"}
    owner = owner_node_id or d.requires_of
    owner_node = new_graph.get(owner) if owner is not None else None
    if owner_node is not None and owner_node.type.value in _VALID_REQUIRES_SRC:
        src_id = owner
    else:
        src_id = TEST_NODE_ID
    if new_graph.get(src_id) is not None:
        edge = Edge(src=src_id, dst=target_id, relation=EdgeType.REQUIRES, origin="runtime")
        new_graph = new_graph.with_edge(edge)

    return new_graph
```

And change the function signature (line 89) from `def _annotate_or_append(graph: DepGraph, d: Discovery) -> DepGraph:` to:

```python
def _annotate_or_append(graph: DepGraph, d: Discovery, owner_node_id: str | None = None) -> DepGraph:
```

Then thread it through `ingest_runtime_failures`. Change the signature (line 127-131) to add `owner_node_id`, and the call on line 154 from `new = _annotate_or_append(new, d)` to `new = _annotate_or_append(new, d, owner_node_id)`:

```python
def ingest_runtime_failures(
    graph: DepGraph,
    observations: list[tuple[str, str]],
    classifiers: Sequence[Callable] = (classify_observation,),
    owner_node_id: str | None = None,
) -> tuple[DepGraph, list[Discovery]]:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_runtime_ingest_owner_edge.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Confirm no regression (default owner = Test, byte-identical)**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_runtime_ingest.py tests/test_runtime_feedback_wiring.py -q`
Expected: PASS — with no `owner_node_id` and no `requires_of`, every edge still hangs off `TEST_NODE_ID`.

- [ ] **Step 6: Verify; do NOT commit.**

---

## Task 3: `diverged_node_ids` — the divergence detector

**Files:**
- Modify: `src/python_deps/depgraph/runtime_ingest.py` (add a pure public helper)
- Test: `tests/depgraph/test_runtime_divergence.py` (create)

**Interfaces:**
- Consumes: `_find_existing_node(graph, d)` (already in the module), `State` (`schema.py:58-63`).
- Produces: `diverged_node_ids(graph: DepGraph, discoveries: Iterable[Discovery]) -> tuple[str, ...]` — the ids of discoveries that map to a node already in state `SATISFIED`. These are the spec §8 divergence signals: NECESSARY says present, SUFFICIENT is still red → more nodes will not help. Pure; the orchestrator (Task 6) consumes it to route an honest give-up.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_runtime_divergence.py
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from python_deps.depgraph.runtime_classify import Discovery  # noqa: E402
from python_deps.depgraph.runtime_ingest import diverged_node_ids  # noqa: E402


def _pkg(state):
    return Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=state, check_command="python3 -c 'import requests'")


def _disc():
    return Discovery(node_type=NodeType.PACKAGE, name="requests", layer=Layer.PIP,
                     evidence="ModuleNotFoundError: No module named 'requests'",
                     check_command="python3 -c 'import requests'")


def test_residual_mapping_to_satisfied_node_is_diverged():
    g = DepGraph().with_node(_pkg(State.SATISFIED))
    assert diverged_node_ids(g, [_disc()]) == ("pkg:requests",)


def test_residual_mapping_to_missing_node_is_not_diverged():
    g = DepGraph().with_node(_pkg(State.MISSING))
    assert diverged_node_ids(g, [_disc()]) == ()


def test_residual_with_no_matching_node_is_not_diverged():
    assert diverged_node_ids(DepGraph(), [_disc()]) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_runtime_divergence.py -q`
Expected: FAIL — `cannot import name 'diverged_node_ids'`.

- [ ] **Step 3: Implement the helper**

In `src/python_deps/depgraph/runtime_ingest.py`, add (after `_find_existing_node`, near the other module-level helpers; `State` is already importable from `schema` — extend the existing `from python_deps.depgraph.schema import (...)` to include `State` if absent):

```python
def diverged_node_ids(graph: DepGraph, discoveries) -> tuple[str, ...]:
    """Ids of discoveries that map to an already-SATISFIED node (spec §8).

    Such a residual means NECESSARY (the graph says present) and SUFFICIENT
    (tests still red referencing it) have diverged — adding more nodes will not
    close it. The orchestrator routes these to an honest give-up, not another
    loop iteration. Pure; reads state only.
    """
    out: list[str] = []
    for d in discoveries:
        existing = _find_existing_node(graph, d)
        if existing is not None and existing.state is State.SATISFIED:
            out.append(existing.id)
    return tuple(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_runtime_divergence.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Verify; do NOT commit.**

---

## Task 4: `make_llm_classifier` — the injectable LLM tier

**Files:**
- Create: `src/envstate/llm_classifier.py`
- Test: `tests/test_llm_classifier.py` (create)

**Interfaces:**
- Consumes: `Discovery` (`runtime_classify.py`), `NodeType`/`Layer` (`schema.py`), `extract_json_object` (`src/envstate/jsonutil.py`).
- Produces: `make_llm_classifier(complete_fn: Callable[[list[dict]], str], *, note_out_of_scope: Callable[[str, str], None] | None = None) -> Callable[[str, str], Discovery | None]`. The returned callable has the **exact same shape as `classify_observation`** — `(command, output) -> Discovery | None` — so it drops straight into the `classifiers` sequence. It builds a temp-0 schema prompt, calls the injected `complete_fn`, parses JSON, and maps a recognized env `kind` (with a real `check_command`) to a `Discovery`; it returns `None` for `REPO_BUG`/`FLAKY`/`UNKNOWN`, for an env kind missing its check, for malformed output, or on any exception — and reports the out-of-scope diagnosis via `note_out_of_scope` so the orchestrator can surface it (spec §3 G3, §6).

`complete_fn` is injected (not the raw client) so this module is pure-of-network and unit-testable with a canned-JSON stub; the orchestrator (Task 5) builds the real `complete_fn` from `complete_with_retry`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_classifier.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import NodeType, Layer  # noqa: E402
from src.envstate.llm_classifier import make_llm_classifier  # noqa: E402


def _fixed(json_text):
    return lambda messages: json_text


def test_package_kind_with_check_becomes_discovery():
    j = '{"kind":"PACKAGE","name":"pytest-asyncio","check_command":"python3 -c \'import pytest_asyncio\'","requires_of":"","confidence":0.9,"rationale":"missing test dep"}'
    clf = make_llm_classifier(_fixed(j))
    d = clf("pytest -q", "ModuleNotFoundError: No module named 'pytest_asyncio'")
    assert d is not None
    assert d.node_type is NodeType.PACKAGE and d.layer is Layer.PIP
    assert d.name == "pytest-asyncio"
    assert d.check_command == "python3 -c 'import pytest_asyncio'"
    assert d.confidence == "runtime-llm"


def test_system_lib_carries_owner_via_requires_of():
    j = '{"kind":"SYSTEM_LIB","name":"libpq-dev","check_command":"dpkg -s libpq-dev","requires_of":"pkg:psycopg2","confidence":0.8,"rationale":"pg_config"}'
    d = make_llm_classifier(_fixed(j))("pip install psycopg2", "pg_config not found")
    assert d is not None and d.node_type is NodeType.SYSTEM_LIB
    assert d.requires_of == "pkg:psycopg2"


def test_repo_bug_returns_none_and_notes_out_of_scope():
    notes = []
    j = '{"kind":"REPO_BUG","name":"","check_command":"","confidence":0.95,"rationale":"assertion failure in app logic"}'
    d = make_llm_classifier(_fixed(j), note_out_of_scope=lambda c, r: notes.append((c, r)))("pytest", "AssertionError: 1 != 2")
    assert d is None
    assert notes and "assertion" in notes[0][1].lower()


def test_env_kind_without_check_returns_none():
    j = '{"kind":"PACKAGE","name":"mystery","check_command":"","confidence":0.5,"rationale":"unsure"}'
    assert make_llm_classifier(_fixed(j))("x", "weird error") is None


def test_malformed_output_returns_none_no_raise():
    assert make_llm_classifier(_fixed("not json at all"))("x", "y") is None
    def _boom(messages):
        raise RuntimeError("llm down")
    assert make_llm_classifier(_boom)("x", "y") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_llm_classifier.py -q`
Expected: FAIL — `No module named 'src.envstate.llm_classifier'`.

- [ ] **Step 3: Implement the module**

Create `src/envstate/llm_classifier.py`:

```python
"""LLM error-classifier tier for the residual handler (spec §6).

Injectable, network-free factory. `make_llm_classifier(complete_fn)` returns a
callable with the SAME shape as `runtime_classify.classify_observation`
((command, output) -> Discovery | None), so it drops into the `classifiers`
sequence of `ingest_runtime_failures`. The pure python_deps modules stay
LLM-free; this src.envstate module is the allowed bridge.

Invariants:
  * temperature 0 (the orchestrator's complete_fn sets it);
  * every env Discovery carries a real check_command (SERVICE is advisory,
    check=None) — host certifies, so a hallucinated node is inert;
  * REPO_BUG/FLAKY/UNKNOWN -> None (honest give-up; no graph pollution).
"""
from __future__ import annotations

from collections.abc import Callable

from python_deps.depgraph.runtime_classify import Discovery
from python_deps.depgraph.schema import Layer, NodeType
from src.envstate.jsonutil import extract_json_object

# kind -> (node type, install layer). Layer is derived from kind, not trusted
# from the model, for robustness.
_KIND_MAP: dict[str, tuple[NodeType, Layer]] = {
    "PACKAGE": (NodeType.PACKAGE, Layer.PIP),
    "SYSTEM_LIB": (NodeType.SYSTEM_LIB, Layer.SYSTEM),
    "TOOL": (NodeType.TOOL, Layer.TOOLCHAIN),
    "CONFIG": (NodeType.CONFIG, Layer.CONFIG),
    "SERVICE": (NodeType.SERVICE, Layer.SERVICES),
}

_SYSTEM_PROMPT = (
    "You classify a single failed-command error into ONE environment obligation, "
    "or decide it is not one. Respond with ONLY a JSON object with keys: "
    "kind (PACKAGE|SYSTEM_LIB|TOOL|CONFIG|SERVICE|REPO_BUG|FLAKY|UNKNOWN), name, "
    "layer (pip|apt|none), install_hint, check_command, requires_of, confidence, rationale.\n"
    "Every environment obligation MUST include a check_command that proves its presence "
    "(an import, `command -v`, `ldconfig -p`, `dpkg -s`). If you cannot give a real check, "
    "you do not know — classify UNKNOWN.\n"
    "If the error is NOT an environment/dependency gap (assertion failure, logic bug, "
    "network timeout), classify REPO_BUG or FLAKY. Do NOT invent a package to explain it.\n"
    "Set requires_of to the node id of the package this is a dependency OF when the error "
    "is scoped to one package (e.g. pkg:psycopg2), else leave it empty."
)


def _build_messages(command: str, output: str) -> list[dict]:
    tail = "\n".join((output or "").splitlines()[-40:])    # error is at the tail
    user = (
        f"COMMAND:\n{command}\n\nERROR (tail):\n{tail}\n\n"
        "Classify per the schema. Respond with ONLY the JSON object."
    )
    return [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user}]


def make_llm_classifier(
    complete_fn: Callable[[list[dict]], str],
    *,
    note_out_of_scope: Callable[[str, str], None] | None = None,
) -> Callable[[str, str], Discovery | None]:
    """Build a (command, output) -> Discovery | None classifier."""

    def _classify(command: str, output: str) -> Discovery | None:
        try:
            text = complete_fn(_build_messages(command, output))
            obj = extract_json_object(text)
        except Exception:                       # never break the run (spec §11)
            return None
        if not isinstance(obj, dict):
            return None

        kind = str(obj.get("kind", "")).strip().upper()
        rationale = str(obj.get("rationale", "")).strip()
        if kind not in _KIND_MAP:               # REPO_BUG / FLAKY / UNKNOWN / junk
            if note_out_of_scope is not None:
                note_out_of_scope(command, rationale or f"non-env: {kind or 'unparseable'}")
            return None

        node_type, layer = _KIND_MAP[kind]
        name = str(obj.get("name", "")).strip()
        check = (obj.get("check_command") or "").strip() or None
        owner = (obj.get("requires_of") or "").strip() or None

        if not name:
            return None
        # Every env obligation needs a real check; SERVICE is advisory (check=None).
        if node_type is not NodeType.SERVICE and not check:
            if note_out_of_scope is not None:
                note_out_of_scope(command, f"{kind} '{name}' had no check_command")
            return None

        return Discovery(
            node_type=node_type,
            name=name,
            layer=layer,
            evidence=(output or "")[-500:],
            check_command=check,
            confidence="runtime-llm",
            requires_of=owner,
        )

    return _classify
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_llm_classifier.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Verify; do NOT commit.**

---

## Task 5: Inject the LLM classifier into `_runtime_ingest_phase` (gated)

**Files:**
- Modify: `src/envstate/orchestrator.py:189-214` (`_runtime_ingest_phase`)
- Test: `tests/test_residual_handler_wiring.py` (create) — source-inspection guards (the `run_v1` harness is heavy; mirror `tests/test_graph_scheduler_flag.py`).

**Interfaces:**
- Consumes: `make_llm_classifier` (Task 4), `complete_with_retry` (`src/envstate/llm_response.py:159`), `extract_json_object` (`jsonutil.py`), `classify_observation`, `ingest_runtime_failures` (with `classifiers=`). `build_agent.client` / `build_agent.model` are in scope (run_v1 params).
- Produces: when `enable_graph_scheduler` is true **and** `build_agent.client` is present, `_runtime_ingest_phase` passes `classifiers=(classify_observation, llm_classifier)`; otherwise it passes the default `(classify_observation,)`. The LLM classifier's `complete_fn` wraps `complete_with_retry(build_agent.client, build_agent.model, messages, temperature=0, max_attempts=2, accept=<parseable-json>)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_residual_handler_wiring.py
from pathlib import Path
_SRC = Path(__file__).resolve().parents[1] / "src" / "envstate" / "orchestrator.py"


def test_llm_classifier_injected_only_under_graph_scheduler():
    src = _SRC.read_text()
    body = src[src.index("def _runtime_ingest_phase"):]
    # the classifier tier is referenced, and gated by the scheduler flag
    assert "make_llm_classifier" in body
    gate = body.index("enable_graph_scheduler")
    inject = body.index("make_llm_classifier")
    assert gate < inject                       # flag check precedes the LLM wiring
    # the deterministic classifier is always present in the default tuple
    assert "classify_observation" in body
    # temperature 0 on the wrapped completion
    assert "temperature=0" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_residual_handler_wiring.py::test_llm_classifier_injected_only_under_graph_scheduler -q`
Expected: FAIL — `make_llm_classifier` not present in `_runtime_ingest_phase`.

- [ ] **Step 3: Implement the injection**

In `src/envstate/orchestrator.py`, edit `_runtime_ingest_phase`. **Indentation matters:** the body sits inside `run_v1` → `_runtime_ingest_phase` → `try:`, so every statement is indented **12 spaces** — match that exactly or the Edit will not apply (the `try:` line itself, orchestrator.py:193, is left untouched). The current lines **194-201** (the `try`-body up to the ingest call) are:

```python
            from python_deps.depgraph.advise import render_depgraph_planner
            from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
            events = ledger.events()
            new_events = events[_rt_mark:]
            obs = [(e.cmd, e.stdout) for e in new_events]
            if not obs:
                return
            new_graph, found = ingest_runtime_failures(current_map.dep_graph, obs)
```

Replace with (still 12-space indented):

```python
            from python_deps.depgraph.advise import render_depgraph_planner
            from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
            from python_deps.depgraph.runtime_classify import classify_observation
            events = ledger.events()
            new_events = events[_rt_mark:]
            obs = [(e.cmd, e.stdout) for e in new_events]
            if not obs:
                return
            pre_graph = current_map.dep_graph
            _out_of_scope: list[tuple[str, str]] = []   # non-env diagnoses; Task 6 reads this

            # Deterministic regex tier always runs; the temp-0 LLM tier is appended
            # ONLY under the graph-scheduler arm and only when a client exists
            # (spec §6 cascade). Off this gate the call is byte-identical to before.
            classifiers = (classify_observation,)
            if enable_graph_scheduler and getattr(build_agent, "client", None) is not None:
                from src.envstate.llm_classifier import make_llm_classifier
                from src.envstate.llm_response import complete_with_retry
                from src.envstate.jsonutil import extract_json_object

                def _complete(messages):
                    text, _usage, _resp = complete_with_retry(
                        build_agent.client, build_agent.model, messages,
                        accept=lambda t: extract_json_object(t) is not None,
                        temperature=0, max_attempts=2,
                    )
                    return text

                _llm = make_llm_classifier(
                    _complete,
                    note_out_of_scope=lambda c, r: _out_of_scope.append((c, r)),
                )
                # Bound LLM fan-out: spec §6 is "LLM only on the misses", but a cycle
                # with 30 failed events would fire 30 synchronous temp-0 calls. Classify
                # each UNIQUE error tail at most once, capped per cycle.
                _seen_errs: set[str] = set()
                _MAX_LLM_PER_CYCLE = 5

                def _bounded_llm(cmd, out):
                    key = (out or "")[-500:]
                    if key in _seen_errs or len(_seen_errs) >= _MAX_LLM_PER_CYCLE:
                        return None
                    _seen_errs.add(key)
                    return _llm(cmd, out)

                classifiers = (classify_observation, _bounded_llm)

            new_graph, found = ingest_runtime_failures(pre_graph, obs, classifiers=classifiers)
```

Notes: `pre_graph` is introduced so Task 6 can compute divergence against the pre-ingest graph; `_out_of_scope` is hoisted unconditionally so Task 6 reads it without a `locals()` workaround (off the LLM gate it stays empty); the `_bounded_llm` wrapper caps per-cycle LLM calls and dedups identical errors. `found` and the `merge_map` fold below are unchanged in this task.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_residual_handler_wiring.py -q`
Expected: PASS.

- [ ] **Step 5: Confirm off-path byte-identical + no regression**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_orchestrator_v1.py tests/test_run_v1_dep_emit.py tests/test_runtime_feedback_wiring.py -q`
Expected: PASS — with `enable_graph_scheduler=False` the classifier tuple stays `(classify_observation,)`; the deterministic path is unchanged.

- [ ] **Step 6: Verify; do NOT commit.**

---

## Task 6: Honest give-up for non-env + divergent residuals (gated)

**Files:**
- Modify: `src/envstate/orchestrator.py` — `_runtime_ingest_phase` (record the give-up reason) and the main loop (return `planner_giveup`); add a `_residual_giveup` local near the other `run_v1` scheduler locals (`_rt_mark`, `_repair_turns`).
- Test: `tests/test_residual_handler_wiring.py` (add source-inspection tests)

**Interfaces:**
- Consumes: `diverged_node_ids` (Task 3); `make_llm_classifier`'s `note_out_of_scope` hook (Task 4); `partition(graph).emittable` (`emit.py`) to test "frontier clean".
- Produces: a `run_v1` local `_residual_giveup: str | None = None`. When `enable_graph_scheduler` and a residual is **diverged** (maps to an already-SATISFIED node) **or** the LLM classified it non-env, **and** the deterministic frontier is clean (`not partition(new_graph).emittable`), `_residual_giveup` is set to a reason string. After `_runtime_ingest_phase()` runs in the main loop, if `_residual_giveup` is set the loop returns `current_map, "planner_giveup"` — `done_flag` is never set (spec §3 G3, §8).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_residual_handler_wiring.py
def test_residual_giveup_is_gated_and_never_sets_done_flag():
    src = _SRC.read_text()
    assert "_residual_giveup" in src
    body = src[src.index("def _runtime_ingest_phase"):]
    assert "diverged_node_ids" in body          # divergence detector consumed
    assert "note_out_of_scope" in body          # non-env diagnoses captured
    assert "emittable" in body                  # frontier-clean guard present
    # the divergence give-up is gated INSIDE the ingest body (not just the run_v1 signature)
    assert body.index("enable_graph_scheduler") < body.index("_residual_giveup")
    # assert the SPECIFIC new loop line, not the bare 'planner_giveup' literal
    # (which already appears 3x in the file and would pass trivially).
    assert "if enable_graph_scheduler and _residual_giveup is not None:" in src
    # the residual give-up block must NOT write done_flag
    blk = body[body.index("_residual_giveup"):]
    assert "done_flag" not in blk[:600]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_residual_handler_wiring.py::test_residual_giveup_is_gated_and_never_sets_done_flag -q`
Expected: FAIL — `_residual_giveup` not present.

- [ ] **Step 3: Add the `_residual_giveup` local**

In `src/envstate/orchestrator.py`, near the runtime high-water mark declaration (`_rt_mark: int = 0`, line 96), add:

```python
    _residual_giveup: str | None = None   # set when a residual is non-env / divergent (spec §3 G3, §8)
```

- [ ] **Step 4: Add the divergence + out-of-scope give-up in `_runtime_ingest_phase`**

Update the `nonlocal` line of `_runtime_ingest_phase` (line 190) to include `_residual_giveup`:

```python
            nonlocal current_map, _rt_mark, _residual_giveup
```

The `_out_of_scope` list and the `note_out_of_scope` wiring were already added in Task 5. Immediately after the `new_graph, found = ingest_runtime_failures(pre_graph, obs, classifiers=classifiers)` line — and after the existing `_rt_mark = len(events)` advance — add the give-up decision (still inside the `try`, **12-space indented**):

```python
            # Honest give-up (spec §3 G3 / §8): a residual that maps to an already-
            # SATISFIED node (divergence) or that the LLM judged non-env, when the
            # deterministic frontier is clean, is not fixable by adding nodes. Record
            # the reason; the main loop returns planner_giveup. done_flag is NEVER set.
            if enable_graph_scheduler:
                import logging
                from python_deps.depgraph.runtime_ingest import diverged_node_ids
                from python_deps.depgraph.emit import partition
                diverged = diverged_node_ids(pre_graph, found)
                if (diverged or _out_of_scope) and not partition(new_graph).emittable:
                    _residual_giveup = (
                        f"graph-scheduler: residual not an environment obligation "
                        f"(diverged={list(diverged)}, out_of_scope={len(_out_of_scope)})"
                    )
                    logging.getLogger(__name__).info(
                        "residual-handler give-up: %s", _residual_giveup
                    )
```

(The existing `if not found: return` and `merge_map(...)` lines remain below this block. `_out_of_scope` is the list hoisted in Task 5 — empty when the LLM tier did not run, so the condition reduces to divergence-only off the LLM path. The `logging.info` surfaces the diagnosis spec §3 G3 requires — the loop's return status is the fixed `"planner_giveup"` literal, so the *reason* is carried in the log line, not the status.)

- [ ] **Step 5: Return `planner_giveup` from the main loop**

Find where `_runtime_ingest_phase()` is invoked in the main cycle loop (around line 229) and add the give-up check immediately after the call:

```python
        _runtime_ingest_phase()
        if enable_graph_scheduler and _residual_giveup is not None:
            return current_map, "planner_giveup"
```

- [ ] **Step 6: Run the wiring test + off-path guards**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_residual_handler_wiring.py tests/test_orchestrator_v1.py tests/test_run_v1_dep_emit.py tests/test_runtime_feedback_wiring.py tests/test_progress_done_consistency.py -q`
Expected: PASS — the give-up path is gated under `enable_graph_scheduler`; off the flag, `_residual_giveup` stays `None` and the loop behaves exactly as before. Confirm no test asserts `done_flag=True` on a give-up.

- [ ] **Step 7: Verify; do NOT commit.**

---

## Task 7: Full suite + e2e validation

**Files:** None (validation only).

- [ ] **Step 1: Run the full unit suite**

Run:
```bash
cd /Users/john/john-planner-v3 && python3 -m pytest tests/ -q -p no:cacheprovider \
  --ignore=tests/test_benchmark_arm_v1.py \
  --ignore=tests/test_repo2run_benchmark.py \
  --ignore=tests/test_repo2run_concurrency.py \
  --ignore=tests/test_repo2run_dataset.py
```
Expected: green except the known pre-existing `eval`-import collection errors (the four ignored files only import on the VM).

- [ ] **Step 2: e2e — memU-server under the graph-scheduler arm**

```bash
cd /Users/john/john-planner-v3
cp workplace/agent_run_summary.json rat_run_v1gs/agent_run_summary_pre_residual.json 2>/dev/null
python3 agent.py https://github.com/NevaMind-AI/memU-server \
  --model deepseek/deepseek-v4-flash --steps 30 --enable-graph-scheduler \
  > rat_run_v1gs/agent_memU_residual.log 2>&1
```

- [ ] **Step 3: Confirm behavior and honesty**

Inspect `workplace/agent_run_summary.json`: expect `configuration_success=True` and `in_build_pass_rate >= 0.8` held (parity with the pre-residual baseline on the clean path — the LLM tier only fires on a regex-miss residual). Grep `rat_run_v1gs/agent_memU_residual.log` for evidence that: (a) the deterministic tier still handled the common shapes (0 LLM tokens on the clean path), and (b) if any unrecognized residual occurred, it was classified (a `runtime-llm` Discovery) or routed to an honest `planner_giveup` — never a `done_flag` with red tests. Compare token/command counts against `rat_run_v1gs/agent_run_summary_pre_residual.json`.

- [ ] **Step 4: Do NOT commit. Report the comparison.**

---

## Self-Review notes (author)

- **Spec coverage:** Task 1 = `Discovery.requires_of` (§7 owner carrier); Task 2 = owner-edge attribution (§7 — `culprit REQUIRES dep` with `TEST_NODE_ID` fallback, **incl. the edge-type guard so a non-Package owner falls back instead of raising and dropping the discovery**); Task 3 = divergence detector (§8); Task 4 = the temp-0, schema-constrained, check-mandatory LLM tier with the non-env escape hatch (§6, §3 G3); Task 5 = cascade injection + bounded LLM fan-out (§6 — regex first, LLM on the miss, deduped/capped); Task 6 = honest give-up routing for divergent + non-env residuals, with the diagnosis logged (§3 G3, §8). The capability-predicate routing (§5) needs no new code — reciped PACKAGE/SYSTEM_LIB/TOOL discoveries already flow to the wave via `emit._is_reciped`; CONFIG/SERVICE stay advisory; the LLM tier produces the same `Discovery` shape.
- **§7 partial — read the Task 2 scope box.** Live owner attribution comes **only** from the LLM classifier's `requires_of` (good for single-package errors). The spec's "rides the per-node repair path" (`repair_failed_nodes` → `owner_node_id`) is **NOT wired** here — the runtime tap reads the whole ledger with no per-event owner, and per-event ledger ownership tagging is a documented follow-on. `owner_node_id` ships as the data-layer hook + unit-tested in isolation; do not read those tests as proof the live repair path is attributed.
- **Verification (3 sonnet agents, 2026-06-27):** fixed 2 blockers — the Task 5 indentation mismatch (body is 12-space, inside `run_v1`→`_runtime_ingest_phase`→`try`) and the edge-rule violation (non-Package `requires_of` would raise inside `with_edge` and the per-observation `try/except` would silently drop the discovery → now guarded). Folded 3 should-fixes: bounded LLM fan-out (dedup + cap), give-up reason logged (not dropped), hoisted `_out_of_scope` (removed the `locals()` wart).
- **Deferred NITs (spec §6, low value):** (a) feed the already-SATISFIED node list into the LLM prompt to pre-empt re-proposal — the divergence stop already backstops this at the cost of one wasted call; (b) aggregate the regex-vs-LLM `confidence` tags into a per-cycle paper number — the distinction exists on each `Discovery` (`runtime-deterministic` vs `runtime-llm`) but is not yet counted; (c) collapse the one-cycle delay when a residual is diverged *and* a new emittable node lands the same cycle (a run-level `_known_diverged` set) — not a permanent miss since the error recurs.
- **Deferred (not in this plan, per spec §12 / Appendix B):** subgraph ingestion (Upgrade A/B — scoped transitive-resolve + scoped system-probe); the freeze already masks the reproducibility cost, so this is a turn-budget/completeness play, not a correctness one. Also deferred: learned-recipe cache, classifier distillation.
- **The version-less caveat (Appendix B.3):** this plan does **not** assign a version to LLM/runtime-discovered PACKAGE nodes, so they remain version-less → not emittable → LLM-mediated. That is the existing behavior; fixing it is Upgrade A, explicitly out of scope here.
- **Risk note:** Tasks 5–6 touch `run_v1` (the most-tested loop). Every change is gated under `enable_graph_scheduler`; the off path returns at `orchestrator.py:191-192` and the classifier tuple stays `(classify_observation,)`. Each task re-runs `test_orchestrator_v1.py` + the off-path guards to keep the deterministic arm byte-identical. Tasks 1–4 are additive and cannot affect the off path (new field with a default, new optional param, new helper, new module).
