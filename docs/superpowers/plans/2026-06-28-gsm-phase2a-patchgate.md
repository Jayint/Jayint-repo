# GSM Phase 2a — PatchProposal + Deterministic PatchGate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the typed `PatchProposal` model and the deterministic `PatchGate` (parse → validate → apply → recompose script) on the dep-graph/block model, as standalone, separately-tested, pure modules — the v3 replacement for the LLM Maintainer, ready for 2b to wire in.

**Architecture:** Greenfield pure modules under `src/python_deps/depgraph/` on top of the merged Phase-1 engine (`block`, `script`, `schema`, `ids`, `schedule`, `emit`). The LLM emits a typed `PatchProposal` (§9 shape); `PatchGate.validate_proposal` returns an error list (the §10 checks); `apply_proposal` is a pure immutable reducer that never writes `SATISFIED`; `compose_script` re-derives the artifact as `compile_blocks(graph)` ∪ governed manual blocks (the recompile-after-mutation entry point). Touches neither `run_v1` nor `run_v3`; no Docker, no network, no LLM in the unit tests.

**Tech Stack:** Python 3 (`python3`), `pytest`, frozen `@dataclass` immutables, the existing `src/python_deps/depgraph/` engine.

**Source design:** `docs/superpowers/specs/2026-06-28-gsm-phase2a-patchgate-design.md` (the master spec's §9, §10, §16, §18).

## Global Constraints

- **No behaviour change to v1 or v3.** Phase 2a adds new modules only; the one edit to existing code (`schedule._dependencies_satisfied`, Task 6) is behaviour-preserving because every current edge is hard (`data.get("hard", True)` defaults to hard). The full suite must stay green except the 4 known pre-existing failures (`test_adapter_logic` nested_pytester, `test_repo2run_dataset` ×2, `test_runtime_pin_seam` floor-trap).
- **State authority (invariants #3/#4):** `apply_proposal` MUST NOT write `State.SATISFIED`; new nodes land `State.MISSING`. `NodeSpec` has NO `state` field — the model is structurally incapable of certifying. Tests assert this.
- **State enum unchanged:** do NOT add values to `State` (`{UNKNOWN, MISSING, SATISFIED}`). Hint/Candidate is `Node.data["promotion"]` (`"hint"`/`"candidate"`) + `Edge.data["hard"]`, never a `state`.
- **Immutability:** every new dataclass is `@dataclass(frozen=True)`; `apply_proposal` returns a NEW `DepGraph` (the input is untouched); `validate_proposal` is pure (mutates nothing).
- **script_patches are governed, never authoritative (the keep-script_patches decision):** an accepted `ScriptPatch` must target a real graph node, cite evidence, and pass read-only-check validation; it becomes a `Block` in `ApplyResult.blocks` and NEVER mutates node state. The persisted script is always `compose_script(graph, blocks)`.
- **Action class is provider-scoped:** `matches_action_class` constrains `ProviderSpec` commands only. `ScriptPatch` commands are the deliberate escape hatch (shell-class by nature) and are NOT action-class-constrained.
- **File size:** each new file < 400 lines; pure modules carry no Docker/network/LLM imports.
- **Naming:** node-id namespace for the patch model is `patch.py` (no collision — `contracts/patch.py` is a different package). The gate is `patch_gate.py`.
- **Reuse, don't reimplement:** `block.Block`/`compile_blocks`, `script.render_setup_sh`/`parse_setup_sh`, `schema.{Node,Edge,EdgeType,NodeType,Layer,State,DiscoveredBy,DepGraph,EDGE_RULES}`, `ids.*`, `schedule.{_dependencies_satisfied,scheduler_frontier}`.
- **Git hygiene:** `git add` only the exact files each task creates/modifies — NEVER `git add -A`/`.`/`<dir>` (the repo has unrelated untracked WIP). Conventional commit messages with an Observation/Why/What/Verification body. **No `Co-Authored-By` trailer.** Do not push.

### Verified reuse signatures (use these exactly)

```python
# src/python_deps/depgraph/schema.py  — all VERIFIED 2026-06-28 against the live tree
@dataclass(frozen=True)
class Node:
    id: str; type: NodeType; name: str; layer: Layer; discovered_by: DiscoveredBy   # REQUIRED (no default)
    tier: int = 0; state: State = State.UNKNOWN; version: str | None = None
    check_command: str | None = None; evidence: str | None = None
    fix_candidates: tuple[str,...] = (); chosen_fix: str | None = None
    # ... attempts/provenance/... ; data: dict = {}   (wrapped read-only at construction)
@dataclass(frozen=True)
class Edge:
    src: str; dst: str; relation: EdgeType = EdgeType.REQUIRES
    marker: str | None = None; data: dict = {}        # data wrapped read-only
class NodeType(Enum):  TEST PROJECT IMPORT PACKAGE SYSTEM_LIB TOOL RUNTIME PLATFORM SERVICE CONFIG DATA_ASSET
class EdgeType(Enum):  REQUIRES="requires"  ALTERNATIVE_TO="alternative_to"  CONFLICTS_WITH="conflicts_with"
class State(Enum):     UNKNOWN MISSING SATISFIED
class Layer(Enum):     INTERPRETER SYSTEM TOOLCHAIN PIP NAMING RUNTIME TESTS CONFIG SERVICES
class DiscoveredBy(Enum): GOAL STATIC_SCAN RESOLVER PROBE RUNTIME
EDGE_RULES: dict[str, tuple[frozenset[str], frozenset[str]]]   # relation.value -> (allowed src .type.value, allowed dst .type.value)
#   "requires": ({Test,Project,Import,Package,Service,Config}, {Project,Import,Package,SystemLib,Tool,Runtime,Platform,Service,Config,DataAsset})
class DepGraph:
    nodes: tuple[Node,...] = (); edges: tuple[Edge,...] = ()
    def get(self, node_id) -> Node | None
    def with_node(self, node) -> DepGraph        # add or REPLACE by id
    def with_edge(self, edge) -> DepGraph         # VALIDATES EDGE_RULES -> RAISES ValueError on illegal/dangling; dedupes by (src,dst,relation)

# src/python_deps/depgraph/ids.py
def package_id(name, version) -> str   # "pkg:name" or "pkg:name==ver"
def syslib_id(soname) -> str           # "syslib:soname"
def tool_id(tool) -> str               # "tool:tool"
def config_id(name) -> str             # "config:name"
def service_id(name) -> str            # "service:name"
def runtime_id(minor) -> str; def import_id(name) -> str; def project_id(name) -> str

# src/python_deps/depgraph/block.py (Phase 1)
@dataclass(frozen=True)
class Block:
    block_id:str; wave:str; commands:tuple[str,...]; target_node_ids:tuple[str,...]
    provider_ids:tuple[str,...]=(); check_commands:tuple[str,...]=(); evidence_refs:tuple[str,...]=()
    mutates_env:bool=True; can_batch:bool=False
def compile_blocks(graph: DepGraph) -> tuple[Block, ...]      # one block per emittable node, topo order

# src/python_deps/depgraph/script.py (Phase 1)
def render_setup_sh(blocks) -> str; def parse_setup_sh(text) -> tuple[Block, ...]

# src/python_deps/depgraph/schedule.py
def _dependencies_satisfied(graph, node) -> bool   # the soft/hard-edge seam (Task 6)
```

Because `with_edge` **raises** `ValueError` on an illegal/dangling edge, `validate_proposal` must catch every edge problem itself (returning an error string) so a validated proposal never makes `apply_proposal` raise. And `apply_proposal` must add nodes BEFORE edges so the edge endpoints exist.

---

### Task 1: `action_class.py` — provider action-class taxonomy

**Files:**
- Create: `src/python_deps/depgraph/action_class.py`
- Test: `tests/depgraph/test_action_class.py`

**Interfaces:**
- Produces: `ACTION_CLASSES: dict[str, str]` and `matches_action_class(kind: str, command: str) -> bool`. Consumed by `validate_proposal` (Task 3).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_action_class.py
from python_deps.depgraph.action_class import matches_action_class, ACTION_CLASSES


def test_apt_class():
    assert matches_action_class("apt", "apt-get install -y --no-install-recommends libpq-dev")
    assert matches_action_class("apt", "apt-get update && apt-get install -y libpq-dev")
    # §14 "wrong action class" case: kind=apt but command is not apt-get install
    assert not matches_action_class("apt", "pip install psycopg2")


def test_pip_class():
    assert matches_action_class("pip", "pip install psycopg2==2.9.9")
    assert matches_action_class("pip", "python3 -m pip install --break-system-packages psycopg2")
    assert not matches_action_class("pip", "apt-get install python3-psycopg2")


def test_npm_class():
    assert matches_action_class("npm", "npm install")
    assert matches_action_class("npm", "npm ci")
    assert not matches_action_class("npm", "yarn add foo")


def test_shell_is_explicit_escape_hatch():
    assert matches_action_class("shell", "make && make install")
    assert "shell" in ACTION_CLASSES


def test_unknown_kind_rejected_and_empty_rejected():
    assert not matches_action_class("brew", "brew install foo")
    assert not matches_action_class("shell", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_action_class.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.action_class'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/action_class.py
"""Provider action-class taxonomy (design §10): a provider command must match the
shell action class declared by its `kind`. Pure: no Docker/network/LLM."""
from __future__ import annotations

import re

# kind -> regex the provider command must match (searched against the stripped command).
ACTION_CLASSES: dict[str, str] = {
    "apt":   r"^apt-get(\s+update\s*&&\s*apt-get)?\s+install\b",
    "pip":   r"^(python3?\s+-m\s+)?pip\s+install\b",
    "npm":   r"^npm\s+(install|ci)\b",
    "shell": r".",   # explicit, audited escape hatch: matches any non-empty command
}


def matches_action_class(kind: str, command: str) -> bool:
    pattern = ACTION_CLASSES.get(kind)
    if pattern is None:
        return False
    return re.search(pattern, (command or "").strip()) is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_action_class.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/action_class.py tests/depgraph/test_action_class.py
git commit -m "feat(depgraph): provider action-class taxonomy (matches_action_class)"
```

---

### Task 2: `patch.py` — PatchProposal model + tolerant parser

**Files:**
- Create: `src/python_deps/depgraph/patch.py`
- Test: `tests/depgraph/test_patch_parse.py`

**Interfaces:**
- Produces: frozen `NodeSpec`, `ProviderSpec`, `EdgeSpec`, `ScriptPatch`, `PatchProposal` (with `is_empty()`), and `parse_patch_proposal(d: dict) -> PatchProposal`. Consumed by Tasks 3–5. The parser does NO validation (that is the gate's job) — it only shapes the §9 dict into the typed model, accepting both the `{"rationale":..., "patch":{...}}` envelope and a bare patch dict, and both `commands` (list) and `command` (singular) on a script patch.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_patch_parse.py
from python_deps.depgraph.patch import (
    parse_patch_proposal, PatchProposal, NodeSpec, ProviderSpec, EdgeSpec, ScriptPatch,
)

# The master spec §9 example (id values are illustrative; the gate validates canonicality).
_SPEC9 = {
    "rationale": {"failure": "meson cannot find libplacebo", "hypothesis": "missing -dev"},
    "patch": {
        "add_requirements": [{
            "id": "syslib:libplacebo.pc", "type": "SystemLib", "name": "libplacebo.pc",
            "layer": "system", "check_command": "pkg-config --exists libplacebo",
            "evidence_ref": "ev:block:meson_setup:stderr",
        }],
        "add_providers": [{
            "id": "apt:libplacebo-dev", "kind": "apt",
            "command": "apt-get install -y --no-install-recommends libplacebo-dev",
            "provides": ["syslib:libplacebo.pc"],
        }],
        "add_edges": [{
            "source": "test:repo_tests_pass", "relation": "requires", "target": "syslib:libplacebo.pc",
        }],
        "script_patches": [{
            "op": "add_block", "block_id": "system.libplacebo", "wave": "system",
            "command": "apt-get update && apt-get install -y --no-install-recommends libplacebo-dev",
            "target_node_ids": ["syslib:libplacebo.pc"], "checks": ["pkg-config --exists libplacebo"],
        }],
        "request_checks": ["syslib:libplacebo.pc"],
    },
}


def test_parses_spec9_example():
    p = parse_patch_proposal(_SPEC9)
    assert isinstance(p, PatchProposal) and not p.is_empty()
    assert p.add_requirements[0] == NodeSpec(
        id="syslib:libplacebo.pc", type="SystemLib", name="libplacebo.pc", layer="system",
        check_command="pkg-config --exists libplacebo", evidence_ref="ev:block:meson_setup:stderr")
    assert p.add_providers[0].kind == "apt" and p.add_providers[0].provides == ("syslib:libplacebo.pc",)
    assert p.add_edges[0] == EdgeSpec(source="test:repo_tests_pass", target="syslib:libplacebo.pc")
    # singular "command" is normalised to the commands tuple
    assert p.script_patches[0].commands == (
        "apt-get update && apt-get install -y --no-install-recommends libplacebo-dev",)
    assert p.request_checks == ("syslib:libplacebo.pc",)


def test_empty_and_defaults():
    p = parse_patch_proposal({})
    assert p.is_empty()
    assert p.add_requirements == () and p.add_edges == () and p.request_checks == ()


def test_unknown_keys_ignored_and_state_maps_to_promotion():
    p = parse_patch_proposal({"patch": {
        "bogus": 123,
        "add_requirements": [{"id": "config:DATABASE_URL", "type": "Config",
                              "name": "DATABASE_URL", "layer": "config", "state": "HINT"}],
    }})
    assert p.add_requirements[0].promotion == "HINT"   # raw value carried; gate normalises/validates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_patch_parse.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.patch'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/patch.py
"""Typed PatchProposal model + tolerant parser (design §9). Pure: no Docker/network/LLM.

The model is the v3 LLM contract (invariant #6): the only accepted state change is a
PatchProposal. NodeSpec deliberately has NO `state` field — the model cannot certify."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: str            # NodeType value, e.g. "SystemLib"
    name: str
    layer: str           # Layer value, e.g. "system"
    check_command: str | None = None
    evidence_ref: str | None = None
    promotion: str | None = None   # "hint" | "candidate" | None (gate validates)


@dataclass(frozen=True)
class ProviderSpec:
    id: str              # e.g. "apt:libplacebo-dev"
    kind: str            # action class, e.g. "apt" | "pip" | "npm" | "shell"
    command: str
    provides: tuple[str, ...] = ()


@dataclass(frozen=True)
class EdgeSpec:
    source: str
    target: str
    relation: str = "requires"
    hard: bool = True


@dataclass(frozen=True)
class ScriptPatch:
    block_id: str
    wave: str
    commands: tuple[str, ...]
    target_node_ids: tuple[str, ...]
    op: str = "add_block"
    checks: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    evidence_ref: str | None = None


@dataclass(frozen=True)
class PatchProposal:
    rationale: dict = field(default_factory=dict)   # advisory only
    add_requirements: tuple[NodeSpec, ...] = ()
    add_providers: tuple[ProviderSpec, ...] = ()
    add_edges: tuple[EdgeSpec, ...] = ()
    script_patches: tuple[ScriptPatch, ...] = ()
    request_checks: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.add_requirements or self.add_providers or self.add_edges
                    or self.script_patches or self.request_checks)


def _as_tuple(x) -> tuple:
    return tuple(x) if isinstance(x, (list, tuple)) else ()


def parse_patch_proposal(d: dict) -> PatchProposal:
    d = d or {}
    patch = d.get("patch", d)
    rationale = d.get("rationale", {})
    if not isinstance(rationale, dict):
        rationale = {}
    reqs = tuple(NodeSpec(
        id=r["id"], type=r["type"], name=r.get("name", ""), layer=r["layer"],
        check_command=r.get("check_command"), evidence_ref=r.get("evidence_ref"),
        promotion=r.get("promotion") if r.get("promotion") is not None else r.get("state"),
    ) for r in _as_tuple(patch.get("add_requirements")))
    provs = tuple(ProviderSpec(
        id=p["id"], kind=p["kind"], command=p["command"], provides=_as_tuple(p.get("provides")),
    ) for p in _as_tuple(patch.get("add_providers")))
    edges = tuple(EdgeSpec(
        source=e["source"], target=e["target"],
        relation=e.get("relation", "requires"), hard=bool(e.get("hard", True)),
    ) for e in _as_tuple(patch.get("add_edges")))
    sps = tuple(ScriptPatch(
        block_id=s["block_id"], wave=s["wave"],
        commands=_as_tuple(s.get("commands")) or ((s["command"],) if s.get("command") else ()),
        target_node_ids=_as_tuple(s.get("target_node_ids")),
        op=s.get("op", "add_block"), checks=_as_tuple(s.get("checks")),
        provides=_as_tuple(s.get("provides")), evidence_ref=s.get("evidence_ref"),
    ) for s in _as_tuple(patch.get("script_patches")))
    return PatchProposal(
        rationale=rationale, add_requirements=reqs, add_providers=provs, add_edges=edges,
        script_patches=sps, request_checks=_as_tuple(patch.get("request_checks")),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_patch_parse.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/patch.py tests/depgraph/test_patch_parse.py
git commit -m "feat(depgraph): typed PatchProposal model + tolerant §9 parser"
```

---

### Task 3: `patch_gate.py` — `validate_proposal` (the §10 checks)

**Files:**
- Create: `src/python_deps/depgraph/patch_gate.py`
- Test: `tests/depgraph/test_patch_gate_validate.py`

**Interfaces:**
- Consumes: `patch.*`, `action_class.matches_action_class`, `schema.{DepGraph,NodeType,Layer,EdgeType,EDGE_RULES}`.
- Produces: `validate_proposal(graph: DepGraph, proposal: PatchProposal, *, known_evidence_ids: frozenset[str]) -> list[str]` — empty list = accept. Pure (mutates nothing). Catches every problem `apply_proposal` would otherwise raise on (illegal/dangling edge), plus the §10 permission/evidence/canonicality/dedupe/read-only/action-class checks.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_patch_gate_validate.py
from python_deps.depgraph.patch import (
    PatchProposal, NodeSpec, ProviderSpec, EdgeSpec, ScriptPatch,
)
from python_deps.depgraph.patch_gate import validate_proposal
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)

_EV = frozenset({"ev1", "ev2"})


def _good():
    return PatchProposal(
        add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib", name="libpq.so",
                                   layer="system", check_command="ldconfig -p | grep -q libpq",
                                   evidence_ref="ev1"),),
        add_providers=(ProviderSpec(id="apt:libpq-dev", kind="apt",
                                    command="apt-get install -y --no-install-recommends libpq-dev",
                                    provides=("syslib:libpq.so",)),),
        add_edges=(EdgeSpec(source="test:repo_tests_pass", target="syslib:libpq.so"),),
        request_checks=("syslib:libpq.so",),
    )


def _graph_with_test_node():
    return DepGraph().with_node(Node(id="test:repo_tests_pass", type=NodeType.TEST,
        name="tests", layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.MISSING))


def test_accepts_well_formed_proposal():
    assert validate_proposal(_graph_with_test_node(), _good(), known_evidence_ids=_EV) == []


def test_rejects_satisfied_or_bad_promotion():
    p = PatchProposal(add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib",
        name="libpq.so", layer="system", check_command="ldconfig -p", evidence_ref="ev1",
        promotion="SATISFIED"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("promotion" in e.lower() for e in errs)


def test_rejects_non_canonical_node_id():
    p = PatchProposal(add_requirements=(NodeSpec(id="pkgconfig:libpq", type="SystemLib",
        name="libpq", layer="system", check_command="ldconfig -p", evidence_ref="ev1"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("canonical" in e.lower() or "prefix" in e.lower() for e in errs)


def test_rejects_missing_evidence():
    p = PatchProposal(add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib",
        name="libpq.so", layer="system", check_command="ldconfig -p", evidence_ref="nope"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("evidence" in e.lower() for e in errs)


def test_rejects_dangling_script_target():
    p = PatchProposal(script_patches=(ScriptPatch(block_id="system.x", wave="system",
        commands=("apt-get install -y libpq-dev",), target_node_ids=("syslib:ghost",),
        evidence_ref="ev1"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("ghost" in e or "target" in e.lower() for e in errs)


def test_rejects_mutating_check_command():
    p = PatchProposal(add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib",
        name="libpq.so", layer="system", check_command="apt-get install -y libpq-dev",
        evidence_ref="ev1"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("read-only" in e.lower() or "mutating" in e.lower() for e in errs)


def test_rejects_action_class_mismatch():
    p = PatchProposal(add_providers=(ProviderSpec(id="apt:libpq-dev", kind="apt",
        command="pip install psycopg2", provides=()),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("action class" in e.lower() for e in errs)


def test_rejects_duplicate_ids_within_proposal():
    n = NodeSpec(id="syslib:libpq.so", type="SystemLib", name="libpq.so", layer="system",
                 check_command="ldconfig -p", evidence_ref="ev1")
    p = PatchProposal(add_requirements=(n, n))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("duplicate" in e.lower() for e in errs)


def test_rejects_illegal_edge_relation_types():
    # requires-edge dst SystemLib is legal; src SystemLib is NOT in EDGE_RULES allowed src.
    g = _graph_with_test_node().with_node(Node(id="syslib:a.so", type=NodeType.SYSTEM_LIB,
        name="a.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING))
    p = PatchProposal(add_edges=(EdgeSpec(source="syslib:a.so", target="test:repo_tests_pass"),))
    errs = validate_proposal(g, p, known_evidence_ids=_EV)
    assert any("edge" in e.lower() or "source type" in e.lower() for e in errs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_patch_gate_validate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.patch_gate'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/patch_gate.py
"""Deterministic PatchGate (design §10): validate -> apply -> recompose.

The v3 replacement for the LLM Maintainer. validate_proposal returns an error list
(empty = accept); apply_proposal is a pure immutable reducer that NEVER writes
SATISFIED; compose_script re-derives the artifact from the graph plus governed
manual blocks. Pure: no Docker/network/LLM."""
from __future__ import annotations

import re

from python_deps.depgraph.action_class import matches_action_class
from python_deps.depgraph.patch import (
    PatchProposal, NodeSpec, ProviderSpec, EdgeSpec, ScriptPatch,
)
from python_deps.depgraph.schema import (
    DepGraph, NodeType, Layer, EdgeType, EDGE_RULES,
)

# Node-type -> canonical id prefix (ids.py).  Types not listed accept any "<kind>:<rest>".
_KIND_PREFIX: dict[NodeType, str] = {
    NodeType.PACKAGE: "pkg:", NodeType.SYSTEM_LIB: "syslib:", NodeType.TOOL: "tool:",
    NodeType.CONFIG: "config:", NodeType.SERVICE: "service:", NodeType.RUNTIME: "runtime:",
    NodeType.IMPORT: "import:", NodeType.PROJECT: "project:",
}
_ALLOWED_PROMOTION = frozenset({"hint", "candidate"})
_MUTATING = re.compile(
    r"(\bapt-get\s+install\b|\bpip\s+install\b|\bnpm\s+(install|ci)\b|\brm\s|\bmkdir\s|>>|>)")


def _node_type(value: str) -> NodeType | None:
    try:
        return NodeType(value)
    except ValueError:
        return None


def validate_proposal(graph: DepGraph, proposal: PatchProposal, *,
                      known_evidence_ids: frozenset[str]) -> list[str]:
    errs: list[str] = []
    existing_ids = {n.id for n in graph.nodes}
    proposed_node_ids = {r.id for r in proposal.add_requirements}

    # within-proposal duplicate ids (nodes / providers / script blocks)
    for label, ids in (("add_requirements", [r.id for r in proposal.add_requirements]),
                       ("add_providers", [p.id for p in proposal.add_providers]),
                       ("script_patches", [s.block_id for s in proposal.script_patches])):
        if len(ids) != len(set(ids)):
            errs.append(f"duplicate id within {label}")

    for r in proposal.add_requirements:
        nt = _node_type(r.type)
        if nt is None:
            errs.append(f"unknown node type {r.type!r} for {r.id}"); continue
        try:
            Layer(r.layer)
        except ValueError:
            errs.append(f"unknown layer {r.layer!r} for {r.id}")
        prefix = _KIND_PREFIX.get(nt)
        if prefix is not None and not r.id.startswith(prefix):
            errs.append(f"non-canonical id {r.id!r}: {nt.value} requires prefix {prefix!r}")
        elif ":" not in r.id:
            errs.append(f"non-canonical id {r.id!r}: missing '<kind>:' prefix")
        if r.promotion is not None and r.promotion not in _ALLOWED_PROMOTION:
            errs.append(f"illegal promotion {r.promotion!r} for {r.id} "
                        f"(only {sorted(_ALLOWED_PROMOTION)} or none; SATISFIED is host-only)")
        if not r.evidence_ref or r.evidence_ref not in known_evidence_ids:
            errs.append(f"requirement {r.id} cites unknown/absent evidence {r.evidence_ref!r}")
        if r.check_command and _MUTATING.search(r.check_command):
            errs.append(f"check command for {r.id} is not read-only: {r.check_command!r}")
        # conflicting redefinition vs graph
        cur = graph.get(r.id)
        if cur is not None and (cur.type.value != r.type or cur.layer.value != r.layer
                                or (cur.check_command or None) != (r.check_command or None)):
            errs.append(f"conflicting redefinition of existing node {r.id}")

    for p in proposal.add_providers:
        if not matches_action_class(p.kind, p.command):
            errs.append(f"provider {p.id} command does not match action class "
                        f"{p.kind!r}: {p.command!r}")

    known_after = existing_ids | proposed_node_ids
    for s in proposal.script_patches:
        if not s.evidence_ref or s.evidence_ref not in known_evidence_ids:
            errs.append(f"script block {s.block_id} cites unknown/absent evidence {s.evidence_ref!r}")
        for nid in s.target_node_ids:
            if nid not in known_after:
                errs.append(f"script block {s.block_id} targets unknown node {nid!r}")
        for chk in s.checks:
            if _MUTATING.search(chk):
                errs.append(f"script block {s.block_id} check is not read-only: {chk!r}")

    # edges: replicate EDGE_RULES against the post-add_requirements view (with_edge would RAISE).
    type_of = {n.id: n.type.value for n in graph.nodes}
    type_of.update({r.id: r.type for r in proposal.add_requirements})
    for e in proposal.add_edges:
        try:
            EdgeType(e.relation)
        except ValueError:
            errs.append(f"unknown edge relation {e.relation!r}"); continue
        rule = EDGE_RULES.get(e.relation)
        if e.source not in type_of or e.target not in type_of:
            errs.append(f"edge {e.relation} references unknown node(s): {e.source!r} -> {e.target!r}")
            continue
        if rule is not None:
            allowed_src, allowed_dst = rule
            if type_of[e.source] not in allowed_src:
                errs.append(f"illegal {e.relation} source type {type_of[e.source]!r} ({e.source!r})")
            if type_of[e.target] not in allowed_dst:
                errs.append(f"illegal {e.relation} destination type {type_of[e.target]!r} ({e.target!r})")

    return errs
```

> **Implementer note:** the `_good()` proposal's edge is `test:repo_tests_pass -(requires)-> syslib:libpq.so`; `Test` is an allowed `requires` source and `SystemLib` an allowed destination per `EDGE_RULES`, so it passes. The graph fixture must contain the `test:` node so the edge source resolves; `syslib:libpq.so` resolves via `add_requirements`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_patch_gate_validate.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/patch_gate.py tests/depgraph/test_patch_gate_validate.py
git commit -m "feat(depgraph): PatchGate.validate_proposal (§10 checks, returns error list)"
```

---

### Task 4: `patch_gate.py` — `apply_proposal` + `ApplyResult`

**Files:**
- Modify: `src/python_deps/depgraph/patch_gate.py` (append `ApplyResult`, `apply_proposal`, helpers)
- Test: `tests/depgraph/test_patch_gate_apply.py`

**Interfaces:**
- Consumes: `patch.*`, `block.Block`, `schema.{Node,Edge,EdgeType,NodeType,Layer,State,DiscoveredBy,DepGraph}`.
- Produces: `@dataclass(frozen=True) ApplyResult(graph: DepGraph, blocks: tuple[Block, ...])` and `apply_proposal(graph: DepGraph, proposal: PatchProposal) -> ApplyResult`. Pure/immutable; adds nodes BEFORE edges; binds providers to `chosen_fix`; converts `script_patches` to governed `Block`s; NEVER writes `State.SATISFIED`. Assumes the proposal already passed `validate_proposal` (re-asserts the no-SATISFIED guard structurally).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_patch_gate_apply.py
from dataclasses import FrozenInstanceError

from python_deps.depgraph.patch import (
    PatchProposal, NodeSpec, ProviderSpec, EdgeSpec, ScriptPatch,
)
from python_deps.depgraph.patch_gate import apply_proposal, ApplyResult
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy, EdgeType,
)


def _base():
    return DepGraph().with_node(Node(id="test:repo_tests_pass", type=NodeType.TEST,
        name="tests", layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.MISSING))


def _proposal():
    return PatchProposal(
        add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib", name="libpq.so",
            layer="system", check_command="ldconfig -p | grep -q libpq", evidence_ref="ev1",
            promotion="candidate"),),
        add_providers=(ProviderSpec(id="apt:libpq-dev", kind="apt",
            command="apt-get install -y --no-install-recommends libpq-dev",
            provides=("syslib:libpq.so",)),),
        add_edges=(EdgeSpec(source="test:repo_tests_pass", target="syslib:libpq.so", hard=False),),
        script_patches=(ScriptPatch(block_id="system.libpq", wave="system",
            commands=("apt-get update && apt-get install -y libpq-dev",),
            target_node_ids=("syslib:libpq.so",), checks=("ldconfig -p | grep -q libpq",),
            evidence_ref="ev1"),),
    )


def test_apply_is_immutable():
    g = _base()
    before = (g.nodes, g.edges)
    apply_proposal(g, _proposal())
    assert (g.nodes, g.edges) == before          # input untouched


def test_node_added_missing_with_promotion_and_never_satisfied():
    res = apply_proposal(_base(), _proposal())
    node = res.graph.get("syslib:libpq.so")
    assert node is not None
    assert node.state is State.MISSING           # invariant #3/#4
    assert node.data.get("promotion") == "candidate"
    assert node.check_command == "ldconfig -p | grep -q libpq"


def test_provider_binds_chosen_fix():
    res = apply_proposal(_base(), _proposal())
    assert res.graph.get("syslib:libpq.so").chosen_fix == "apt:libpq-dev"


def test_soft_edge_carries_hard_false():
    res = apply_proposal(_base(), _proposal())
    edge = next(e for e in res.graph.edges if e.dst == "syslib:libpq.so")
    assert edge.relation is EdgeType.REQUIRES and edge.data.get("hard") is False


def test_script_patch_becomes_governed_block_not_state():
    res = apply_proposal(_base(), _proposal())
    assert len(res.blocks) == 1
    b = res.blocks[0]
    assert b.block_id == "system.libpq" and b.target_node_ids == ("syslib:libpq.so",)
    assert b.check_commands == ("ldconfig -p | grep -q libpq",)
    # the block never certified anything: target node is still MISSING
    assert res.graph.get("syslib:libpq.so").state is State.MISSING


def test_adversarial_apply_never_satisfied():
    # even a fully populated proposal yields no SATISFIED node
    res = apply_proposal(_base(), _proposal())
    assert all(n.state is not State.SATISFIED for n in res.graph.nodes if n.id != "test:repo_tests_pass")
    assert _base().get("test:repo_tests_pass").state is State.MISSING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_patch_gate_apply.py -q`
Expected: FAIL with `ImportError: cannot import name 'apply_proposal'` (or `ApplyResult`).

- [ ] **Step 3: Write minimal implementation (append to `patch_gate.py`)**

```python
# append to src/python_deps/depgraph/patch_gate.py
from dataclasses import dataclass, replace

from python_deps.depgraph.block import Block
from python_deps.depgraph.schema import (
    Node, Edge, State, DiscoveredBy,
)


@dataclass(frozen=True)
class ApplyResult:
    graph: DepGraph
    blocks: tuple[Block, ...]


def _provider_fix(p: ProviderSpec) -> str:
    # apt providers store the "apt:NAME" form (emit._apt_name strips the prefix);
    # everything else stores the literal command (compile_blocks' fallback emits it,
    # and for PACKAGE nodes compile_blocks derives the pip command from name/version).
    return p.id if p.id.startswith("apt:") else p.command


def _script_patch_to_block(s: ScriptPatch) -> Block:
    return Block(
        block_id=s.block_id, wave=s.wave, commands=s.commands,
        target_node_ids=s.target_node_ids, provider_ids=s.provides,
        check_commands=s.checks,
        evidence_refs=(s.evidence_ref,) if s.evidence_ref else (),
    )


def apply_proposal(graph: DepGraph, proposal: PatchProposal) -> ApplyResult:
    g = graph
    # 1. requirement nodes — always MISSING (never SATISFIED), promotion tag if present.
    for r in proposal.add_requirements:
        if g.get(r.id) is not None:
            continue                                    # dedup no-op (validate ensured non-conflicting)
        data = {"promotion": r.promotion} if r.promotion else {}
        g = g.with_node(Node(
            id=r.id, type=NodeType(r.type), name=r.name or r.id.split(":", 1)[-1],
            layer=Layer(r.layer), discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
            check_command=r.check_command, evidence=r.evidence_ref, data=data,
        ))
    # 2. providers -> chosen_fix on each provided node (first writer wins).
    for p in proposal.add_providers:
        fix = _provider_fix(p)
        for nid in p.provides:
            node = g.get(nid)
            if node is not None and node.chosen_fix is None:
                g = g.with_node(replace(node, chosen_fix=fix))
    # 3. edges — endpoints now exist (validate guaranteed legality; with_edge dedupes).
    for e in proposal.add_edges:
        g = g.with_edge(Edge(src=e.source, dst=e.target, relation=EdgeType(e.relation),
                             data={"hard": e.hard}))
    # 4. script_patches -> governed blocks; they NEVER mutate node state.
    blocks = tuple(_script_patch_to_block(s) for s in proposal.script_patches)
    return ApplyResult(graph=g, blocks=blocks)
```

> **Implementer note:** `replace(node, chosen_fix=fix)` needs `node.data` to remain a plain value; `schema.Node.__post_init__` re-wraps `data` as a read-only view, so `replace` is safe. Keep the two `from ... import` lines deduped with Task 3's imports at the top of the file (merge them; do not leave two import blocks).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_patch_gate_apply.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/patch_gate.py tests/depgraph/test_patch_gate_apply.py
git commit -m "feat(depgraph): PatchGate.apply_proposal (pure reducer; never writes SATISFIED)"
```

---

### Task 5: `patch_gate.py` — `compose_script` (recompile-after-mutation)

**Files:**
- Modify: `src/python_deps/depgraph/patch_gate.py` (append `compose_script`)
- Test: `tests/depgraph/test_compose_script.py`

**Interfaces:**
- Consumes: `block.{Block,compile_blocks}`, `schema.Layer`, `script.render_setup_sh`/`parse_setup_sh` (test only).
- Produces: `compose_script(graph: DepGraph, manual_blocks: tuple[Block, ...] = ()) -> tuple[Block, ...]` — `compile_blocks(graph)` (untouched topo order) merged with `manual_blocks`, deduped by `block_id` (graph-compiled wins on collision), each manual block slotted into its wave via a STABLE sort by wave rank. This is the artifact source 2b renders each cycle.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_compose_script.py
from python_deps.depgraph.block import Block
from python_deps.depgraph.patch_gate import compose_script
from python_deps.depgraph.script import render_setup_sh, parse_setup_sh
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)


def _graph_two_waves():
    g = DepGraph()
    g = g.with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB, name="libpq.so",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
        check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev"))
    g = g.with_node(Node(id="pkg:psycopg2==2.9.9", type=NodeType.PACKAGE, name="psycopg2",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="2.9.9",
        check_command="python -m pip show psycopg2"))
    return g


def test_compiled_only_when_no_manual():
    blocks = compose_script(_graph_two_waves())
    ids = [b.block_id for b in blocks]
    assert ids == ["system.libpq.so", "pip.psycopg2==2.9.9"]   # compiled topo order preserved


def test_manual_system_block_slots_into_system_wave_before_pip():
    manual = (Block(block_id="system.extra", wave="system",
                    commands=("make install",), target_node_ids=("syslib:libpq.so",)),)
    blocks = compose_script(_graph_two_waves(), manual)
    ids = [b.block_id for b in blocks]
    # manual system block after compiled system block, both before the pip block
    assert ids.index("system.extra") > ids.index("system.libpq.so")
    assert ids.index("system.extra") < ids.index("pip.psycopg2==2.9.9")


def test_dedupe_block_id_compiled_wins():
    manual = (Block(block_id="system.libpq.so", wave="system",
                    commands=("echo override",), target_node_ids=("syslib:libpq.so",)),)
    blocks = compose_script(_graph_two_waves(), manual)
    libpq = [b for b in blocks if b.block_id == "system.libpq.so"]
    assert len(libpq) == 1 and "override" not in libpq[0].commands[0]   # compiled kept


def test_round_trips_through_render_parse():
    manual = (Block(block_id="system.extra", wave="system",
                    commands=("make install",), target_node_ids=("syslib:libpq.so",)),)
    blocks = compose_script(_graph_two_waves(), manual)
    assert parse_setup_sh(render_setup_sh(blocks)) == blocks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_compose_script.py -q`
Expected: FAIL with `ImportError: cannot import name 'compose_script'`.

- [ ] **Step 3: Write minimal implementation (append to `patch_gate.py`)**

```python
# append to src/python_deps/depgraph/patch_gate.py
from python_deps.depgraph.block import compile_blocks

# Wave rank for slotting manual blocks; mirrors the Layer enum order so a manual
# "system" block runs before any "pip" block. compile_blocks already emits compiled
# blocks in topo (wave-rank-nondecreasing) order, and Python's sort is STABLE, so
# sorting the merged list by wave rank leaves compiled blocks in place and slots
# each manual block after the compiled blocks of its wave.
_WAVE_RANK: dict[str, int] = {layer.value: i for i, layer in enumerate(Layer)}


def compose_script(graph: DepGraph, manual_blocks: tuple[Block, ...] = ()) -> tuple[Block, ...]:
    compiled = compile_blocks(graph)
    seen = {b.block_id for b in compiled}
    fresh = []
    for b in manual_blocks:
        if b.block_id in seen:                 # graph-compiled block wins on id collision
            continue
        seen.add(b.block_id)
        fresh.append(b)
    if not fresh:
        return compiled
    merged = list(compiled) + fresh            # compiled first -> stable sort keeps them first per wave
    merged.sort(key=lambda b: _WAVE_RANK.get(b.wave, len(_WAVE_RANK)))
    return tuple(merged)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_compose_script.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/patch_gate.py tests/depgraph/test_compose_script.py
git commit -m "feat(depgraph): compose_script (compiled blocks + governed manual overlay)"
```

---

### Task 6: soft/hard-edge seam in `schedule._dependencies_satisfied`

**Files:**
- Modify: `src/python_deps/depgraph/schedule.py:18-25` (`_dependencies_satisfied`)
- Test: `tests/depgraph/test_soft_edge_seam.py`

**Interfaces:**
- Produces: `_dependencies_satisfied` now treats an edge with `data["hard"] is False` as non-blocking (invariant #10). Behaviour-preserving for existing graphs (all current edges omit `data["hard"]`, defaulting to hard).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_soft_edge_seam.py
from python_deps.depgraph.schema import (
    DepGraph, Node, Edge, NodeType, Layer, State, DiscoveredBy, EdgeType,
)
from python_deps.depgraph.schedule import _dependencies_satisfied


def _two_nodes():
    dependent = Node(id="pkg:app", type=NodeType.PACKAGE, name="app", layer=Layer.PIP,
                     discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING)
    dep = Node(id="config:DATABASE_URL", type=NodeType.CONFIG, name="DATABASE_URL",
               layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING)
    return dependent, dep


def test_hard_unsatisfied_dep_blocks():
    dependent, dep = _two_nodes()
    g = DepGraph().with_node(dependent).with_node(dep).with_edge(
        Edge(src="pkg:app", dst="config:DATABASE_URL", relation=EdgeType.REQUIRES))
    assert _dependencies_satisfied(g, dependent) is False


def test_soft_unsatisfied_dep_does_not_block():
    dependent, dep = _two_nodes()
    g = DepGraph().with_node(dependent).with_node(dep).with_edge(
        Edge(src="pkg:app", dst="config:DATABASE_URL", relation=EdgeType.REQUIRES,
             data={"hard": False}))
    assert _dependencies_satisfied(g, dependent) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_soft_edge_seam.py -q`
Expected: FAIL — `test_soft_unsatisfied_dep_does_not_block` returns `False` (the soft edge still blocks because the filter is not yet present). `test_hard_unsatisfied_dep_blocks` already passes.

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/depgraph/schedule.py`, change `_dependencies_satisfied` so the
`requires`-edge condition also requires the edge to be hard:

```python
def _dependencies_satisfied(graph: DepGraph, node: Node) -> bool:
    """True when every HARD node this one REQUIRES is SATISFIED.

    Soft edges (``Edge.data["hard"] is False``) never block scheduling (invariant #10);
    they are hints/candidates promoted to hard only on runtime/gate failure.
    """
    for edge in graph.edges:
        if (edge.src == node.id and edge.relation is EdgeType.REQUIRES
                and edge.data.get("hard", True)):
            dep = graph.get(edge.dst)
            if dep is None or dep.state is not State.SATISFIED:
                return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_soft_edge_seam.py -q`
Expected: PASS (2 tests). Then run the scheduler suite to confirm no regression:
`python3 -m pytest tests/depgraph/test_schedule.py tests/envstate/test_graph_scheduler.py -q` — green.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schedule.py tests/depgraph/test_soft_edge_seam.py
git commit -m "feat(depgraph): soft/hard-edge seam — _dependencies_satisfied honors Edge.data['hard']"
```

---

### Task 7: Phase-2a invariant suite + full-suite regression gate

**Files:**
- Create: `tests/depgraph/test_gsm_invariants_phase2a.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6. Encodes the §16 invariants Phase 2a can assert as the contribution surface.

- [ ] **Step 1: Write the test**

```python
# tests/depgraph/test_gsm_invariants_phase2a.py
"""Design §16 invariants assertable in Phase 2a (PatchGate)."""
from python_deps.depgraph.patch import PatchProposal, NodeSpec, ScriptPatch
from python_deps.depgraph.patch_gate import apply_proposal, validate_proposal
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)

_EV = frozenset({"ev1"})


def test_invariant3_4_apply_never_yields_satisfied():
    p = PatchProposal(add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib",
        name="libpq.so", layer="system", check_command="ldconfig -p | grep -q libpq",
        evidence_ref="ev1", promotion="candidate"),))
    res = apply_proposal(DepGraph(), p)
    assert res.graph.get("syslib:libpq.so").state is State.MISSING


def test_invariant6_model_cannot_carry_state():
    # NodeSpec has no `state` field; a SATISFIED attempt can only arrive as a promotion tag,
    # which validate rejects.
    assert not hasattr(NodeSpec("x:y", "Tool", "y", "toolchain"), "state")
    p = PatchProposal(add_requirements=(NodeSpec(id="tool:foo", type="Tool", name="foo",
        layer="toolchain", check_command="foo --version", evidence_ref="ev1",
        promotion="SATISFIED"),))
    assert any("promotion" in e.lower() for e in validate_proposal(DepGraph(), p, known_evidence_ids=_EV))


def test_invariant8_every_accepted_block_targets_existing_node():
    g = DepGraph().with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB, name="libpq.so",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING))
    sp = ScriptPatch(block_id="system.x", wave="system",
        commands=("apt-get install -y libpq-dev",), target_node_ids=("syslib:libpq.so",),
        evidence_ref="ev1")
    res = apply_proposal(g, PatchProposal(script_patches=(sp,)))
    for b in res.blocks:
        for nid in b.target_node_ids:
            assert res.graph.get(nid) is not None


def test_validate_is_pure():
    g = DepGraph()
    before = (g.nodes, g.edges)
    validate_proposal(g, PatchProposal(), known_evidence_ids=frozenset())
    assert (g.nodes, g.edges) == before
```

- [ ] **Step 2: Run the suite (expect PASS — characterization over finished Tasks 1–6)**

Run: `python3 -m pytest tests/depgraph/test_gsm_invariants_phase2a.py -q`
Expected: PASS (4 tests). If any FAILS, a Task 1–6 invariant is broken — fix the implementation, not the test.

- [ ] **Step 3: Full-suite regression gate**

Run: `python3 -m pytest tests -q -p no:cacheprovider`
Expected: only the 4 known pre-existing failures remain (`test_adapter_logic` nested_pytester, `test_repo2run_dataset` ×2, `test_runtime_pin_seam` floor-trap), 0 new. Any NEW failure means a Phase-2a module leaked into an existing import path — investigate before committing.

- [ ] **Step 4: Commit**

```bash
git add tests/depgraph/test_gsm_invariants_phase2a.py
git commit -m "test(depgraph): Phase-2a GSM invariants (apply never SATISFIED; model cannot certify; validate pure)"
```

---

## Phase 2a done-definition

- `parse_patch_proposal → validate_proposal → apply_proposal → compose_script` works end-to-end on hand-built proposals + graphs, with the §10 checks enforced and the §16 invariants asserted (apply never SATISFIED; the model is structurally incapable of certifying; script_patches are governed blocks, not state).
- The provider action-class taxonomy rejects the §14 wrong-action-class case.
- The soft/hard-edge seam is in place and behaviour-preserving.
- Neither `run_v1` nor `run_v3` changed; full suite green except the 4 known pre-existing failures.

## Next phase (2b — separate plan, do NOT start here)

2b is the integration: rewrite `run_v3` to drive `compile_blocks → run_blocks → certify_refresh`; emit a `PatchProposal` from the BuildAgent via structured output (replacing the `Action:`/`Final Answer:` free-text parse at `build_agent.py:162,207`, invariant #6); call `validate_proposal → apply_proposal → compose_script → render_setup_sh` each cycle (recompile-after-mutation); fork `emit_drain` (`depgraph_live.py:89`) so v3 stops producing `RecipePatch` and delete the dead `apply_recipe_patch` branch (`orchestrator.py:603-639`); switch `_finalize_supervisor_artifacts` (`agent.py:1638`) to the compiled `setup.sh` for v3 (keep ledger-replay for v1); add the LLM config/service classifier feeding soft-hint proposals + generalize the `schedule._is_actionable` CONFIG/SERVICE carve-out into the soft/hard rule; carry the three Phase-1-review §5.2-bundle corrections; keep `_verified_test_run_passed` as the binding done-gate (§18 #3); expose the internal script-materialization toggle for the §14 B3 ablation. Re-baseline v3 after.
