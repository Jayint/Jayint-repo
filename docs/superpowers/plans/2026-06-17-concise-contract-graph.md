# Concise Contract Graph (v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `v1g` 11-node/12-edge contract graph with a concise 3-node/3-edge planner overlay (Contract/Blocker/Attempt; violates/addresses/depends_on) where status is projected from host evidence and the Planner emits multi-step RecipePatches.

**Architecture:** Host-owned deterministic projection + LLM Maintainer semantic patches + LLM Planner RecipePatches, all behind the existing off-by-default `enable_contract_graph` flag. The graph never stores mutable truth: Contract status, Attempt outcome, and Blocker.active are recomputed each cycle from `WorldModelMap` evidence. Spec: `docs/superpowers/specs/2026-06-17-concise-contract-graph-design.md`.

**Tech Stack:** Python 3 (frozen dataclasses, enums), pytest. No new third-party deps. Tests: `.venv/bin/python -m pytest tests/ -q`. The repo has no `pytest.ini`/markers; `tests/conftest.py` only adds the repo root to `sys.path`.

**Migration shape:** Clean break, one pass (the arm is off by default, so the on-disk graph format may break). Internal task order below is dependency-ordered; the suite is green at the end, not necessarily at every intermediate commit. Every commit message uses conventional-commit format.

---

## Shared Interfaces (keystone — every task conforms to these exact names)

These are the canonical types. Tasks below reference them; do not diverge from these names/signatures.

### `src/envstate/contracts/schema.py` — enums & tables
```python
class NodeType(enum.Enum):
    CONTRACT = "Contract"
    BLOCKER = "Blocker"
    ATTEMPT = "Attempt"

class EdgeType(enum.Enum):
    VIOLATES = "violates"        # Blocker  -> Contract
    ADDRESSES = "addresses"      # Attempt  -> Contract
    DEPENDS_ON = "depends_on"    # Contract -> Contract

class ContractStatus(enum.Enum):           # projected, never stored
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"

class ContractLevel(enum.Enum):
    GOAL = "goal"
    ATOMIC = "atomic"

class BlockerKind(enum.Enum):
    MODULE_NOT_FOUND = "module_not_found"
    MISSING_BINARY = "missing_binary"
    MISSING_SYSTEM_LIBRARY = "missing_system_library"
    VERSION_CONFLICT = "version_conflict"
    BUILD_FAILURE = "build_failure"
    SERVICE_UNREACHABLE = "service_unreachable"
    ENV_VAR_MISSING = "env_var_missing"
    TEST_COLLECTION_FAILURE = "test_collection_failure"
    UNKNOWN = "unknown"

class AttemptKind(enum.Enum):
    PYTHON_INSTALL = "python_install"; SYSTEM_INSTALL = "system_install"
    ENV_CONFIG = "env_config"; SERVICE_START = "service_start"
    BUILD_FIX = "build_fix"; VALIDATION = "validation"
    TEST_RETRY = "test_retry"; INSPECT = "inspect"; OTHER = "other"

class AttemptOutcome(enum.Enum):
    PENDING = "pending"; OK = "ok"; FAILED = "failed"
    OK_BUT_STILL_BLOCKED = "ok_but_still_blocked"

LAYERS: frozenset[str] = frozenset({"deps", "system", "runtime", "build", "tests", "config"})

EDGE_RULES = {  # edge value -> (allowed source types, allowed target types)
    "violates":   (frozenset({"Blocker"}),  frozenset({"Contract"})),
    "addresses":  (frozenset({"Attempt"}),  frozenset({"Contract"})),
    "depends_on": (frozenset({"Contract"}), frozenset({"Contract"})),
}

# Field-level ownership (replaces the old binary node partition):
HOST_CREATABLE_NODE_TYPES = frozenset({"Contract", "Attempt"})       # + Contract status/Attempt outcome/Blocker.active are host-only fields
MAINTAINER_CREATABLE_NODE_TYPES = frozenset({"Contract", "Blocker"}) # maintainer promotes contracts + creates blockers
MAINTAINER_FORBIDDEN_FIELDS = frozenset({"status", "outcome", "active"})  # maintainer may never write these
VALID_NODE_TYPES = frozenset(nt.value for nt in NodeType)
VALID_EDGE_TYPES = frozenset(et.value for et in EdgeType)
# redact_secrets(text) is kept verbatim from the current schema.py.
```

### Node `data` payloads (carried in the generic `Node.data` dict)
```
Contract.data: level, kind, subject, layer, check, source_refs:list, evidence_refs:list, description, metadata:dict
               (NO status field — status is projected)
Blocker.data:  signature, kind, layer, root_or_downstream, summary, evidence_refs:list, active:bool, metadata:dict
Attempt.data:  intent, kind, proposed_by, commands:list, outcome, outcome_reason, evidence_refs:list,
               created_from_target_node_ids:list, metadata:dict
```

### `src/envstate/contracts/ids.py`
```python
def slug(text: str) -> str                       # unchanged (lowercase, non-alnum -> '-')
def contract_id(kind: str, subject: str) -> str  # f"contract:{kind}:{slug(subject)}"
def goal_contract_id(name: str) -> str           # f"contract:goal:{name}"
def foundational_contract_id(name: str) -> str   # f"contract:{name}"   (2-segment)
def blocker_id(signature: str) -> str            # f"blocker:{slug(signature)}"
def attempt_id(key: str) -> str                  # f"attempt:{slug(key)}"
```

### `src/envstate/contracts/graph.py`
```python
@dataclasses.dataclass(frozen=True)
class ContractGraph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    diagnostic_notes: tuple[str, ...] = ()   # capped at 10, advisory only
    # queries: node/has_node/active_nodes/nodes_by_type/out_edges/in_edges (kept)
    # new: contracts()/blockers()/attempts()/goal_contracts()/required_goal_contracts()
    # to_dict()/from_dict() round-trip (no status_events key)

def project_status(graph: ContractGraph, contract_id: str, host_satisfied: frozenset[str]) -> str
    # "satisfied" if contract_id in host_satisfied
    # else "violated" if any ACTIVE Blocker has a violates-edge to contract_id
    # else "unknown"

def depends_on_closure(graph: ContractGraph, goal_id: str) -> tuple[str, ...]   # DFS over depends_on
def root_blockers(graph: ContractGraph) -> tuple[Node, ...]                      # active blockers, root-first
def frontier_by_layer(graph, host_satisfied) -> dict[str, tuple[str, ...]]       # unsatisfied contracts grouped by layer
def goal_ready(graph: ContractGraph, host_satisfied: frozenset[str]) -> bool     # all required goals + deps satisfied
```

### `src/envstate/contracts/patch.py` — Maintainer (semantic) patch
```python
@dataclasses.dataclass(frozen=True)
class GraphPatch:
    add_contracts: tuple[Node, ...] = ()
    add_blockers: tuple[Node, ...] = ()
    add_edges: tuple[Edge, ...] = ()                 # violates / depends_on only
    update_blocker_classification: tuple[dict, ...] = ()   # {blocker_id, root_or_downstream?, kind?, summary?}
    update_contract_description: tuple[dict, ...] = ()     # {contract_id, description}
    diagnostic_notes: tuple[str, ...] = ()
    def is_empty(self) -> bool

def parse_graph_patch(d: Any) -> GraphPatch          # tolerant of missing keys / wrong types
```

### `src/envstate/world_model.py` — new types
```python
@dataclasses.dataclass(frozen=True)
class DependencyState:
    declared: tuple[Fact, ...] = ()
    resolved: tuple[Fact, ...] = ()          # from `python -m pip inspect`
    package_manager: str = "pip"
    test_framework: str = "pytest"

@dataclasses.dataclass(frozen=True)
class RecipeStep:
    id: str
    kind: str                 # AttemptKind value
    command: str
    target_node_ids: tuple[str, ...] = ()

@dataclasses.dataclass(frozen=True)
class RecipePatch:
    steps: tuple[RecipeStep, ...] = ()

# PlannerDecision gains: action may be "apply_recipe_patch"; new field recipe_patch: RecipePatch | None = None
# WorldModelMap gains:   dependency_state: DependencyState | None = None
#                        import_results: tuple[tuple[str, bool], ...] = ()   # (import_name, ok) from the sweep
# open_problems stays as a field but is populated as a DERIVED VIEW (see Task 13 derive_open_problems()).
```

### Host-derived signals (computed by `projection.py`, consumed everywhere)
```python
# subject extraction + deterministic promotion (Task 6):
def extract_blocker_subject(signature: str) -> tuple[str | None, str]   # (subject, blocker_kind_value)
def promote_atomic_contracts(graph, signatures: list[str]) -> list[Node]  # deterministic, no LLM

# import sweep ingestion + satisfaction set (Task 7):
def host_satisfied_set(world_map, ledger) -> frozenset[str]   # contract ids the host certifies this cycle
def derive_attempt_outcome(graph, attempt_id, host_satisfied, step_failed: bool) -> str  # AttemptOutcome value
```

---

## File Structure

| File | Action | Responsibility after change |
|---|---|---|
| `contracts/schema.py` | rewrite | 3 node / 3 edge enums, EDGE_RULES, field-level ownership, BlockerKind/AttemptKind/AttemptOutcome, redact_secrets |
| `contracts/ids.py` | rewrite | new id grammar (contract/blocker/attempt) |
| `contracts/nodes.py` | edit | keep Node/Edge + (de)serialize; drop ContractStatusEvent |
| `contracts/graph.py` | rewrite | container (no status stream) + project_status + traversal |
| `contracts/goals.py` | rewrite | coarse backbone seed + foundational atomics |
| `contracts/projection.py` | rewrite | blocker extractor, deterministic promotion, import-sweep ingestion, status projection, outcome derivation, blocker auto-resolve, refresh_host_graph |
| `contracts/validators.py` | rewrite | one-shot import sweep command + drive probes off promoted atomics |
| `contracts/patch.py` | rewrite | semantic GraphPatch keys + parser |
| `contracts/validation.py` | rewrite | 3-edge rules, field-level ownership, reject rules |
| `contracts/apply.py` | edit | apply semantic patch (field-level updates) |
| `contracts/attempts.py` | new (replaces transitions.py) | addresses edges + outcome derivation |
| `contracts/render.py` | rewrite | three-section planner render + maintainer serializer |
| `world_model.py` | edit | DependencyState, RecipeStep/RecipePatch, import_results, derive_open_problems, auto-resolve on Blockers |
| `extractor.py` / `snapshot.py` | edit | `import_sweep` + `dep_tree` probe fields |
| `planner.py` | edit | RecipePatch parser + three-section prompt + action vocab |
| `maintainer.py` | edit | semantic patch keys + prompt; stop writing the map's semantic fields |
| `build_agent.py` | edit | seed `run` with the whole recipe; budget scales with steps |
| `orchestrator.py` | edit | drive RecipePatch per cycle; per-step Attempt outcome; refresh order |

---

## PHASE A — Type foundations (schema, ids, nodes, graph)

### Task 1: Rewrite `schema.py` to the 3-node/3-edge vocabulary

**Files:**
- Modify: `src/envstate/contracts/schema.py`
- Test: `tests/test_contracts_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_schema.py
from src.envstate.contracts import schema

def test_three_node_three_edge_vocabulary():
    assert {nt.value for nt in schema.NodeType} == {"Contract", "Blocker", "Attempt"}
    assert {et.value for et in schema.EdgeType} == {"violates", "addresses", "depends_on"}
    assert {s.value for s in schema.ContractStatus} == {"satisfied", "violated", "unknown"}

def test_edge_rules_are_three_rows_typed():
    assert set(schema.EDGE_RULES) == {"violates", "addresses", "depends_on"}
    assert schema.EDGE_RULES["violates"] == (frozenset({"Blocker"}), frozenset({"Contract"}))
    assert schema.EDGE_RULES["addresses"] == (frozenset({"Attempt"}), frozenset({"Contract"}))
    assert schema.EDGE_RULES["depends_on"] == (frozenset({"Contract"}), frozenset({"Contract"}))

def test_ownership_constants_and_forbidden_fields():
    assert schema.MAINTAINER_CREATABLE_NODE_TYPES == frozenset({"Contract", "Blocker"})
    assert schema.MAINTAINER_FORBIDDEN_FIELDS == frozenset({"status", "outcome", "active"})

def test_blocker_attempt_enums_present():
    assert "missing_system_library" in {k.value for k in schema.BlockerKind}
    assert "ok_but_still_blocked" in {o.value for o in schema.AttemptOutcome}

def test_redact_secrets_kept():
    assert schema.redact_secrets("API_KEY=sk-abcdefgh12345678") != "API_KEY=sk-abcdefgh12345678"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_contracts_schema.py -q`
Expected: FAIL (current `NodeType` has 11 members / no `BlockerKind`).

- [ ] **Step 3: Rewrite `schema.py`** — replace the enums/tables with the keystone block under "Shared Interfaces → schema.py" above. Keep `redact_secrets` and `_SECRET_PATTERNS` byte-for-byte from the current file (schema.py:100-115). Delete `ValidationState`, `HOST_OWNED_NODE_TYPES`, `MAINTAINER_NODE_TYPES`, `VALID_STATUSES`'s old 5-value set. Add `VALID_STATUSES = frozenset(s.value for s in ContractStatus)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_contracts_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/schema.py tests/test_contracts_schema.py
git commit -m "feat(contracts): 3-node/3-edge schema with field-level ownership"
```

> Note: the suite will be RED elsewhere until Phase F — that is expected for this clean-break rewrite. Run the targeted test file per task, not the whole suite, until Task 20.

---

### Task 2: Rewrite `ids.py` grammar

**Files:**
- Modify: `src/envstate/contracts/ids.py`
- Test: `tests/test_contracts_ids.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_ids.py
from src.envstate.contracts import ids

def test_id_grammar():
    assert ids.contract_id("python_import", "cv2") == "contract:python_import:cv2"
    assert ids.contract_id("system_library", "libGL.so.1") == "contract:system_library:libgl-so-1"
    assert ids.goal_contract_id("repo_tests_pass") == "contract:goal:repo_tests_pass"
    assert ids.foundational_contract_id("python_version_compatible") == "contract:python_version_compatible"
    assert ids.blocker_id("ImportError: libGL.so.1") == "blocker:importerror-libgl-so-1"
    assert ids.attempt_id("install libgl1") == "attempt:install-libgl1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_contracts_ids.py -q`
Expected: FAIL (no `blocker_id`/`attempt_id`/`foundational_contract_id`).

- [ ] **Step 3: Rewrite `ids.py`** to exactly the keystone "ids.py" signatures. Keep `slug`. `contract_id` slugs the subject. Delete `artifact_id`, `requirement_id`, `capability_id`, `command_id`, `revision_id`, `transition_id`, `validator_id`, `verification_target_id`, `open_problem_id`, `failure_id`.

- [ ] **Step 4: Run test** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/ids.py tests/test_contracts_ids.py
git commit -m "feat(contracts): contract/blocker/attempt id grammar"
```

---

### Task 3: Trim `nodes.py` (drop `ContractStatusEvent`)

**Files:**
- Modify: `src/envstate/contracts/nodes.py`
- Test: `tests/test_contracts_nodes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_nodes.py
from src.envstate.contracts.nodes import Node, Edge, node_to_dict, node_from_dict, edge_to_dict
from src.envstate.contracts import nodes as nodes_mod

def test_node_roundtrip_flattens_data():
    n = Node("contract:python_import:cv2", "Contract",
             {"level": "atomic", "kind": "python_import", "subject": "cv2"})
    d = node_to_dict(n)
    assert d["id"] == "contract:python_import:cv2" and d["kind"] == "python_import"
    assert node_from_dict(d) == n

def test_contract_status_event_removed():
    assert not hasattr(nodes_mod, "ContractStatusEvent")
```

- [ ] **Step 2: Run** → FAIL (`ContractStatusEvent` still present).

- [ ] **Step 3: Edit `nodes.py`** — delete the `ContractStatusEvent` dataclass and its `event_to_dict`/`event_from_dict` helpers. Keep `Node`, `Edge`, and their (de)serializers verbatim (nodes.py:9-65).

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/nodes.py tests/test_contracts_nodes.py
git commit -m "refactor(contracts): drop ContractStatusEvent (status is projected)"
```

---

### Task 4: Rewrite `graph.py` — container + `project_status` + traversal

**Files:**
- Modify: `src/envstate/contracts/graph.py`
- Test: `tests/test_contracts_graph.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_graph.py
from src.envstate.contracts.nodes import Node, Edge
from src.envstate.contracts.graph import (
    ContractGraph, project_status, depends_on_closure, frontier_by_layer, goal_ready,
)

def _g():
    nodes = (
        Node("contract:goal:repo_tests_pass", "Contract",
             {"level": "goal", "required": True, "layer": "tests", "kind": "tests_pass"}),
        Node("contract:goal:repo_imports_work", "Contract",
             {"level": "goal", "required": True, "layer": "deps", "kind": "imports_work"}),
        Node("contract:python_import:cv2", "Contract",
             {"level": "atomic", "layer": "deps", "kind": "python_import", "subject": "cv2"}),
        Node("blocker:importerror-libgl", "Blocker",
             {"signature": "ImportError: libGL.so.1", "kind": "missing_system_library",
              "layer": "system", "active": True, "summary": "libGL missing"}),
    )
    edges = (
        Edge("contract:goal:repo_tests_pass", "depends_on", "contract:goal:repo_imports_work"),
        Edge("contract:goal:repo_imports_work", "depends_on", "contract:python_import:cv2"),
        Edge("blocker:importerror-libgl", "violates", "contract:python_import:cv2"),
    )
    return ContractGraph(nodes=nodes, edges=edges)

def test_project_status_violated_from_active_blocker():
    g = _g()
    assert project_status(g, "contract:python_import:cv2", frozenset()) == "violated"

def test_project_status_satisfied_from_host_set():
    g = _g()
    assert project_status(g, "contract:python_import:cv2",
                          frozenset({"contract:python_import:cv2"})) == "satisfied"

def test_project_status_unknown_when_no_evidence():
    g = _g()
    assert project_status(g, "contract:goal:repo_imports_work", frozenset()) == "unknown"

def test_depends_on_closure_reaches_atomic():
    g = _g()
    cl = depends_on_closure(g, "contract:goal:repo_tests_pass")
    assert "contract:python_import:cv2" in cl

def test_frontier_by_layer_groups_unsatisfied():
    g = _g()
    fr = frontier_by_layer(g, frozenset())
    assert "contract:python_import:cv2" in fr["deps"]

def test_goal_ready_false_until_all_satisfied():
    g = _g()
    assert goal_ready(g, frozenset()) is False
    everything = frozenset(n.id for n in g.nodes if n.type == "Contract")
    assert goal_ready(g, everything) is True

def test_diagnostic_notes_roundtrip_capped():
    g = ContractGraph(diagnostic_notes=tuple(str(i) for i in range(15)))
    assert ContractGraph.from_dict(g.to_dict()).diagnostic_notes == g.diagnostic_notes
```

- [ ] **Step 2: Run** → FAIL (`project_status`/traversal absent; `ContractGraph` still has `status_events`).

- [ ] **Step 3: Rewrite `graph.py`**:

```python
"""Immutable ContractGraph container + status projection + traversal."""
from __future__ import annotations
import dataclasses
from typing import Optional
from .nodes import Edge, Node, edge_from_dict, edge_to_dict, node_from_dict, node_to_dict

@dataclasses.dataclass(frozen=True)
class ContractGraph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    diagnostic_notes: tuple[str, ...] = ()

    @staticmethod
    def empty() -> "ContractGraph":
        return ContractGraph()

    def node(self, node_id):
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def has_node(self, node_id): return self.node(node_id) is not None
    def active_nodes(self): return tuple(n for n in self.nodes if not n.invalidated)
    def nodes_by_type(self, t): return tuple(n for n in self.active_nodes() if n.type == t)
    def contracts(self): return self.nodes_by_type("Contract")
    def blockers(self): return self.nodes_by_type("Blocker")
    def attempts(self): return self.nodes_by_type("Attempt")

    def out_edges(self, source, edge_type=None):
        return tuple(e for e in self.edges if not e.invalidated and e.source == source
                     and (edge_type is None or e.type == edge_type))

    def in_edges(self, target, edge_type=None):
        return tuple(e for e in self.edges if not e.invalidated and e.target == target
                     and (edge_type is None or e.type == edge_type))

    def goal_contracts(self):
        return tuple(n for n in self.contracts() if n.data.get("level") == "goal")

    def required_goal_contracts(self):
        return tuple(n for n in self.goal_contracts() if bool(n.data.get("required", False)))

    def to_dict(self):
        return {"nodes": [node_to_dict(n) for n in self.nodes],
                "edges": [edge_to_dict(e) for e in self.edges],
                "diagnostic_notes": list(self.diagnostic_notes)}

    @staticmethod
    def from_dict(d):
        d = d or {}
        return ContractGraph(
            nodes=tuple(node_from_dict(x) for x in d.get("nodes", [])),
            edges=tuple(edge_from_dict(x) for x in d.get("edges", [])),
            diagnostic_notes=tuple(d.get("diagnostic_notes", [])))


def _active_blocker_violates(graph: ContractGraph, contract_id: str) -> bool:
    for e in graph.in_edges(contract_id, "violates"):
        b = graph.node(e.source)
        if b is not None and not b.invalidated and bool(b.data.get("active", True)):
            return True
    return False


def project_status(graph: ContractGraph, contract_id: str, host_satisfied) -> str:
    if contract_id in host_satisfied:
        return "satisfied"
    if _active_blocker_violates(graph, contract_id):
        return "violated"
    return "unknown"


def depends_on_closure(graph: ContractGraph, goal_id: str) -> tuple[str, ...]:
    seen, stack, out = set(), [goal_id], []
    while stack:
        cur = stack.pop()
        for e in graph.out_edges(cur, "depends_on"):
            if e.target not in seen:
                seen.add(e.target); out.append(e.target); stack.append(e.target)
    return tuple(out)


def root_blockers(graph: ContractGraph) -> tuple[Node, ...]:
    active = [b for b in graph.blockers() if bool(b.data.get("active", True))]
    return tuple(sorted(active, key=lambda b: 0 if b.data.get("root_or_downstream") == "root" else 1))


def frontier_by_layer(graph: ContractGraph, host_satisfied) -> dict:
    out: dict[str, list[str]] = {}
    for c in graph.contracts():
        if project_status(graph, c.id, host_satisfied) != "satisfied":
            out.setdefault(c.data.get("layer", "deps"), []).append(c.id)
    return {k: tuple(v) for k, v in out.items()}


def goal_ready(graph: ContractGraph, host_satisfied) -> bool:
    required = graph.required_goal_contracts()
    if not required:
        return False
    for goal in required:
        if project_status(graph, goal.id, host_satisfied) != "satisfied":
            return False
        for dep in depends_on_closure(graph, goal.id):
            if project_status(graph, dep, host_satisfied) != "satisfied":
                return False
    return True
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/graph.py tests/test_contracts_graph.py
git commit -m "feat(contracts): immutable graph + per-cycle status projection + traversal"
```

---

## PHASE B — Host projection (goals, blocker extraction, promotion, status, outcomes)

### Task 5: Rewrite `goals.py` — coarse backbone seed

**Files:**
- Modify: `src/envstate/contracts/goals.py`
- Test: `tests/test_contracts_goals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_goals.py
from src.envstate.contracts.goals import seed_backbone, BACKBONE_EDGES, GOAL_IDS, FOUNDATIONAL_IDS

def test_seed_emits_seven_goals_and_four_foundational():
    nodes, edges = seed_backbone()
    ids = {n.id for n in nodes}
    assert GOAL_IDS <= ids and FOUNDATIONAL_IDS <= ids
    assert len(GOAL_IDS) == 7 and len(FOUNDATIONAL_IDS) == 4

def test_top_goal_is_required_and_named_repo_tests_pass():
    nodes, _ = seed_backbone()
    top = next(n for n in nodes if n.id == "contract:goal:repo_tests_pass")
    assert top.data["level"] == "goal" and top.data["required"] is True

def test_no_per_dep_contracts_seeded():
    nodes, _ = seed_backbone()
    # backbone never mints python_import contracts at cold-start
    assert not any(n.data.get("kind") == "python_import" for n in nodes)

def test_backbone_edges_wire_tests_pass_to_phases():
    _, edges = seed_backbone()
    pairs = {(e.source, e.target) for e in edges}
    assert ("contract:goal:repo_tests_pass", "contract:goal:repo_imports_work") in pairs
    assert ("contract:goal:repo_deps_installed", "contract:package_manager_available") in pairs
```

- [ ] **Step 2: Run** → FAIL (current `seed_goal_template` loops per dep).

- [ ] **Step 3: Rewrite `goals.py`** (no `required` arg; pure constant backbone):

```python
"""Coarse goal/phase backbone seeded once at cold-start (spec §6.2)."""
from __future__ import annotations
from . import ids
from .nodes import Edge, Node

GOAL_NAMES = ("repo_tests_pass", "repo_tests_collect", "repo_imports_work",
              "repo_deps_installed", "repo_build_ready", "repo_services_ready", "repo_config_ready")
FOUNDATIONAL = ("python_version_compatible", "package_manager_available",
                "test_runner_available", "project_installable")
GOAL_IDS = frozenset(ids.goal_contract_id(n) for n in GOAL_NAMES)
FOUNDATIONAL_IDS = frozenset(ids.foundational_contract_id(n) for n in FOUNDATIONAL)
GOAL_TESTS_PASS = ids.goal_contract_id("repo_tests_pass")

_LAYER = {"repo_tests_pass": "tests", "repo_tests_collect": "tests", "repo_imports_work": "deps",
          "repo_deps_installed": "deps", "repo_build_ready": "build",
          "repo_services_ready": "runtime", "repo_config_ready": "config"}
_CHECK = {"repo_tests_pass": "python -m pytest -q",
          "repo_tests_collect": "python -m pytest --collect-only -q --disable-warnings"}

# (source_name, target_id) ordering backbone
_BACKBONE = [
    ("repo_tests_pass", ids.goal_contract_id("repo_tests_collect")),
    ("repo_tests_pass", ids.goal_contract_id("repo_imports_work")),
    ("repo_tests_pass", ids.goal_contract_id("repo_deps_installed")),
    ("repo_tests_pass", ids.goal_contract_id("repo_build_ready")),
    ("repo_tests_pass", ids.goal_contract_id("repo_services_ready")),
    ("repo_tests_pass", ids.goal_contract_id("repo_config_ready")),
    ("repo_tests_collect", ids.goal_contract_id("repo_imports_work")),
    ("repo_tests_collect", ids.foundational_contract_id("test_runner_available")),
    ("repo_imports_work", ids.goal_contract_id("repo_deps_installed")),
    ("repo_deps_installed", ids.foundational_contract_id("package_manager_available")),
    ("repo_deps_installed", ids.foundational_contract_id("python_version_compatible")),
]
BACKBONE_EDGES = tuple(Edge(ids.goal_contract_id(s), "depends_on", t) for s, t in _BACKBONE)

def seed_backbone() -> tuple[list[Node], list[Edge]]:
    nodes: list[Node] = []
    for name in GOAL_NAMES:
        nodes.append(Node(ids.goal_contract_id(name), "Contract",
            {"level": "goal", "kind": name, "subject": "repo", "layer": _LAYER[name],
             "required": name == "repo_tests_pass", "check": _CHECK.get(name, ""),
             "source_refs": ["goal"], "evidence_refs": [],
             "description": f"Goal contract: {name}.", "metadata": {}}))
    for name in FOUNDATIONAL:
        nodes.append(Node(ids.foundational_contract_id(name), "Contract",
            {"level": "atomic", "kind": name, "subject": name, "layer": "runtime",
             "required": False, "check": "", "source_refs": ["foundational"],
             "evidence_refs": [], "description": f"Foundational: {name}.", "metadata": {}}))
    return nodes, list(BACKBONE_EDGES)
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/goals.py tests/test_contracts_goals.py
git commit -m "feat(contracts): seed coarse 7-goal backbone (no per-dep contracts)"
```

---

### Task 6: Deterministic blocker-subject extraction + atomic promotion

**Files:**
- Create: `src/envstate/contracts/extract.py`
- Test: `tests/test_contracts_extract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_extract.py
from src.envstate.contracts.extract import extract_blocker_subject, promote_atomic_contracts
from src.envstate.contracts.graph import ContractGraph

def test_extract_module_not_found():
    assert extract_blocker_subject("ModuleNotFoundError: No module named 'yaml'") == ("yaml", "module_not_found")

def test_extract_missing_binary():
    assert extract_blocker_subject("pg_config: command not found") == ("pg_config", "missing_binary")
    assert extract_blocker_subject("pg_config executable not found") == ("pg_config", "missing_binary")

def test_extract_missing_system_library():
    subj, kind = extract_blocker_subject("ImportError: libGL.so.1: cannot open shared object file")
    assert subj == "libGL.so.1" and kind == "missing_system_library"

def test_extract_unknown_returns_none():
    assert extract_blocker_subject("some unrelated text") == (None, "unknown")

def test_promote_creates_contract_for_module_not_found():
    nodes = promote_atomic_contracts(ContractGraph.empty(),
                                     ["ModuleNotFoundError: No module named 'yaml'"])
    assert any(n.id == "contract:python_import:yaml" for n in nodes)

def test_promote_is_idempotent_against_existing():
    from src.envstate.contracts.nodes import Node
    g = ContractGraph(nodes=(Node("contract:python_import:yaml", "Contract", {"level": "atomic"}),))
    assert promote_atomic_contracts(g, ["ModuleNotFoundError: No module named 'yaml'"]) == []
```

- [ ] **Step 2: Run** → FAIL (module absent).

- [ ] **Step 3: Create `extract.py`**:

```python
"""Deterministic failure-signature -> (subject, blocker_kind) + atomic contract promotion (spec §6.4)."""
from __future__ import annotations
import re
from . import ids
from .nodes import Node

# (compiled pattern, blocker_kind, contract_kind)
_RULES = [
    (re.compile(r"No module named ['\"]([A-Za-z0-9_.]+)['\"]"), "module_not_found", "python_import"),
    (re.compile(r"ModuleNotFoundError:\s*([A-Za-z0-9_.]+)"), "module_not_found", "python_import"),
    (re.compile(r"([A-Za-z0-9_.+-]+)\s*:?\s*(?:command not found|executable not found)", re.I),
     "missing_binary", "binary"),
    (re.compile(r"(lib[A-Za-z0-9_.+-]+\.so[0-9.]*)\s*:\s*cannot open shared object", re.I),
     "missing_system_library", "system_library"),
    (re.compile(r"fatal error:\s*([A-Za-z0-9_./+-]+\.h)\b", re.I), "missing_system_library", "system_library"),
]

def extract_blocker_subject(signature: str) -> tuple[str | None, str]:
    if not signature:
        return None, "unknown"
    for pat, kind, _ in _RULES:
        m = pat.search(signature)
        if m:
            return m.group(1), kind
    return None, "unknown"

def _contract_kind_for(signature: str) -> tuple[str | None, str | None]:
    for pat, _kind, ckind in _RULES:
        m = pat.search(signature)
        if m:
            return m.group(1), ckind
    return None, None

def promote_atomic_contracts(graph, signatures) -> list[Node]:
    out, seen = [], set()
    for sig in signatures:
        subject, ckind = _contract_kind_for(sig)
        if subject is None:
            continue
        cid = ids.contract_id(ckind, subject)
        if cid in seen or graph.has_node(cid):
            continue
        seen.add(cid)
        layer = {"python_import": "deps", "binary": "system", "system_library": "system"}[ckind]
        out.append(Node(cid, "Contract", {"level": "atomic", "kind": ckind, "subject": subject,
            "layer": layer, "check": "", "source_refs": [f"signature:{sig[:60]}"],
            "evidence_refs": [], "description": f"{ckind} obligation: {subject}.", "metadata": {}}))
    return out
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/extract.py tests/test_contracts_extract.py
git commit -m "feat(contracts): deterministic blocker-subject extraction + atomic promotion"
```

---

### Task 7: One-shot import sweep command + `host_satisfied_set` + outcome derivation

**Files:**
- Modify: `src/envstate/contracts/validators.py`
- Test: `tests/test_contracts_validators.py`

The validators module keeps `KNOWN_IMPORT_NAMES`/`resolve_import_name` (validators.py:22-50) verbatim. Replace the per-contract `run_confirmed_validators` with: (a) a `build_import_sweep_command()` that returns a single `/bin/sh -lc`-safe heredoc importing every declared dep, and (b) `host_satisfied_set` + `derive_attempt_outcome` helpers.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_validators.py
from src.envstate.contracts.validators import (
    build_import_sweep_command, resolve_import_name, host_satisfied_set, derive_attempt_outcome,
)
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge

def test_resolve_import_name_kept():
    assert resolve_import_name("opencv-python") == "cv2"
    assert resolve_import_name("PyYAML") == "yaml"

def test_import_sweep_command_is_posix_sh_safe_single_call():
    cmd = build_import_sweep_command(["opencv-python", "pyyaml"])
    assert cmd.count("<<") == 1           # single heredoc -> single exec_readonly call
    assert "[[" not in cmd and "pipefail" not in cmd   # no bashisms

def test_host_satisfied_from_import_results():
    g = ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract",
        {"level": "atomic", "kind": "python_import", "subject": "cv2"}),))
    world = type("W", (), {"import_results": (("cv2", True),), "done_flag": False})()
    sat = host_satisfied_set(g, world, ledger_events=[])
    assert "contract:python_import:cv2" in sat

def test_derive_outcome_ok_when_target_satisfied():
    g = ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),
                             Node("attempt:x", "Attempt",
                                  {"created_from_target_node_ids": ["contract:python_import:cv2"]}),))
    out = derive_attempt_outcome(g, "attempt:x", frozenset({"contract:python_import:cv2"}), step_failed=False)
    assert out == "ok"

def test_derive_outcome_ok_but_still_blocked():
    g = ContractGraph(
        nodes=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),
               Node("blocker:b", "Blocker", {"active": True, "signature": "x"}),
               Node("attempt:x", "Attempt",
                    {"created_from_target_node_ids": ["contract:python_import:cv2"]})),
        edges=(Edge("blocker:b", "violates", "contract:python_import:cv2"),))
    out = derive_attempt_outcome(g, "attempt:x", frozenset(), step_failed=False)
    assert out == "ok_but_still_blocked"

def test_derive_outcome_failed_on_step_failure():
    g = ContractGraph(nodes=(Node("attempt:x", "Attempt", {"created_from_target_node_ids": []}),))
    assert derive_attempt_outcome(g, "attempt:x", frozenset(), step_failed=True) == "failed"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Edit `validators.py`** — keep `KNOWN_IMPORT_NAMES`/`resolve_import_name`/`_DYNAMIC_IMPORT_PROBE_TMPL`. Replace `run_confirmed_validators`/`_REGISTRY` with:

```python
import json as _json
from .graph import project_status

def build_import_sweep_command(declared_dist_names) -> str:
    """One /bin/sh -lc-safe heredoc that imports each declared dep and prints JSON {import_name: ok}."""
    imports = [resolve_import_name(d) for d in declared_dist_names]
    py_list = "[" + ",".join(repr(i) for i in imports) + "]"
    return (
        "python - <<'_E_'\n"
        "import importlib, json\n"
        f"_names={py_list}\n"
        "_res={}\n"
        "for _n in _names:\n"
        "    try:\n        importlib.import_module(_n); _res[_n]=True\n"
        "    except Exception:\n        _res[_n]=False\n"
        "print(json.dumps(_res))\n"
        "_E_"
    )

def parse_import_sweep(stdout: str) -> tuple[tuple[str, bool], ...]:
    try:
        d = _json.loads(stdout.strip().splitlines()[-1])
        return tuple((str(k), bool(v)) for k, v in d.items())
    except Exception:
        return ()

def host_satisfied_set(graph, world_map, ledger_events) -> frozenset:
    """Contract ids the host certifies this cycle (spec §6.3)."""
    sat = set()
    ok_imports = {name for name, ok in getattr(world_map, "import_results", ()) if ok}
    for c in graph.contracts():
        if c.data.get("kind") == "python_import":
            if resolve_import_name(c.data.get("subject", "")) in ok_imports \
               or c.data.get("subject", "") in ok_imports:
                sat.add(c.id)
    # goal: repo_tests_pass is host-certified by the done-gate (handled in projection refresh)
    return frozenset(sat)

def derive_attempt_outcome(graph, attempt_id, host_satisfied, step_failed: bool) -> str:
    if step_failed:
        return "failed"
    node = graph.node(attempt_id)
    targets = (node.data.get("created_from_target_node_ids") or []) if node else []
    if not targets:
        return "ok"
    statuses = [project_status(graph, t, host_satisfied) for t in targets]
    if all(s == "satisfied" for s in statuses):
        return "ok"
    if any(s == "violated" for s in statuses):
        return "ok_but_still_blocked"
    return "ok"
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/validators.py tests/test_contracts_validators.py
git commit -m "feat(contracts): one-shot import sweep + host-satisfied set + attempt outcome derivation"
```

---

### Task 8: Rewrite `projection.py` — `refresh_host_graph`

**Files:**
- Modify: `src/envstate/contracts/projection.py`
- Test: `tests/test_refresh_host_graph.py`

`refresh_host_graph` now: (1) seeds the backbone once (idempotent), (2) deterministically promotes atomic contracts from failure signatures in the ledger, (3) marks Blockers inactive when their subject is confirmed installed, (4) computes `host_satisfied` and emits the goal-satisfied certification via the done-gate, (5) re-projects `open_problems` as a derived view, and returns a new map. It no longer creates Failure/Capability/CommandExecution/Requirement/RepoArtifact/EnvironmentRevision nodes.

> **Prerequisite (forward ref to Task 13):** this task's `refresh_host_graph` writes `merge_map(..., host_satisfied=...)` and reads `world_map.import_results`. If executing strictly in order, FIRST add the three `host_satisfied: frozenset = frozenset()` lines to `WorldModelMap` (dataclass default + `merge_map` kwarg + replace branch) and the `import_results` default — these are the first sub-steps of Task 13; pulling them forward here keeps Task 8 self-contained. Do the remaining Task 13 work (DependencyState, RecipePatch, serialization, `derive_open_problems`) in Task 13.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_refresh_host_graph.py
from src.envstate.contracts.projection import refresh_host_graph
from src.envstate.contracts.goals import GOAL_TESTS_PASS
from src.envstate.world_model import initial_map, merge_map, Fact
from src.envstate.ledger import ActionLedger, ActionEvent

def _ledger(events):
    led = ActionLedger()
    for e in events:
        led.append(e)
    return led

def _base():
    return initial_map(base_image="python:3.11", workdir="/repo", language="python 3.11",
                       build_system="pip", repo_layout=("tests/", "requirements.txt"),
                       required=(Fact("opencv-python", ""),))

def test_seeds_backbone_idempotently():
    m = refresh_host_graph(_base(), _ledger([]), snapshot=None, exec_readonly=None, current_revision=0)
    g1 = m.contract_graph
    m2 = refresh_host_graph(m, _ledger([]), snapshot=None, exec_readonly=None, current_revision=0)
    assert len(m2.contract_graph.nodes) == len(g1.nodes)        # no duplicates
    assert g1.has_node(GOAL_TESTS_PASS)

def test_promotes_atomic_contract_from_failure_signature():
    led = _ledger([ActionEvent(step=1, cmd="python -c 'import cv2'", rc=1,
                               stdout="ImportError: libGL.so.1: cannot open shared object file",
                               env_revision_before=0, env_revision_after=0, mutation_class=None)])
    m = refresh_host_graph(_base(), led, snapshot=None, exec_readonly=None, current_revision=0)
    assert m.contract_graph.has_node("contract:system_library:libGL.so.1") \
        or m.contract_graph.has_node("contract:system_library:libgl.so.1")

def test_done_gate_does_not_satisfy_goal_on_collect_only():
    # collect-only (no "N passed") must NOT mark the goal satisfied even with done_flag=True
    led = _ledger([ActionEvent(step=1, cmd="python -m pytest --collect-only -q", rc=0,
                               stdout="collected 5 items", env_revision_before=0,
                               env_revision_after=0, mutation_class=None)])
    m = merge_map(_base(), done_flag=True)
    m = refresh_host_graph(m, led, snapshot=None, exec_readonly=None, current_revision=0)
    assert m.host_satisfied == frozenset() or GOAL_TESTS_PASS not in m.host_satisfied
```

> The real-pass satisfaction case (`done_flag=True` + a real `N passed` pytest run → `GOAL_TESTS_PASS in m.host_satisfied`) is asserted in Task 13's `test_world_model_v2` once the `host_satisfied` field exists, and again in Task 20's integration suite.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Rewrite `projection.py`** following spec §6 and reusing the current `_verified_test_command_id` (projection.py:161-185) verbatim for the done-gate. Skeleton:

```python
"""Host-owned deterministic projection into the contract graph (spec §6). No LLM."""
from __future__ import annotations
from typing import Any
from . import goals, ids
from .apply import apply_patch
from .extract import extract_blocker_subject, promote_atomic_contracts
from .graph import ContractGraph
from .patch import GraphPatch
from .validators import host_satisfied_set
from ..world_model import merge_map

# Paste the CURRENT `_verified_test_command_id(events)` body (pre-rewrite projection.py:161-185)
# UNCHANGED — it returns the latest rc=0 real pytest command id (pytest in cmd, not --collect-only,
# stdout shows execution/completion via maintainer._shows_execution/_shows_pytest_completion).
def _verified_test_command_id(events):
    from src.envstate.maintainer import _shows_execution, _shows_pytest_completion
    for ev in reversed(list(events)):
        if ev.rc != 0 or "pytest" not in ev.cmd or "--collect-only" in ev.cmd:
            continue
        out = getattr(ev, "stdout", "") or ""
        if _shows_execution(out) or _shows_pytest_completion(out):
            return ev.step   # any non-None value; CommandExecution nodes no longer exist
    return None

def _failure_signatures(events) -> list[str]:
    return [ (e.stdout or "")[-400:] for e in events if getattr(e, "rc", 0) != 0 ]

def _auto_resolve_blockers(graph: ContractGraph, installed_names: set[str], system_names: set[str]):
    """Flip Blocker.active=False when its extracted subject is confirmed present."""
    updated = []
    for b in graph.blockers():
        if not bool(b.data.get("active", True)):
            continue
        subj = (b.data.get("metadata") or {}).get("extracted_subject") or ""
        s = subj.lower()
        if s and (s in installed_names or s in system_names or s.replace("lib", "") in system_names):
            new = dict(b.data); new["active"] = False
            import dataclasses
            updated.append(dataclasses.replace(b, data=new))
    return updated

def refresh_host_graph(world_map, ledger, snapshot, exec_readonly, current_revision, *, on_error=None):
    graph: ContractGraph = world_map.contract_graph
    events = list(ledger.events())

    # 1. seed backbone (idempotent)
    seed_nodes, seed_edges = goals.seed_backbone()
    add_nodes = [n for n in seed_nodes if not graph.has_node(n.id)]
    existing_edges = {(e.source, e.type, e.target) for e in graph.edges}
    add_edges = [e for e in seed_edges if (e.source, e.type, e.target) not in existing_edges]

    # 2. deterministic atomic promotion from failure signatures
    sigs = _failure_signatures(events)
    promoted = promote_atomic_contracts(
        apply_patch(graph, GraphPatch(add_contracts=tuple(add_nodes))), sigs)
    add_nodes += [n for n in promoted if not graph.has_node(n.id)]

    graph = apply_patch(graph, GraphPatch(add_contracts=tuple(add_nodes), add_edges=tuple(add_edges)))

    # 3. blocker auto-resolve
    installed = {f.name.lower() for f in world_map.installed}
    system = {f.name.lower() for f in world_map.system_installed}
    resolved = _auto_resolve_blockers(graph, installed, system)
    if resolved:
        graph = apply_patch(graph, GraphPatch(update_blockers=tuple(resolved)))

    # 4. host_satisfied set + done-gate goal certification
    host_satisfied = set(host_satisfied_set(graph, world_map, events))
    if world_map.done_flag and _verified_test_command_id(events) is not None:
        host_satisfied.add(goals.GOAL_TESTS_PASS)
        # a real pass implies collect + imports + deps satisfied:
        for nm in ("repo_tests_collect", "repo_imports_work", "repo_deps_installed"):
            host_satisfied.add(ids.goal_contract_id(nm))

    return merge_map(world_map, contract_graph=graph, host_satisfied=frozenset(host_satisfied))
```

> `GraphPatch` gains `add_contracts`, `add_blockers`, `add_edges`, `update_blockers`, `update_contracts` in Task 9; if implementing Task 8 first, stub those fields. `apply_patch` handles them in Task 11.

- [ ] **Step 4: Run** → PASS (the two minimal assertions: backbone seeded idempotently, promotion fires).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/projection.py tests/test_refresh_host_graph.py
git commit -m "feat(contracts): host projection — backbone seed, promotion, blocker auto-resolve, done-gate"
```

---

## PHASE C — Patch / validation / apply / attempts

### Task 9: Rewrite `patch.py` — semantic `GraphPatch`

**Files:**
- Modify: `src/envstate/contracts/patch.py`
- Test: `tests/test_contracts_patch.py`

`GraphPatch` carries both the Maintainer-facing semantic keys AND the host-internal `update_blockers`/`update_contracts`/`invalidate_*` fields used by projection. The *parser* (`parse_graph_patch`) only reads the Maintainer-allowed keys.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_patch.py
from src.envstate.contracts.patch import GraphPatch, parse_graph_patch

def test_parse_reads_only_semantic_keys():
    p = parse_graph_patch({
        "add_contracts": [{"id": "contract:python_import:cv2", "type": "Contract", "level": "atomic"}],
        "add_blockers": [{"id": "blocker:b", "type": "Blocker", "signature": "x", "active": True}],
        "add_edges": [{"source": "blocker:b", "type": "violates", "target": "contract:python_import:cv2"}],
        "update_blocker_classification": [{"blocker_id": "blocker:b", "root_or_downstream": "root"}],
        "update_contract_description": [{"contract_id": "contract:python_import:cv2", "description": "needs cv2"}],
        "diagnostic_notes": ["cv2 blocked by libGL"],
        "add_status_events": [{"contract_id": "x", "status": "satisfied"}],  # IGNORED (not a key)
    })
    assert len(p.add_contracts) == 1 and len(p.add_blockers) == 1 and len(p.add_edges) == 1
    assert p.update_blocker_classification[0]["root_or_downstream"] == "root"
    assert p.diagnostic_notes == ("cv2 blocked by libGL",)

def test_parse_tolerates_garbage():
    assert parse_graph_patch(None).is_empty()
    assert parse_graph_patch({"add_contracts": "nope"}).is_empty()
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Rewrite `patch.py`**:

```python
"""Semantic GraphPatch (spec §7) + host-internal update fields + tolerant parser."""
from __future__ import annotations
import dataclasses
from typing import Any
from .nodes import Edge, Node, edge_from_dict, node_from_dict

@dataclasses.dataclass(frozen=True)
class GraphPatch:
    add_contracts: tuple[Node, ...] = ()
    add_blockers: tuple[Node, ...] = ()
    add_edges: tuple[Edge, ...] = ()
    update_blocker_classification: tuple[dict, ...] = ()
    update_contract_description: tuple[dict, ...] = ()
    diagnostic_notes: tuple[str, ...] = ()
    # host-internal (never parsed from LLM):
    add_attempts: tuple[Node, ...] = ()
    update_blockers: tuple[Node, ...] = ()
    update_contracts: tuple[Node, ...] = ()
    update_attempts: tuple[Node, ...] = ()
    invalidate_nodes: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.add_contracts or self.add_blockers or self.add_edges
                    or self.update_blocker_classification or self.update_contract_description
                    or self.diagnostic_notes or self.add_attempts or self.update_blockers
                    or self.update_contracts or self.update_attempts or self.invalidate_nodes)

def _nodes(v, ntype):
    return tuple(node_from_dict(x) for x in v if isinstance(x, dict)) if isinstance(v, list) else ()

def _dicts(v):
    return tuple(x for x in v if isinstance(x, dict)) if isinstance(v, list) else ()

def parse_graph_patch(d: Any) -> GraphPatch:
    if not isinstance(d, dict):
        return GraphPatch()
    return GraphPatch(
        add_contracts=_nodes(d.get("add_contracts"), "Contract"),
        add_blockers=_nodes(d.get("add_blockers"), "Blocker"),
        add_edges=tuple(edge_from_dict(x) for x in d.get("add_edges", []) if isinstance(x, dict))
                  if isinstance(d.get("add_edges"), list) else (),
        update_blocker_classification=_dicts(d.get("update_blocker_classification")),
        update_contract_description=_dicts(d.get("update_contract_description")),
        diagnostic_notes=tuple(str(x) for x in d.get("diagnostic_notes", []))
                         if isinstance(d.get("diagnostic_notes"), list) else (),
    )
```

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/patch.py tests/test_contracts_patch.py
git commit -m "feat(contracts): semantic GraphPatch keys + tolerant parser"
```

---

### Task 10: Rewrite `validation.py` — field-level ownership + reject rules

**Files:**
- Modify: `src/envstate/contracts/validation.py`
- Test: `tests/test_contracts_validation.py`

Implements spec §8: 3-edge validity, field-level ownership (Maintainer may not write status/outcome/active, may not create Attempts), grounded blockers (every `Blocker.evidence_refs` cites a real command id), no inventory mirror (`MAX_PROMOTIONS_PER_CYCLE=8`), backbone attachment, reference-integrity.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_validation.py
from src.envstate.contracts.validation import validate_patch, MAX_PROMOTIONS_PER_CYCLE
from src.envstate.contracts.patch import GraphPatch
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge

KNOWN_CMDS = frozenset({"cmd:001"})

def _graph_with_goal():
    return ContractGraph(nodes=(Node("contract:goal:repo_imports_work", "Contract",
                                     {"level": "goal", "layer": "deps"}),))

def test_rejects_attempt_creation_by_maintainer():
    p = GraphPatch(add_contracts=(Node("attempt:x", "Attempt", {}),))
    errs = validate_patch(_graph_with_goal(), p, scope="maintainer", known_command_ids=KNOWN_CMDS)
    assert any("Attempt" in e for e in errs)

def test_rejects_blocker_without_command_evidence():
    p = GraphPatch(add_blockers=(Node("blocker:b", "Blocker",
        {"signature": "x", "active": True, "evidence_refs": ["cmd:999"]}),),
        add_edges=(Edge("blocker:b", "violates", "contract:goal:repo_imports_work"),))
    errs = validate_patch(_graph_with_goal(), p, scope="maintainer", known_command_ids=KNOWN_CMDS)
    assert any("evidence" in e.lower() for e in errs)

def test_rejects_orphan_atomic_contract():
    p = GraphPatch(add_contracts=(Node("contract:python_import:cv2", "Contract",
        {"level": "atomic", "layer": "deps"}),))  # no depends_on/violates linking it under backbone
    errs = validate_patch(_graph_with_goal(), p, scope="maintainer", known_command_ids=KNOWN_CMDS)
    assert any("backbone" in e.lower() or "orphan" in e.lower() for e in errs)

def test_rejects_too_many_promotions():
    contracts = tuple(Node(f"contract:python_import:p{i}", "Contract", {"level": "atomic", "layer": "deps"})
                      for i in range(MAX_PROMOTIONS_PER_CYCLE + 1))
    p = GraphPatch(add_contracts=contracts)
    errs = validate_patch(_graph_with_goal(), p, scope="maintainer", known_command_ids=KNOWN_CMDS)
    assert any("inventory" in e.lower() or "too many" in e.lower() for e in errs)

def test_valid_maintainer_patch_passes():
    p = GraphPatch(
        add_blockers=(Node("blocker:b", "Blocker",
            {"signature": "x", "active": True, "evidence_refs": ["cmd:001"]}),),
        add_edges=(Edge("blocker:b", "violates", "contract:goal:repo_imports_work"),))
    assert validate_patch(_graph_with_goal(), p, scope="maintainer", known_command_ids=KNOWN_CMDS) == []
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Rewrite `validation.py`** per spec §8. Key points: signature is `validate_patch(graph, patch, *, scope, known_command_ids=frozenset())`. `scope="maintainer"` forbids any `add_contracts` node whose `type != "Contract"`, any node `data` containing a `MAINTAINER_FORBIDDEN_FIELDS` key with a non-None value, blockers whose `evidence_refs` aren't all in `known_command_ids`, atomic contracts/blockers not reachable under a goal via a `depends_on`/`violates` edge present in `graph.edges + patch.add_edges`, and `len(add_contracts) > MAX_PROMOTIONS_PER_CYCLE`. Edge validity uses `schema.EDGE_RULES`. `scope="host"` skips the ownership/grounding/promotion-bound checks.

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/validation.py tests/test_contracts_validation.py
git commit -m "feat(contracts): field-level ownership + grounded-blocker + no-inventory-mirror validation"
```

---

### Task 11: Update `apply.py` for the semantic patch

**Files:**
- Modify: `src/envstate/contracts/apply.py`
- Test: `tests/test_contracts_apply.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_apply.py
from src.envstate.contracts.apply import apply_patch
from src.envstate.contracts.patch import GraphPatch
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge

def test_apply_adds_and_caps_notes():
    g = ContractGraph(diagnostic_notes=tuple(str(i) for i in range(9)))
    p = GraphPatch(add_contracts=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),),
                   diagnostic_notes=("new1", "new2"))
    g2 = apply_patch(g, p)
    assert g2.has_node("contract:python_import:cv2")
    assert len(g2.diagnostic_notes) == 10 and g2.diagnostic_notes[-1] == "new2"   # capped, newest kept

def test_update_blocker_classification_merges_fields():
    g = ContractGraph(nodes=(Node("blocker:b", "Blocker",
        {"root_or_downstream": "unknown", "active": True, "summary": "old"}),))
    p = GraphPatch(update_blocker_classification=({"blocker_id": "blocker:b",
                                                   "root_or_downstream": "root", "summary": "new"},))
    g2 = apply_patch(g, p)
    b = g2.node("blocker:b")
    assert b.data["root_or_downstream"] == "root" and b.data["summary"] == "new" and b.data["active"] is True

def test_update_blockers_replaces_node():
    import dataclasses
    g = ContractGraph(nodes=(Node("blocker:b", "Blocker", {"active": True}),))
    replaced = dataclasses.replace(g.node("blocker:b"), data={"active": False})
    g2 = apply_patch(g, GraphPatch(update_blockers=(replaced,)))
    assert g2.node("blocker:b").data["active"] is False
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Rewrite `apply.py`** — process `add_contracts`+`add_blockers`+`add_attempts` (as node adds, `setdefault`), `add_edges`, `update_blockers`+`update_contracts`+`update_attempts` (replace by id), `update_blocker_classification`/`update_contract_description` (merge the named fields into `data`), `invalidate_nodes`, and append `diagnostic_notes` keeping only the last 10. Return a new `ContractGraph`.

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/apply.py tests/test_contracts_apply.py
git commit -m "feat(contracts): apply semantic patch (field-level updates, capped notes)"
```

---

### Task 12: Replace `transitions.py` with `attempts.py`

**Files:**
- Create: `src/envstate/contracts/attempts.py`
- Delete: `src/envstate/contracts/transitions.py`
- Test: `tests/test_contracts_attempts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_attempts.py
from src.envstate.contracts.attempts import commit_attempt, attempt_node
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node

def _g():
    return ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),))

def test_commit_attempt_adds_node_and_addresses_edges():
    step = type("S", (), {"id": "step:1", "kind": "python_install",
                          "command": "pip install opencv-python",
                          "target_node_ids": ("contract:python_import:cv2",)})()
    patch = commit_attempt(_g(), step, proposed_by="planner")
    assert any(n.type == "Attempt" for n in patch.add_contracts) or any(
        n.type == "Attempt" for n in patch.add_blockers) or patch.add_edges
    g2 = __import__("src.envstate.contracts.apply", fromlist=["apply_patch"]).apply_patch(_g(), patch)
    a = next(n for n in g2.nodes if n.type == "Attempt")
    assert a.data["proposed_by"] == "planner" and a.data["outcome"] == "pending"
    assert any(e.type == "addresses" and e.target == "contract:python_import:cv2" for e in g2.edges)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Create `attempts.py`** — `attempt_node(step, proposed_by) -> Node` (id `ids.attempt_id(step.id + ":" + step.command[:20])`, data with `intent`/`kind=step.kind`/`proposed_by`/`commands=[step.command]`/`outcome="pending"`/`outcome_reason=""`/`evidence_refs=[]`/`created_from_target_node_ids=list(step.target_node_ids)`/`metadata={}`); `commit_attempt(graph, step, proposed_by) -> GraphPatch` adding the Attempt node via the `add_attempts` field (already defined on `GraphPatch` in Task 9, applied by `apply_patch` in Task 11) and one `addresses` edge per target that exists in the graph. Then `git rm src/envstate/contracts/transitions.py`.

> Attempt nodes are host-created, so they are NOT in `MAINTAINER_CREATABLE_NODE_TYPES` (the validator rejects any Maintainer-proposed Attempt — Task 10's `test_rejects_attempt_creation_by_maintainer`).

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/attempts.py tests/test_contracts_attempts.py
git rm src/envstate/contracts/transitions.py
git commit -m "feat(contracts): attempts.py — addresses edges + host-created Attempt nodes"
```

---

## PHASE D — World model & probes

### Task 13: `world_model.py` — DependencyState, RecipePatch, host_satisfied, derived open_problems

**Files:**
- Modify: `src/envstate/world_model.py`
- Test: `tests/test_world_model_v2.py`

Add (per keystone): `DependencyState`, `RecipeStep`, `RecipePatch`; `PlannerDecision.recipe_patch`; `WorldModelMap.dependency_state`, `.import_results`, `.host_satisfied`. Thread each new `WorldModelMap` field through the **7 places** (dataclass default, `merge_map` kwarg + replace branch, `map_to_dict`, `map_from_dict`, `initial_map`, and — for probe-sourced fields — `EnvSnapshot`/`apply_deterministic`). Add `derive_open_problems(graph) -> tuple[OpenProblem, ...]` and call it so `open_problems` becomes a view over active Blockers.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_world_model_v2.py
from src.envstate.world_model import (
    initial_map, merge_map, map_to_dict, map_from_dict, derive_open_problems,
    DependencyState, RecipeStep, RecipePatch, Fact,
)
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge

def _base():
    return initial_map(base_image="b", workdir="/r", language="python 3.11",
                       build_system="pip", repo_layout=("tests/",))

def test_new_fields_roundtrip():
    m = merge_map(_base(), dependency_state=DependencyState(declared=(Fact("cv2", ""),)),
                  import_results=(("cv2", False),), host_satisfied=frozenset({"contract:goal:repo_tests_pass"}))
    m2 = map_from_dict(map_to_dict(m))
    assert m2.dependency_state.declared[0].name == "cv2"
    assert m2.import_results == (("cv2", False),)
    assert "contract:goal:repo_tests_pass" in m2.host_satisfied

def test_recipe_patch_types():
    rp = RecipePatch(steps=(RecipeStep("s1", "system_install", "apt-get install -y libgl1",
                                       ("contract:system_library:libGL.so.1",)),))
    assert rp.steps[0].command.startswith("apt-get")

def test_derive_open_problems_from_active_blockers():
    g = ContractGraph(nodes=(
        Node("blocker:a", "Blocker", {"signature": "ImportError: libGL.so.1", "active": True,
                                      "summary": "libGL missing", "layer": "system"}),
        Node("blocker:gone", "Blocker", {"signature": "old", "active": False, "summary": "x", "layer": "deps"}),))
    ops = derive_open_problems(g)
    assert len(ops) == 1 and ops[0].layer == "system"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Edit `world_model.py`** — add the dataclasses (keystone), thread the 7 places (the map's `merge_map` does NOT raise on unknown kwargs because it only replaces named fields — add explicit kwargs for `dependency_state`, `import_results`, `host_satisfied`). Implement:

```python
def derive_open_problems(graph) -> tuple["OpenProblem", ...]:
    out = []
    for b in graph.blockers():
        if bool(b.data.get("active", True)):
            out.append(OpenProblem(signature=b.data.get("signature", ""),
                                   interpretation=b.data.get("summary", ""),
                                   layer=b.data.get("layer", "deps")))
    return tuple(out)
```

Update the Task 8 `refresh_host_graph` test's `contract_graph_host_satisfied` reference to `m.host_satisfied` now that the field exists, and have `refresh_host_graph` also set `open_problems=derive_open_problems(graph)` in its final `merge_map`.

- [ ] **Step 4: Run** both: `.venv/bin/python -m pytest tests/test_world_model_v2.py tests/test_refresh_host_graph.py -q` → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/world_model.py tests/test_world_model_v2.py
git commit -m "feat(envstate): DependencyState/RecipePatch types + derived open_problems + host_satisfied"
```

---

### Task 14: Probe fields — `import_sweep` + `dep_tree`

**Files:**
- Modify: `src/envstate/extractor.py`, `src/envstate/snapshot.py`, `src/envstate/world_model.py` (`apply_deterministic`)
- Test: `tests/test_probe_import_sweep.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_probe_import_sweep.py
from src.envstate.world_model import apply_deterministic, initial_map, Fact

def _base():
    return initial_map(base_image="b", workdir="/r", language="python 3.11",
                       build_system="pip", repo_layout=("tests/",), required=(Fact("opencv-python", ""),))

def test_apply_deterministic_folds_import_results():
    snap = type("S", (), {"env": {"python_version": "3.11"}, "installed": (Fact("opencv-python", ""),),
                          "system_installed": (), "import_results": (("cv2", False),)})()
    man = type("M", (), {"required": (Fact("opencv-python", ""),), "build_system": "pip"})()
    m = apply_deterministic(_base(), snap, man)
    assert m.import_results == (("cv2", False),)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3:**
  - `extractor.py`: add `EXTRACTOR_COMMANDS["dep_tree"] = "python -m pip inspect 2>/dev/null || true"` (POSIX-safe, `|| true` so `run_extractor` keeps it). The import sweep is NOT a static extractor command — it is built per-cycle from declared deps via `validators.build_import_sweep_command` and run through `exec_readonly`; ingest its result in `refresh_host_graph` or `apply_deterministic`.
  - `snapshot.py`: add `import_results` and `dep_tree` to `EnvSnapshot` (defaults `()` / `""`) and to `_SNAPSHOT_FIELDS` where probe-sourced.
  - `world_model.apply_deterministic`: copy `getattr(snap, "import_results", ())` into the map (guarded by the existing `snap.env` degrade check). Keep `import_results` OUT of `env` (gotcha: it would suppress the degrade guard).

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/extractor.py src/envstate/snapshot.py src/envstate/world_model.py tests/test_probe_import_sweep.py
git commit -m "feat(envstate): import_sweep + dep_tree probe fields folded deterministically"
```

---

## PHASE E — Render, Planner, Maintainer, BuildAgent

### Task 15: Rewrite `render.py` — three-section planner render + maintainer serializer

**Files:**
- Modify: `src/envstate/contracts/render.py`
- Test: `tests/test_contracts_render.py`

`render_graph_for_planner(graph, host_satisfied)` emits the three sections (spec §9.1): **Repair Map** (required goals + their projected statuses + active blockers + recent attempts/outcomes), **Repair Frontier** (`frontier_by_layer` + root blockers). `serialize_graph_for_maintainer(graph)` returns active Contract/Blocker/Attempt dicts (no status_events).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_render.py
from src.envstate.contracts.render import render_graph_for_planner, serialize_graph_for_maintainer
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge

def _g():
    return ContractGraph(
        nodes=(Node("contract:goal:repo_tests_pass", "Contract",
                    {"level": "goal", "required": True, "layer": "tests", "kind": "tests_pass"}),
               Node("contract:python_import:cv2", "Contract",
                    {"level": "atomic", "layer": "deps", "kind": "python_import", "subject": "cv2"}),
               Node("blocker:libgl", "Blocker", {"signature": "ImportError: libGL.so.1",
                    "active": True, "root_or_downstream": "root", "summary": "libGL missing", "layer": "system"})),
        edges=(Edge("contract:goal:repo_tests_pass", "depends_on", "contract:python_import:cv2"),
               Edge("blocker:libgl", "violates", "contract:python_import:cv2")))

def test_planner_render_has_three_sections_and_root_blocker():
    out = render_graph_for_planner(_g(), frozenset())
    assert "Repair Map" in out and "Repair Frontier" in out
    assert "blocker:libgl" in out and "violated" in out

def test_maintainer_serializer_has_no_status_events():
    d = serialize_graph_for_maintainer(_g())
    assert set(d) == {"contracts", "blockers", "attempts", "edges"}
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Rewrite `render.py`** — markdown builder iterating `graph.required_goal_contracts()`, `graph.contracts()` with `project_status(g, c.id, host_satisfied)`, `root_blockers(g)`, `graph.attempts()` (id — outcome — intent), and `frontier_by_layer(g, host_satisfied)`. `serialize_graph_for_maintainer` returns dicts keyed `contracts`/`blockers`/`attempts`/`edges` (active only). Reuse `node_to_dict`/`edge_to_dict`.

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/contracts/render.py tests/test_contracts_render.py
git commit -m "feat(contracts): three-section planner render + maintainer serializer"
```

---

### Task 16: `planner.py` — RecipePatch action

**Files:**
- Modify: `src/envstate/planner.py`
- Test: `tests/test_planner_recipe.py`

`parse_planner_decision` accepts `action="apply_recipe_patch"` with `recipe_patch.steps[]` (each `{id, kind, command, target_node_ids}`), returning `PlannerDecision(action="apply_recipe_patch", recipe_patch=RecipePatch(...))`. Each step with no `target_node_ids` is rejected (ungrounded). `done`/`giveup` unchanged. Rewrite the prompt's Contract-Graph + Output sections to the three-section + RecipePatch format (spec §9).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_planner_recipe.py
from src.envstate.planner import parse_planner_decision

def _json(body):  # planner expects a ```json fenced object
    return "```json\n" + body + "\n```"

def test_parses_recipe_patch():
    d = parse_planner_decision(_json('''
    {"action":"apply_recipe_patch","target_node_ids":["contract:system_library:libGL.so.1"],
     "recipe_patch":{"steps":[
       {"id":"s1","kind":"system_install","command":"apt-get install -y libgl1",
        "target_node_ids":["contract:system_library:libGL.so.1"]},
       {"id":"s2","kind":"validation","command":"python -c \\"import cv2\\"",
        "target_node_ids":["contract:python_import:cv2"]}]}}'''))
    assert d.action == "apply_recipe_patch"
    assert len(d.recipe_patch.steps) == 2 and d.recipe_patch.steps[0].command.startswith("apt-get")

def test_rejects_ungrounded_step():
    d = parse_planner_decision(_json('''
    {"action":"apply_recipe_patch","recipe_patch":{"steps":[
       {"id":"s1","kind":"system_install","command":"apt-get install -y libgl1","target_node_ids":[]}]}}'''))
    assert d is None

def test_done_and_giveup_unchanged():
    assert parse_planner_decision(_json('{"action":"giveup","reason":"no tests"}')).action == "giveup"
    assert parse_planner_decision(_json(
        '{"action":"done","satisfied_goal_contract_ids":["contract:goal:repo_tests_pass"]}')).action == "done"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Edit `planner.py`** — add `"apply_recipe_patch"` to `_VALID_ACTIONS`; in `parse_planner_decision` add a branch building `RecipeStep`/`RecipePatch` (reject if any step lacks `target_node_ids`); update `render_planning_view` to call `render_graph_for_planner(world_map.contract_graph, world_map.host_satisfied)`; rewrite the `## Contract Graph` + `## Output` prompt sections (spec §9.1/§9.2). Keep `task`/`done`/`giveup` parsing for back-compat but make `apply_recipe_patch` the documented work action.

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/planner.py tests/test_planner_recipe.py
git commit -m "feat(planner): apply_recipe_patch action + three-section render + RecipePatch parse"
```

---

### Task 17: `maintainer.py` — semantic patch keys + stop map writes

**Files:**
- Modify: `src/envstate/maintainer.py`
- Test: `tests/test_maintainer_graph_patch_v2.py`

`parse_v1_maintainer_reply` parses `graph_patch` via the new `parse_graph_patch`, validates with `scope="maintainer"` + `known_command_ids` (the cmd ids in the ledger/graph), applies on success, and on the map-write side **drops `open_problems`/`notes`** (the map's semantic fields) — keeping only the host-derived `done_flag`/`progress` writes (maintainer.py:584-591 and fallback 542-548). The prompt's graph-patch section (maintainer.py:337-363) is rewritten to the semantic keys (spec §7) and the prompt no longer asks for `open_problems`/`planner_notes`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_maintainer_graph_patch_v2.py
from src.envstate.maintainer import parse_v1_maintainer_reply
from src.envstate.world_model import initial_map, merge_map, Fact
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node

def _report(cmd="python -c 'import cv2'", rc=1, out="ImportError: libGL.so.1"):
    from src.envstate.world_model import TaskReport, CommandRecord
    return TaskReport(task_goal="g", status="blocked",
                      commands=(CommandRecord(cmd=cmd, rc=rc, output=out),), learning="blocked")

def _map():
    g = ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract", {"level": "atomic"}),))
    return merge_map(initial_map("b", "/r", "python 3.11", "pip", ("tests/",)), contract_graph=g)

def test_maintainer_does_not_write_open_problems():
    reply = '```json\n{"open_problems":[{"signature":"x","interpretation":"y","layer":"deps"}]}\n```'
    out = parse_v1_maintainer_reply(reply, _map(), _report())
    assert out.open_problems == ()    # semantic map writes are dropped; blockers live in the graph

def test_valid_graph_patch_applies_blocker():
    reply = ('```json\n{"graph_patch":{'
             '"add_blockers":[{"id":"blocker:libgl","type":"Blocker","signature":"ImportError: libGL.so.1",'
             '"active":true,"layer":"system","summary":"libGL","evidence_refs":[]}],'
             '"add_edges":[{"source":"blocker:libgl","type":"violates","target":"contract:python_import:cv2"}]}}\n```')
    errs = []
    out = parse_v1_maintainer_reply(reply, _map(), _report(), on_patch_error=errs.append)
    # evidence_refs empty -> grounded-blocker rule rejects; assert it was caught, map graph unchanged
    assert errs and not out.contract_graph.has_node("blocker:libgl")
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Edit `maintainer.py`** — swap to `parse_graph_patch`/`validate_patch(scope="maintainer", known_command_ids=...)`/`apply_patch`; compute `known_command_ids` from the ledger events folded this cycle; in the success `merge_map` (584-591) pass `contract_graph=` and `done_flag`/`progress` ONLY (drop `open_problems`/`notes`); same for the fallback (542-548). Rewrite the prompt graph-patch block (337-363) to the §7 keys and remove the `open_problems`/`planner_notes` instructions. Keep `_verified_test_run_passed` and the done-gate verbatim.

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/maintainer.py tests/test_maintainer_graph_patch_v2.py
git commit -m "feat(maintainer): semantic graph-patch keys; stop writing map's semantic fields"
```

---

### Task 18: `build_agent.py` — execute a whole recipe

**Files:**
- Modify: `src/envstate/build_agent.py`
- Test: `tests/test_build_agent_recipe.py`

Add `BuildAgent.run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0) -> TaskReport` that runs the ordered steps in the existing mini-ReAct style: seed the message with the full numbered recipe, run in order, local-repair within each step, stop-and-report on an unrepairable failure. Budget = `LOCAL_BUDGET_BASE + LOCAL_BUDGET_PER_STEP * len(recipe.steps)` (cap `RECIPE_BUDGET_CAP`). Keep the stuck-guard. The existing `run(task,...)` stays for back-compat.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_agent_recipe.py
from src.envstate.build_agent import BuildAgent, recipe_budget
from src.envstate.world_model import RecipePatch, RecipeStep
from src.envstate.ledger import ActionLedger

def test_recipe_budget_scales_with_steps():
    assert recipe_budget(1) < recipe_budget(5) <= recipe_budget(50)  # monotone, capped

def test_run_recipe_executes_steps_in_order_and_stops_on_failure():
    calls = []
    def sandbox_execute(cmd):
        calls.append(cmd)
        return (False, "E: boom") if "fail" in cmd else (True, "ok")
    rp = RecipePatch(steps=(
        RecipeStep("s1", "system_install", "apt-get install -y libgl1", ("contract:system_library:libGL.so.1",)),
        RecipeStep("s2", "system_install", "fail-here", ("contract:x",)),
        RecipeStep("s3", "validation", "python -c 'import cv2'", ("contract:python_import:cv2",))))
    ba = BuildAgent(client=_StubClient(), model="m", synthesizer=None, container_id="c")
    report = ba.run_recipe(rp, sandbox_execute, ActionLedger(), step_offset=0)
    assert "apt-get install -y libgl1" in calls          # step 1 ran
    assert report.status == "blocked"                    # stopped on s2
    assert "python -c 'import cv2'" not in calls          # s3 (after failure) did not run
```

(Define `_StubClient` inline using `types.SimpleNamespace` per the established stub pattern — its `chat.completions.create` returns a message whose content tells the agent to run the step command verbatim then `Final Answer: Success`.)

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Edit `build_agent.py`** — add `LOCAL_BUDGET_BASE=2`, `LOCAL_BUDGET_PER_STEP=2`, `RECIPE_BUDGET_CAP=16`, `recipe_budget(n)`; add `run_recipe`; add `BUILD_AGENT_RECIPE_PROMPT` (or extend the prompt) explaining: "execute each numbered step's command, repair only local errors, do not redesign; emit `Final Answer: Success` only when all steps are done or report blocked." Accumulate `CommandRecord`s across steps into one `TaskReport`.

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/build_agent.py tests/test_build_agent_recipe.py
git commit -m "feat(build_agent): run_recipe — whole-recipe execution, step-budget, stop-and-report"
```

---

## PHASE F — Orchestrator integration

### Task 19: `orchestrator.py` — drive RecipePatch per cycle

**Files:**
- Modify: `src/envstate/orchestrator.py`
- Test: `tests/test_orchestrator_recipe.py`

In the per-cycle body (orchestrator.py:131-167), when `enable_contract_graph` and `decision.action == "apply_recipe_patch"`: commit one Attempt per step (`attempts.commit_attempt`, `addresses` edges) BEFORE execution; run the whole recipe via `build_agent.run_recipe`; record one combined `TaskReport`; `_host_refresh` ONCE after the recipe (before maintainer); then `derive_attempt_outcome` for each committed Attempt from the re-projected `host_satisfied`; finally `Maintainer.update`; `_host_refresh` again; preserve the `done_flag` hard-stop (do NOT check mid-recipe). Use a monotonic global step counter for ledger offsets (gotcha: cycle-based offset aliases across cycles).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_recipe.py  (style: scripted queue fakes, per test_orchestrator_contract_graph.py)
from src.envstate.orchestrator import run_v1
from src.envstate.world_model import (initial_map, PlannerDecision, RecipePatch, RecipeStep,
                                       TaskReport, CommandRecord, Fact)
from src.envstate.snapshot import EnvSnapshot
from src.envstate.ledger import ActionLedger

class _Planner:
    def __init__(self, decisions): self._q = list(decisions)
    def decide(self, m): assert self._q; return self._q.pop(0)
class _BuildAgent:
    def __init__(self): self.recipes = []
    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        self.recipes.append(recipe)
        for s in recipe.steps: sandbox_execute(s.command)
        return TaskReport(task_goal="recipe", status="blocked",
                          commands=tuple(CommandRecord(s.command, 0, "ok") for s in recipe.steps),
                          learning="ran")
class _Maintainer:
    def update(self, m, report): return m

def test_run_v1_drives_recipe_and_commits_attempts():
    rp = RecipePatch(steps=(RecipeStep("s1", "system_install", "apt-get install -y libgl1",
                                       ("contract:system_library:libGL.so.1",)),))
    planner = _Planner([PlannerDecision(action="apply_recipe_patch", recipe_patch=rp),
                        PlannerDecision(action="giveup", reason="stop")])
    ba = _BuildAgent()
    m0 = initial_map("python:3.11", "/repo", "python 3.11", "pip", ("tests/",), required=(Fact("opencv-python",""),))
    reason = run_v1(planner, ba, _Maintainer(), m0, ActionLedger(),
                    sandbox_execute=lambda c: (True, "ok"), max_cycles=3,
                    probe=lambda: EnvSnapshot(installed=(), env={"python_version": "3.11"}),
                    manifest=type("M", (), {"required": (Fact("opencv-python", ""),), "build_system": "pip"})(),
                    exec_readonly=lambda c: (0, "{}"), enable_contract_graph=True)
    assert ba.recipes and ba.recipes[0].steps[0].command == "apt-get install -y libgl1"
    assert reason in ("planner_giveup", "max_cycles")
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Edit `orchestrator.py`** — add the `apply_recipe_patch` branch described above, calling `attempts.commit_attempt` + `_apply_patch`/`_validate_patch(scope="host")`, `build_agent.run_recipe`, `derive_attempt_outcome` (write outcomes back via a host `update_attempts` patch), then the existing maintainer + refresh + done-gate flow. Keep the legacy `task`/`transition_proposal` branch for back-compat behind `decision.action == "task"`. Replace the cycle-based step offset with a `global_step` counter incremented by `len(report.commands)`.

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_orchestrator_recipe.py
git commit -m "feat(orchestrator): drive multi-step RecipePatch + per-step Attempt outcomes"
```

---

### Task 20: Integration + coverage regression guard + full suite green

**Files:**
- Test: `tests/test_contract_graph_v2_integration.py`
- Modify: any remaining callers surfaced by the suite (e.g. `agent.py:1064-1077` if it imports removed symbols)

- [ ] **Step 1: Write the integration + regression tests**

```python
# tests/test_contract_graph_v2_integration.py
from src.envstate.contracts.projection import refresh_host_graph
from src.envstate.contracts.goals import GOAL_TESTS_PASS
from src.envstate.world_model import initial_map, merge_map, Fact
from src.envstate.ledger import ActionLedger, ActionEvent

def _led(evs):
    l = ActionLedger()
    for e in evs: l.append(e)
    return l

def test_libgl_fault_localizes_to_system_library():
    """cv2 import fails on libGL -> a system_library contract is promoted under the graph."""
    m = initial_map("python:3.11", "/repo", "python 3.11", "pip", ("tests/", "requirements.txt"),
                    required=(Fact("opencv-python", ""),))
    led = _led([ActionEvent(step=1, cmd="python -c 'import cv2'", rc=1,
                            stdout="ImportError: libGL.so.1: cannot open shared object file",
                            env_revision_before=0, env_revision_after=0, mutation_class=None)])
    m = refresh_host_graph(m, led, snapshot=None, exec_readonly=None, current_revision=0)
    g = m.contract_graph
    assert any(n.data.get("kind") == "system_library" for n in g.contracts())
    assert g.has_node(GOAL_TESTS_PASS)

def test_coverage_regression_guard_import_sweep_surfaces_missing_dep():
    """A dep imported only deep in tests is surfaced by the import sweep even with no failure signature yet."""
    m = merge_map(initial_map("python:3.11", "/repo", "python 3.11", "pip", ("tests/",),
                              required=(Fact("opencv-python", ""),)),
                  import_results=(("cv2", False),))
    from src.envstate.contracts.validators import host_satisfied_set
    from src.envstate.contracts.goals import seed_backbone
    from src.envstate.contracts.graph import ContractGraph
    nodes, edges = seed_backbone()
    g = ContractGraph(nodes=tuple(nodes), edges=tuple(edges))
    sat = host_satisfied_set(g, m, ledger_events=[])
    assert GOAL_TESTS_PASS not in sat   # cv2 not importable -> goal NOT satisfied (no false success)
```

- [ ] **Step 2: Run the FULL suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: investigate every failure. Likely remaining work: update `agent.py` (the `run_v1` caller) and delete/skip obsolete `v1g` tests that asserted the old 11-node schema (`test_contracts_*` legacy files, `test_orchestrator_contract_graph.py`, `test_maintainer_graph_patch.py`, `test_refresh_host_graph.py` old assertions). For each obsolete test: either port it to the new schema or delete it with a one-line commit explaining why.

- [ ] **Step 3: Make the suite green** — fix real regressions; port or delete obsolete-schema tests. Do NOT weaken the done-gate tests (they guard against false success — keep them).

- [ ] **Step 4: Run full suite again** → PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test(contracts): v2 integration + coverage regression guard; port/remove obsolete v1g tests"
```

---
