# DepGraph → Contract Graph Seed Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate the certified dependency graph (`DepGraph`) into seed atomic Contract nodes for the existing Contract Graph V2 at cold-start, so the agent loop begins with the complete, ordered obligation set the depgraph predicts instead of rediscovering it reactively from stderr.

**Architecture:** A single pure adapter — a *proactive, depgraph-sourced sibling of `promote_atomic_contracts`*. It enumerates the depgraph's obligation-bearing nodes (Import / SystemLib / Tool) and emits the same flat atomic `Contract` nodes that `extract.promote_atomic_contracts` emits from failure signatures, but sourced from the depgraph (all of them, not only the ones that already failed), tagged with depgraph provenance. The depgraph is consumed once at init: its facts become contracts; only a `source_refs` provenance string survives. The three-role loop, host certification, and the done-gate are untouched.

**Tech Stack:** Python 3.11, frozen dataclasses, pytest. No new dependencies. No Docker/network in any unit test (DepGraph fixtures are hand-built in-process).

## Global Constraints

- **Mirror `promote_atomic_contracts` exactly in shape.** The adapter returns `list[Node]` (Contract nodes only). It emits **no Blockers, no edges, no state assertions** — the host still certifies. It is idempotent: skip any `cid` already present (`graph.has_node(cid)`) and dedupe within one pass via a `seen` set. This matches `src/envstate/contracts/extract.py:37-52` line-for-line in structure.
- **Contract `data` schema is fixed** (from `goals.py` / `extract.py`): keys are exactly `level, kind, subject, layer, check, source_refs, evidence_refs, description, metadata`. Atomic contracts use `level="atomic"`.
- **Kind/layer mapping is fixed** (must match `extract.py:48`): `Import → ("python_import","deps")`, `SystemLib → ("system_library","system")`, `Tool → ("binary","system")`. No other depgraph node types become contracts (Test/Project/Package/Runtime are skipped — Test already exists in the backbone; Package is a provider that folds into the fix, not an obligation).
- **Seed ALL obligation-bearing nodes regardless of `state`** (proactive completeness — the difference from reactive promotion). A satisfied import still becomes a contract; the host certifies it normally.
- **Provenance** goes in `source_refs` as `[f"depgraph:{dep_node.id}"]` (parallels the existing `["goal"]`, `["signature:..."]` convention). No new schema field.
- **Behind the existing gate.** The feature is active only when `world_map.dep_graph is not None`, which is only populated under the `enable_dep_graph` flag (arm `v1gd`). Off-state must be byte-identical.
- **Do NOT touch the done-gate.** The live gate (`_verified_test_run_passed`, `maintainer.py:192-241`) already requires a real test run; the collect-only hole is already closed. Pass-rate tightening is OUT OF SCOPE (see Deferred Follow-ups).
- **Do NOT add proactive Blockers in v1.** A prediction must not assert a violation; the host owns blocker creation. (See Deferred Follow-ups.)
- **Python style:** PEP 8, type annotations on all signatures, `from __future__ import annotations`, black/isort/ruff formatting. **Do NOT run import-pruning lint autofix between task commits** — later tasks add imports earlier tasks don't use.
- **Avoid runtime import cycles:** annotate `DepGraph` via `typing.TYPE_CHECKING` (string annotation), never a top-level runtime import of `python_deps.depgraph` into `src/envstate/`.

---

## File Structure

- **Create:** `src/envstate/contracts/depgraph_seed.py` — the pure adapter (one public function).
- **Create:** `tests/envstate/contracts/test_depgraph_seed.py` — adapter unit tests (place beside existing contract-graph tests; if they live elsewhere, mirror that location).
- **Modify:** `src/envstate/world_model.py` — add `dep_graph: "DepGraph | None" = None` field to `WorldModelMap` and a matching keyword param to `initial_map`.
- **Modify:** `src/envstate/contracts/projection.py` — call the adapter inside `refresh_host_graph()`, right after the `promote_atomic_contracts` block.
- **Test (modify/create):** `tests/envstate/test_world_model_dep_graph.py` and a wiring test for `refresh_host_graph` (place beside existing world_model/projection tests).

---

## Task 1: Thread `dep_graph` through `WorldModelMap`

**Files:**
- Modify: `src/envstate/world_model.py` (add field near the existing `dep_advisory: str` at line ~91; add param to `initial_map`; confirm `merge_map` preserves it)
- Test: `tests/envstate/test_world_model_dep_graph.py`

**Interfaces:**
- Produces: `WorldModelMap.dep_graph: "DepGraph | None"` (default `None`); `initial_map(..., dep_graph: "DepGraph | None" = None)` stores it.
- Consumes (Task 3): `world_map.dep_graph`.

- [ ] **Step 1: Write the failing test**

```python
# tests/envstate/test_world_model_dep_graph.py
from src.envstate.world_model import initial_map, merge_map
from python_deps.depgraph.schema import DepGraph, Node as DNode, NodeType, Layer, DiscoveredBy


def _tiny_depgraph() -> DepGraph:
    n = DNode(id="import:cv2", type=NodeType.IMPORT, name="cv2",
              layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN)
    return DepGraph(nodes=(n,))


def test_initial_map_stores_dep_graph():
    g = _tiny_depgraph()
    m = initial_map(base_image="python:3.11", dep_graph=g)
    assert m.dep_graph is g


def test_initial_map_defaults_dep_graph_none():
    m = initial_map(base_image="python:3.11")
    assert m.dep_graph is None


def test_merge_map_preserves_dep_graph():
    g = _tiny_depgraph()
    m = initial_map(base_image="python:3.11", dep_graph=g)
    m2 = merge_map(m, done_flag=True)
    assert m2.dep_graph is g
```

> NOTE: `initial_map`'s real required params may differ — read `src/envstate/world_model.py` and pass whatever it already requires (e.g. `base_image`). The three assertions (stored, defaults None, survives `merge_map`) are the contract.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/envstate/test_world_model_dep_graph.py -v`
Expected: FAIL — `TypeError: initial_map() got an unexpected keyword argument 'dep_graph'` (or `AttributeError: ... 'dep_graph'`).

- [ ] **Step 3: Add the field and param**

In `src/envstate/world_model.py`, add the TYPE_CHECKING import near the top (the module already has `from __future__ import annotations`):

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from python_deps.depgraph.schema import DepGraph
```

Add the field to the `WorldModelMap` dataclass, immediately after the existing `dep_advisory: str` field:

```python
    dep_graph: "DepGraph | None" = None
```

Add a keyword parameter to `initial_map` (default `None`) and pass it into the `WorldModelMap(...)` construction, mirroring how `dep_advisory` is threaded:

```python
def initial_map(..., dep_advisory: str = "", dep_graph: "DepGraph | None" = None, ...) -> WorldModelMap:
    return WorldModelMap(..., dep_advisory=dep_advisory, dep_graph=dep_graph, ...)
```

If `merge_map` is implemented with `dataclasses.replace`, it preserves the new field automatically (the `test_merge_map_preserves_dep_graph` test guards this). If `merge_map` reconstructs the dataclass field-by-field, add `dep_graph=...` there too.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/envstate/test_world_model_dep_graph.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/world_model.py tests/envstate/test_world_model_dep_graph.py
git commit -m "feat(envstate): carry dep_graph on WorldModelMap (seed-adapter input)"
```

---

## Task 2: The pure seed adapter

**Files:**
- Create: `src/envstate/contracts/depgraph_seed.py`
- Test: `tests/envstate/contracts/test_depgraph_seed.py`

**Interfaces:**
- Consumes: a built `DepGraph` (from `python_deps.depgraph.schema`); the `ContractGraph.has_node(id)` predicate; `ids.contract_id(kind, subject)`; the `Node` constructor from `src/envstate/contracts/nodes.py`.
- Produces: `seed_contracts_from_depgraph(graph: ContractGraph, dep_graph: DepGraph) -> list[Node]` — a list of `Node(type="Contract", ...)` ready to drop into `GraphPatch(add_contracts=...)`. Same return type as `promote_atomic_contracts`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/envstate/contracts/test_depgraph_seed.py
from src.envstate.contracts.depgraph_seed import seed_contracts_from_depgraph
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node
from src.envstate.contracts import ids
from python_deps.depgraph.schema import (
    DepGraph, Node as DNode, NodeType, Layer, DiscoveredBy, State,
)


def _imp(name, state=State.MISSING):
    return DNode(id=f"import:{name}", type=NodeType.IMPORT, name=name,
                 layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
                 state=state, check_command=f'python -c "import {name}"')


def _syslib(soname):
    return DNode(id=f"syslib:{soname}", type=NodeType.SYSTEM_LIB, name=soname,
                 layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE,
                 state=State.MISSING, check_command=f"ldconfig -p | grep {soname}")


def _tool(name):
    return DNode(id=f"tool:{name}", type=NodeType.TOOL, name=name,
                 layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.PROBE,
                 state=State.MISSING, check_command=f"command -v {name}")


def test_import_becomes_python_import_contract():
    g = DepGraph(nodes=(_imp("cv2"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert len(out) == 1
    n = out[0]
    assert n.id == ids.contract_id("python_import", "cv2")
    assert n.type == "Contract"
    assert n.data["level"] == "atomic"
    assert n.data["kind"] == "python_import"
    assert n.data["subject"] == "cv2"
    assert n.data["layer"] == "deps"


def test_syslib_becomes_system_library_contract():
    g = DepGraph(nodes=(_syslib("libGL.so.1"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert out[0].data["kind"] == "system_library"
    assert out[0].data["layer"] == "system"
    assert out[0].id == ids.contract_id("system_library", "libGL.so.1")


def test_tool_becomes_binary_contract():
    g = DepGraph(nodes=(_tool("pg_config"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert out[0].data["kind"] == "binary"
    assert out[0].data["layer"] == "system"


def test_skips_non_obligation_node_types():
    test_node = DNode(id="test:repo_tests_pass", type=NodeType.TEST, name="repo_tests_pass",
                      layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL)
    proj = DNode(id="project:x", type=NodeType.PROJECT, name="x",
                 layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN)
    pkg = DNode(id="pkg:numpy==2.0", type=NodeType.PACKAGE, name="numpy",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="2.0")
    g = DepGraph(nodes=(test_node, proj, pkg))
    assert seed_contracts_from_depgraph(ContractGraph.empty(), g) == []


def test_seeds_all_states_not_only_missing():
    g = DepGraph(nodes=(_imp("numpy", state=State.SATISFIED),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert len(out) == 1  # satisfied import is still an obligation


def test_idempotent_skips_existing_contract_id():
    existing = Node(ids.contract_id("python_import", "cv2"), "Contract",
                    {"level": "atomic", "kind": "python_import", "subject": "cv2",
                     "layer": "deps", "check": "", "source_refs": ["signature:x"],
                     "evidence_refs": [], "description": "x", "metadata": {}})
    g = DepGraph(nodes=(_imp("cv2"),))
    out = seed_contracts_from_depgraph(ContractGraph(nodes=(existing,)), g)
    assert out == []


def test_dedupes_within_one_pass():
    # Two depgraph nodes that canonicalize to the same contract id.
    g = DepGraph(nodes=(_imp("cv2"), _imp("cv2")))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert len(out) == 1


def test_provenance_records_depgraph_node_id():
    g = DepGraph(nodes=(_imp("cv2"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert out[0].data["source_refs"] == ["depgraph:import:cv2"]


def test_carries_check_command():
    g = DepGraph(nodes=(_imp("cv2"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert out[0].data["check"] == 'python -c "import cv2"'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/envstate/contracts/test_depgraph_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.envstate.contracts.depgraph_seed'`.

- [ ] **Step 3: Write the adapter**

```python
# src/envstate/contracts/depgraph_seed.py
"""Proactive, depgraph-sourced sibling of extract.promote_atomic_contracts.

Translates the certified dependency graph's obligation-bearing nodes
(Import / SystemLib / Tool) into the SAME flat atomic Contract nodes that
``promote_atomic_contracts`` emits from stderr — but sourced from the depgraph
(all of them, not only the ones that already failed), tagged with depgraph
provenance. No Blockers, no edges, no state assertions: the host still
certifies. Idempotent (skips ids already in the graph). Pure: no Docker, no
network — the DepGraph is built elsewhere and passed in.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import ids
from .nodes import Node

if TYPE_CHECKING:
    from .graph import ContractGraph
    from python_deps.depgraph.schema import DepGraph

# depgraph NodeType.value -> (contract kind, contract layer).
# Mirrors extract.py:48 layer choices; other node types are not obligations.
_KIND_BY_TYPE: dict[str, tuple[str, str]] = {
    "Import": ("python_import", "deps"),
    "SystemLib": ("system_library", "system"),
    "Tool": ("binary", "system"),
}


def seed_contracts_from_depgraph(
    graph: "ContractGraph", dep_graph: "DepGraph"
) -> list[Node]:
    """Atomic Contract nodes seeded from the depgraph's obligations.

    Returns a ``list[Node]`` (Contracts only) for ``GraphPatch(add_contracts=...)``;
    skips any contract id already present and dedupes within the pass.
    """
    out: list[Node] = []
    seen: set[str] = set()
    for node in dep_graph.nodes:
        mapping = _KIND_BY_TYPE.get(node.type.value)
        if mapping is None:
            continue
        ckind, layer = mapping
        subject = node.name
        cid = ids.contract_id(ckind, subject)
        if cid in seen or graph.has_node(cid):
            continue
        seen.add(cid)
        out.append(
            Node(
                cid,
                "Contract",
                {
                    "level": "atomic",
                    "kind": ckind,
                    "subject": subject,
                    "layer": layer,
                    "check": node.check_command or "",
                    "source_refs": [f"depgraph:{node.id}"],
                    "evidence_refs": [],
                    "description": f"{ckind} obligation: {subject}.",
                    "metadata": {},
                },
            )
        )
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/envstate/contracts/test_depgraph_seed.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/depgraph_seed.py tests/envstate/contracts/test_depgraph_seed.py
git commit -m "feat(contracts): depgraph -> atomic contract seed adapter (proactive promote)"
```

---

## Task 3: Wire the adapter into `refresh_host_graph`

**Files:**
- Modify: `src/envstate/contracts/projection.py` (inside `refresh_host_graph()`, immediately after the `promote_atomic_contracts` block at ~line 140-143)
- Test: `tests/envstate/contracts/test_projection_depgraph_seed.py`

**Interfaces:**
- Consumes: `world_map.dep_graph` (Task 1), `seed_contracts_from_depgraph` (Task 2).
- Produces: after a host refresh, every depgraph obligation appears as a Contract node in `world_map.contract_graph`.

- [ ] **Step 1: Write the failing test**

```python
# tests/envstate/contracts/test_projection_depgraph_seed.py
from src.envstate.contracts import ids
from src.envstate.contracts.projection import refresh_host_graph
from src.envstate.world_model import initial_map
from python_deps.depgraph.schema import (
    DepGraph, Node as DNode, NodeType, Layer, DiscoveredBy, State,
)


def _depgraph_with_missing_import() -> DepGraph:
    n = DNode(id="import:cv2", type=NodeType.IMPORT, name="cv2",
              layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
              state=State.MISSING, check_command='python -c "import cv2"')
    return DepGraph(nodes=(n,))


def test_refresh_seeds_depgraph_contract():
    m = initial_map(base_image="python:3.11", dep_graph=_depgraph_with_missing_import())
    m2 = refresh_host_graph(m)   # pass any other args refresh_host_graph requires
    assert m2.contract_graph.has_node(ids.contract_id("python_import", "cv2"))


def test_refresh_without_dep_graph_seeds_nothing_extra():
    m = initial_map(base_image="python:3.11")  # dep_graph is None
    m2 = refresh_host_graph(m)
    assert not m2.contract_graph.has_node(ids.contract_id("python_import", "cv2"))
```

> NOTE: read `src/envstate/contracts/projection.py` for `refresh_host_graph`'s real signature (it may take probe results / manifest / events). Construct the minimal valid inputs; the assertion that matters is the seeded contract id's presence (and absence when `dep_graph is None`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/envstate/contracts/test_projection_depgraph_seed.py -v`
Expected: FAIL — the `python_import:cv2` contract is absent (adapter not wired).

- [ ] **Step 3: Wire the call**

At the top of `src/envstate/contracts/projection.py`, add:

```python
from .depgraph_seed import seed_contracts_from_depgraph
```

Inside `refresh_host_graph()`, immediately after the existing `promote_atomic_contracts` block (the `add_nodes += [n for n in promoted if not graph.has_node(n.id)]` line ~143), add:

```python
        if world_map.dep_graph is not None:
            seeded = seed_contracts_from_depgraph(pre_graph, world_map.dep_graph)
            add_nodes += [n for n in seeded if not graph.has_node(n.id)]
```

> Use the same local variable names already in scope at that point (`pre_graph`, `graph`, `add_nodes`, and the world-model parameter — confirm whether it's named `world_map` or `map`). The seeded nodes flow through the existing `apply_patch(graph, GraphPatch(add_contracts=tuple(add_nodes), ...))` call; do not add a second patch.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/envstate/contracts/test_projection_depgraph_seed.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full contract + envstate suites (no regressions)**

Run: `pytest tests/envstate -q`
Expected: PASS, no regressions. The off-state path (`dep_graph is None`) must be unaffected.

- [ ] **Step 6: Commit**

```bash
git add src/envstate/contracts/projection.py tests/envstate/contracts/test_projection_depgraph_seed.py
git commit -m "feat(contracts): seed depgraph obligations into refresh_host_graph"
```

---

## Deferred Follow-ups (NOT in this plan — separate, measured decisions)

1. **Pass-rate tightening of the done-gate.** The live gate accepts N≥1 passed; a 1-pass/50-error run slips through. Tightening to `pass_rate >= 0.8` is independent of the depgraph and gets its own ticket.
2. **Proactive Blockers.** Emitting a Blocker for each depgraph `MISSING` node would surface predicted gaps in the frontier before they fail at runtime — but a prediction asserting a violation breaks the "host certifies" discipline. Evaluate only after measuring the contracts-only version.
3. **Atomic→atomic `depends_on` chaining.** Carrying the depgraph `requires` chain (e.g. `import:cv2 → syslib:libGL`) as `depends_on` edges (bridging folded Package nodes) would add ordering beyond the `layer` field. Current frontier rendering already orders by layer, so this is an enhancement, not a requirement.
4. **A/B measurement.** Run `v1gd` (with this adapter) vs `v1g` on the native-dep subset, scored with `compute_essr`, to decide whether the seeded obligations move ESSR before investing in (1)-(3).

---

## Self-Review Notes

- **Spec coverage:** the adapter (Task 2) + its input field (Task 1) + its wiring (Task 3) cover the full "seed contracts from depgraph" design. Done-gate and blockers are explicitly deferred.
- **Type consistency:** `seed_contracts_from_depgraph` returns `list[Node]` — identical to `promote_atomic_contracts`, and consumed the same way (`add_nodes += [...]`). `dep_graph` is `"DepGraph | None"` everywhere (string annotation, TYPE_CHECKING import).
- **No placeholders:** every step has runnable test code, the exact adapter implementation, and the exact wiring snippet. The two NOTE blocks point implementers at real signatures to confirm (initial_map, refresh_host_graph) without inventing them.
