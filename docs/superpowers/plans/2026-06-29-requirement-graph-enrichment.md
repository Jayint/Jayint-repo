# Requirement-Graph Enrichment (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the v3 agent a derived, structured `RequirementSlice` (providers, structural context, layer cohort, active gate, platform + evidence as text) instead of a flat fact list — all read-time derivation from data already on the graph, with no new node type, edge, storage, or reasoning plane.

**Architecture:** A new pure module `src/python_deps/depgraph/req_slice.py` builds + renders the slice. `frame_obligation` attaches the typed slice to `ObligationPacket`; `packet_to_task` renders it into `Task.facts`. `Task` and `build_agent` are unchanged — the free-text agent just gets richer bullets; the typed object rides on the packet for Slice B.

**Tech Stack:** Python 3, `pytest`, the existing `python_deps/depgraph` engine + `src/envstate` orchestration.

**Source design:** `docs/superpowers/specs/2026-06-29-requirement-graph-enrichment-design.md`.

## Global Constraints

- **`python_deps/depgraph` stays envstate-free.** `req_slice.py` MUST NOT import anything from `src.envstate` (no `VERIFY_TEST_CMD`, no constants). `active_gate` comes from the graph's `TEST` node's own `check_command`, falling back to `""`.
- **No new node types, edges, storage, or reasoning plane.** The slice is *derived at read-time*; nothing is written to `node.data` at construction. No `Provider`/`Gate`/`ActionBlock` nodes, no `provides`/`constrains` edges, no evidence-id scheme.
- **`world_model.Task` and `build_agent._build_task_message` are NOT modified.** Delivery is via `ObligationPacket.requirement_slice` rendered into the existing `Task.facts` tuple.
- **v1 untouched.** `run_v1` and the planner path do not call `frame_obligation`/`next_decision`.
- **Reuse, don't reimplement:** `advise.{_chain_to_goal,_conflict_note,_best_evidence_line,_platform_note}`, `schema.DepGraph.{requires_of,required_by,get}`, `Node.attempts`. Import the `advise` helpers **lazily inside the function** to avoid any module load-order coupling.
- **Git hygiene:** `git add` only the exact files each task creates/modifies — NEVER `git add -A`/`.`/`<dir>`. Conventional commit messages with an Observation/Why/What/Verification body. **No `Co-Authored-By` trailer.** Do not push.

### Verified integration points (grounded 2026-06-28/29)

```python
# src/python_deps/depgraph/schema.py
@dataclass(frozen=True)
class Attempt:  command:str  outcome:str  check:str=""  cycle:int=0          # :102
class Node:  id type name layer discovered_by tier state version=None check_command=None
             evidence=None fix_candidates:tuple[str,...]=() chosen_fix:str|None=None
             attempts:tuple[Attempt,...]=() resolved_python=None data:dict=...      # frozen
class DepGraph:
    def get(self, node_id) -> Node | None                 # :242
    def requires_of(self, node_id) -> tuple[Node, ...]    # :296  (outgoing REQUIRES = this node's deps)
    def required_by(self, node_id) -> tuple[Node, ...]    # :306  (incoming REQUIRES = who needs this)
    nodes: tuple[Node,...] ; edges: tuple[Edge,...]
NodeType.TEST ; State.{SATISFIED,MISSING,UNKNOWN} (.value -> "satisfied"/"missing"/"unknown")

# src/python_deps/depgraph/advise.py  (reuse; import lazily)
def _chain_to_goal(graph, node, limit=6) -> str          # :192  "libxml2 <- lxml <- repo_tests_pass"
def _conflict_note(graph, node) -> str | None            # :213
def _best_evidence_line(evidence) -> str | None          # :50   (last error-matching line, <=160 chars)
def _platform_note(node) -> str | None                   # :225  "resolved for: 3.10 / manylinux..."

# src/python_deps/depgraph/schedule.py
@dataclass(frozen=True)
class ObligationPacket:  node_id node_type tier layer goal evidence check_command
    depends_on=() blocks=() certified_context=() start_recipe=None bind_recipe=None   # :69  (frozen)
def frame_obligation(graph, node) -> ObligationPacket    # :86  (computes blocks at :91, certified_context :95)

# src/envstate/graph_scheduler.py
def packet_to_task(packet) -> Task                       # :20  (builds Task.facts from packet fields :21-44)
def _discover_task() -> Task                             # :54  (facts=())

# src/envstate/world_model.py
@dataclass(frozen=True)
class Task:  goal done_when layer facts:tuple[str,...] target_node_ids=() transition_proposal=None  # :116
```

---

### Task 1: `providers_view(node)` — structured providers (the "A" piece)

**Files:**
- Create: `src/python_deps/depgraph/req_slice.py`
- Test: `tests/depgraph/test_req_slice.py`

**Interfaces:**
- Produces: `ProviderCand(id:str, action_class:str)`, `TriedProvider(command:str, outcome:str, provider_id:str|None)`, `ProviderView(candidates:tuple, chosen:str|None, tried_failed:tuple)`, and `providers_view(node) -> ProviderView`. Pure; reads only `node.fix_candidates`, `node.chosen_fix`, `node.attempts`. Consumed by Task 2.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_req_slice.py
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import Node, NodeType, Layer, State, DiscoveredBy, Attempt
from python_deps.depgraph.req_slice import providers_view, ProviderView, ProviderCand, TriedProvider


def _syslib(**kw):
    base = dict(id="syslib:libxml2", type=NodeType.SYSTEM_LIB, name="libxml2",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                check_command="pkg-config --exists libxml-2.0",
                fix_candidates=("apt:libxml2-dev",), chosen_fix="apt:libxml2-dev")
    base.update(kw)
    return Node(**base)


def test_candidates_include_chosen_and_action_class():
    pv = providers_view(_syslib())
    ids = [c.id for c in pv.candidates]
    assert "apt:libxml2-dev" in ids
    assert pv.chosen == "apt:libxml2-dev"
    assert next(c.action_class for c in pv.candidates if c.id == "apt:libxml2-dev") == "apt"


def test_chosen_added_to_candidates_when_absent():
    pv = providers_view(_syslib(fix_candidates=()))   # chosen set but not in candidates
    assert "apt:libxml2-dev" in [c.id for c in pv.candidates]


def test_tried_failed_derived_from_failed_attempts_with_reverse_parse():
    node = _syslib(attempts=(
        Attempt(command="apt-get install -y libxml2dev", outcome="failed", check="", cycle=1),
        Attempt(command="apt-get install -y libxml2-dev", outcome="succeeded", check="", cycle=2),
    ))
    pv = providers_view(node)
    assert len(pv.tried_failed) == 1                       # only the failed one
    t = pv.tried_failed[0]
    assert t.command == "apt-get install -y libxml2dev"
    assert t.provider_id == "apt:libxml2dev"                # single-token reverse-parse


def test_batch_command_has_no_provider_id():
    node = _syslib(attempts=(Attempt(command="apt-get install -y a b c", outcome="failed"),))
    assert providers_view(node).tried_failed[0].provider_id is None   # batch -> not attributable


def test_pip_provider_action_class_and_reverse_parse():
    node = Node(id="pkg:lxml==5.0", type=NodeType.PACKAGE, name="lxml", layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="5.0",
                check_command="python -m pip show lxml", fix_candidates=("pip:lxml",),
                chosen_fix="pip:lxml",
                attempts=(Attempt(command="pip install lxml==5.0", outcome="failed"),))
    pv = providers_view(node)
    assert next(c.action_class for c in pv.candidates if c.id == "pip:lxml") == "pip"
    assert pv.tried_failed[0].provider_id == "pip:lxml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_req_slice.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.req_slice'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/req_slice.py
"""Read-time derivation of the structured RequirementSlice the v3 agent sees (design
2026-06-29). Pure: no Docker/LLM/subprocess and NO dependency on src.envstate."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCand:
    id: str
    action_class: str            # "apt" | "pip" | "npm" | "" (undeterminable)


@dataclass(frozen=True)
class TriedProvider:
    command: str
    outcome: str
    provider_id: str | None      # best-effort reverse-parse of `command`; None for batch/unparseable


@dataclass(frozen=True)
class ProviderView:
    candidates: tuple[ProviderCand, ...]
    chosen: str | None
    tried_failed: tuple[TriedProvider, ...]


def _action_class_for(provider_id: str) -> str:
    head = provider_id.split(":", 1)[0] if ":" in provider_id else ""
    return {"apt": "apt", "pip": "pip", "npm": "npm"}.get(head, "")


def _provider_from_command(command: str) -> str | None:
    """Map an install command back to a provider id when EXACTLY one package is named
    (single-token); batch installs are not cleanly attributable -> None."""
    m = re.search(r"\bapt(?:-get)?\s+install\b(.*)", command)
    if m:
        toks = [t for t in m.group(1).split() if not t.startswith("-")]
        return f"apt:{toks[0]}" if len(toks) == 1 else None
    m = re.search(r"\bpip3?\s+install\b(.*)", command)
    if m:
        toks = [t for t in m.group(1).split() if not t.startswith("-")]
        return f"pip:{toks[0].split('==')[0]}" if len(toks) == 1 else None
    return None


def providers_view(node) -> ProviderView:
    cand_ids = list(node.fix_candidates)
    if node.chosen_fix and node.chosen_fix not in cand_ids:
        cand_ids.append(node.chosen_fix)
    candidates = tuple(ProviderCand(id=c, action_class=_action_class_for(c)) for c in cand_ids)
    tried = tuple(
        TriedProvider(command=a.command, outcome=a.outcome,
                      provider_id=_provider_from_command(a.command))
        for a in node.attempts if a.outcome == "failed"
    )
    return ProviderView(candidates=candidates, chosen=node.chosen_fix, tried_failed=tried)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_req_slice.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/req_slice.py tests/depgraph/test_req_slice.py
git commit -m "feat(req-graph): providers_view — structured provider derivation (candidates/chosen/tried-failed)"
```

---

### Task 2: `build_requirement_slice(graph, node)` — structural context (B/C/D/F)

**Files:**
- Modify: `src/python_deps/depgraph/req_slice.py`
- Test: `tests/depgraph/test_req_slice.py` (add to the same file)

**Interfaces:**
- Consumes: `providers_view` (Task 1); the `advise` helpers (lazy import); `DepGraph.{requires_of,required_by}`.
- Produces: `DepView(id:str, state:str)`, `RequirementSlice` (the §3.1 field set), and `build_requirement_slice(graph, node) -> RequirementSlice`. Pure. Consumed by Task 3 (render) and Task 4 (frame_obligation).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_req_slice.py
from python_deps.depgraph.schema import DepGraph, Edge, EdgeType
from python_deps.depgraph.req_slice import build_requirement_slice, RequirementSlice, DepView


def _graph_with_frontier():
    g = DepGraph()
    g = g.with_node(Node(id="test:repo_tests_pass", type=NodeType.TEST, name="repo_tests_pass",
        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.MISSING,
        check_command="python -m pytest -q"))
    g = g.with_node(Node(id="pkg:lxml==5.0", type=NodeType.PACKAGE, name="lxml", layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="5.0",
        check_command="python -m pip show lxml"))
    g = g.with_node(Node(id="syslib:libxml2", type=NodeType.SYSTEM_LIB, name="libxml2",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
        check_command="pkg-config --exists libxml-2.0", chosen_fix="apt:libxml2-dev",
        fix_candidates=("apt:libxml2-dev",), evidence='Dependency "libxml2" not found, tried pkgconfig',
        attempts=(Attempt(command="apt-get install -y libxml2dev", outcome="failed"),)))
    g = g.with_node(Node(id="tool:pkg-config", type=NodeType.TOOL, name="pkg-config",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.SATISFIED,
        check_command="command -v pkg-config", chosen_fix="apt:pkg-config"))
    # Package -> SystemLib is EDGE_RULES-legal (mirrors test_compose_script). pkg requires the syslib.
    g = g.with_edge(Edge(src="pkg:lxml==5.0", dst="syslib:libxml2", relation=EdgeType.REQUIRES))
    return g


def test_build_slice_deps_unblocks_cohort_providers_gate():
    g = _graph_with_frontier()
    s = build_requirement_slice(g, g.get("syslib:libxml2"))
    assert isinstance(s, RequirementSlice)
    assert s.node_id == "syslib:libxml2" and s.kind == "SystemLib" and s.state == "missing"
    assert s.check == "pkg-config --exists libxml-2.0"
    # unblocks = reverse REQUIRES (recovers the dropped `blocks`): pkg:lxml needs the syslib
    assert "pkg:lxml==5.0" in s.unblocks
    # layer cohort (SYSTEM): the satisfied pkg-config tool, syslib itself excluded
    assert "tool:pkg-config" in s.layer_cohort_satisfied
    assert s.node_id not in s.layer_cohort_satisfied and s.node_id not in s.layer_cohort_missing
    # active gate synthesized from the TEST node's own check (no envstate import)
    assert s.active_gate == "python -m pytest -q"
    # providers + tried-failed carried through
    assert s.providers.chosen == "apt:libxml2-dev"
    assert s.providers.tried_failed and s.providers.tried_failed[0].provider_id == "apt:libxml2dev"
    # evidence reduced to the best line (text, not an id)
    assert "libxml2" in s.evidence


def test_active_gate_empty_when_no_test_node():
    g = DepGraph().with_node(Node(id="syslib:x", type=NodeType.SYSTEM_LIB, name="x",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
        check_command="pkg-config --exists x"))
    s = build_requirement_slice(g, g.get("syslib:x"))
    assert s.active_gate == ""          # no TEST node -> empty, never crashes
```

> **Implementer note:** `DepGraph.with_edge` takes an `Edge(src=, dst=, relation=)` object (NOT kwargs on `with_edge`) and validates against `schema.EDGE_RULES` (raises on an illegal pair). `Package -> SystemLib` is known-legal (used in `tests/depgraph/test_compose_script.py`). The assertions only need: a frontier node with a reverse-dep (`required_by`) and a same-layer satisfied cohort node — so if you add more edges, confirm each pair is legal per `EDGE_RULES` or `with_edge` will raise.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_req_slice.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_requirement_slice'`.

- [ ] **Step 3: Implement**

Append to `src/python_deps/depgraph/req_slice.py`:

```python
@dataclass(frozen=True)
class DepView:
    id: str
    state: str


@dataclass(frozen=True)
class RequirementSlice:
    node_id: str
    kind: str
    layer: str
    state: str
    check: str
    evidence: str
    deps: tuple[DepView, ...]
    chain_to_goal: str
    unblocks: tuple[str, ...]
    layer_cohort_satisfied: tuple[str, ...]
    layer_cohort_missing: tuple[str, ...]
    conflict: str | None
    providers: ProviderView
    active_gate: str
    platform: str | None


def build_requirement_slice(graph, node) -> RequirementSlice:
    """Pure read-time projection of `node` for the agent. Reuses advise's render helpers
    (imported lazily to avoid module load-order coupling)."""
    from python_deps.depgraph.advise import (
        _chain_to_goal, _conflict_note, _best_evidence_line, _platform_note,
    )
    from python_deps.depgraph.schema import NodeType, State

    deps = tuple(DepView(id=d.id, state=d.state.value) for d in graph.requires_of(node.id))
    unblocks = tuple(n.id for n in graph.required_by(node.id))
    cohort = [n for n in graph.nodes if n.layer == node.layer and n.id != node.id]
    sat = tuple(n.id for n in cohort if n.state is State.SATISFIED)
    miss = tuple(n.id for n in cohort if n.state is State.MISSING)
    goal = next((n for n in graph.nodes if n.type is NodeType.TEST), None)
    active_gate = goal.check_command if (goal and goal.check_command) else ""
    return RequirementSlice(
        node_id=node.id, kind=node.type.value, layer=node.layer.value, state=node.state.value,
        check=node.check_command or "", evidence=_best_evidence_line(node.evidence) or "",
        deps=deps, chain_to_goal=_chain_to_goal(graph, node) or "", unblocks=unblocks,
        layer_cohort_satisfied=sat, layer_cohort_missing=miss,
        conflict=_conflict_note(graph, node), providers=providers_view(node),
        active_gate=active_gate, platform=_platform_note(node),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_req_slice.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/req_slice.py tests/depgraph/test_req_slice.py
git commit -m "feat(req-graph): build_requirement_slice — deps/unblocks/cohort/gate/platform derivation"
```

---

### Task 3: `render_requirement_slice(slice)` — compact agent-facing text

**Files:**
- Modify: `src/python_deps/depgraph/req_slice.py`
- Test: `tests/depgraph/test_req_slice.py`

**Interfaces:**
- Consumes: `RequirementSlice` (Task 2).
- Produces: `render_requirement_slice(slice) -> tuple[str]` — one logical fact line per element, omitting empty sections, never crashing on None/empty fields. Consumed by Task 5 (`packet_to_task`).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_req_slice.py
from python_deps.depgraph.req_slice import render_requirement_slice


def test_render_contains_target_avoidance_and_no_crash():
    g = _graph_with_frontier()
    lines = render_requirement_slice(build_requirement_slice(g, g.get("syslib:libxml2")))
    blob = "\n".join(lines)
    assert any(l.startswith("target:") for l in lines)
    assert "apt:libxml2-dev" in blob                         # candidate/chosen surfaced
    assert "tried & FAILED" in blob and "avoid apt:libxml2dev" in blob  # the anti-ReAct signal
    assert "active gate: python -m pytest -q" in blob
    assert all(isinstance(l, str) for l in lines)


def test_render_empty_slice_does_not_crash():
    g = DepGraph().with_node(Node(id="syslib:x", type=NodeType.SYSTEM_LIB, name="x",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING))
    lines = render_requirement_slice(build_requirement_slice(g, g.get("syslib:x")))
    assert lines and lines[0].startswith("target:")          # minimal but valid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_req_slice.py -q`
Expected: FAIL — `ImportError: cannot import name 'render_requirement_slice'`.

- [ ] **Step 3: Implement**

Append to `src/python_deps/depgraph/req_slice.py`:

```python
def render_requirement_slice(s: RequirementSlice) -> tuple[str]:
    """Compact, agent-readable fact lines. Empty sections are omitted."""
    lines = [f"target: {s.node_id}  ({s.kind}, {s.layer}, {s.state})"]
    gate = f"   [active gate: {s.active_gate}]" if s.active_gate else ""
    lines.append(f"why: {s.chain_to_goal or '(no chain to goal)'}{gate}")
    if s.check:
        lines.append(f"check: {s.check}")
    if s.deps:
        lines.append("deps: " + ", ".join(f"{d.id}={d.state}" for d in s.deps))
    pv = s.providers
    if pv.candidates:
        chosen = f"  chosen={pv.chosen}" if pv.chosen else ""
        lines.append("providers: candidates=[" + ", ".join(c.id for c in pv.candidates) + "]" + chosen)
    for t in pv.tried_failed:
        avoid = f"  (=> avoid {t.provider_id})" if t.provider_id else ""
        lines.append(f"tried & FAILED: {t.command}{avoid}")
    if s.layer_cohort_satisfied or s.layer_cohort_missing:
        lines.append(
            f"layer ({s.layer}): satisfied=[{', '.join(s.layer_cohort_satisfied)}]  "
            f"missing=[{', '.join(s.layer_cohort_missing)}]"
        )
    if s.conflict:
        lines.append(s.conflict)
    if s.platform:
        lines.append(s.platform)
    if s.evidence:
        lines.append(f"evidence: {s.evidence}")
    return tuple(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_req_slice.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/req_slice.py tests/depgraph/test_req_slice.py
git commit -m "feat(req-graph): render_requirement_slice — compact agent-facing fact lines"
```

---

### Task 4: attach the slice in `frame_obligation` (ObligationPacket)

**Files:**
- Modify: `src/python_deps/depgraph/schedule.py` (`ObligationPacket` `:69`, `frame_obligation` `:86`)
- Test: `tests/depgraph/test_frame_obligation_slice.py`

**Interfaces:**
- Consumes: `build_requirement_slice` (Task 2).
- Produces: `ObligationPacket` gains `requirement_slice: RequirementSlice | None = None`, populated by `frame_obligation`. Consumed by Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_frame_obligation_slice.py
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from python_deps.depgraph.schedule import frame_obligation


def _g():
    g = DepGraph().with_node(Node(id="syslib:libxml2", type=NodeType.SYSTEM_LIB, name="libxml2",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
        check_command="pkg-config --exists libxml-2.0", chosen_fix="apt:libxml2-dev",
        fix_candidates=("apt:libxml2-dev",)))
    return g


def test_frame_obligation_attaches_requirement_slice():
    g = _g()
    pkt = frame_obligation(g, g.get("syslib:libxml2"))
    assert pkt.requirement_slice is not None
    assert pkt.requirement_slice.node_id == "syslib:libxml2"
    assert pkt.requirement_slice.providers.chosen == "apt:libxml2-dev"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_frame_obligation_slice.py -q`
Expected: FAIL — `AttributeError: 'ObligationPacket' object has no attribute 'requirement_slice'`.

- [ ] **Step 3: Implement**

In `src/python_deps/depgraph/schedule.py`:
1. Add the import near the top (after the existing `from python_deps.depgraph.emit import topo_order`):

```python
from python_deps.depgraph.req_slice import RequirementSlice, build_requirement_slice
```

2. Add the field to `ObligationPacket` (after `bind_recipe`, keeping it last so construction stays positional-safe):

```python
    requirement_slice: RequirementSlice | None = None
```

3. In `frame_obligation`, populate it in the returned `ObligationPacket(...)`:

```python
        requirement_slice=build_requirement_slice(graph, node),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_frame_obligation_slice.py -q`
Expected: PASS. Then `python3 -m pytest tests/depgraph -q -k "schedule or oblig or compose or block"` — existing schedule/packet tests still green (the new field is defaulted).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schedule.py tests/depgraph/test_frame_obligation_slice.py
git commit -m "feat(req-graph): frame_obligation attaches RequirementSlice to ObligationPacket"
```

---

### Task 5: render the slice into `Task.facts` in `packet_to_task`

**Files:**
- Modify: `src/envstate/graph_scheduler.py` (`packet_to_task` `:20`)
- Test: `tests/test_graph_scheduler_slice.py`

**Interfaces:**
- Consumes: `render_requirement_slice` (Task 3), `ObligationPacket.requirement_slice` (Task 4).
- Produces: when the packet carries a slice, `Task.facts` is the rendered slice lines (replacing the old flat `evidence`/`depends_on`/`certified_context` facts), with the service-recipe facts (`start_recipe`/`bind_recipe`) appended after. No slice → the existing flat facts (back-compat). `_discover_task` unchanged (`facts=()`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_scheduler_slice.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from python_deps.depgraph.schedule import frame_obligation
from src.envstate.graph_scheduler import packet_to_task


def _frontier_packet():
    g = DepGraph().with_node(Node(id="syslib:libxml2", type=NodeType.SYSTEM_LIB, name="libxml2",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
        check_command="pkg-config --exists libxml-2.0", chosen_fix="apt:libxml2-dev",
        fix_candidates=("apt:libxml2-dev",)))
    return frame_obligation(g, g.get("syslib:libxml2"))


def test_task_facts_are_the_rendered_slice():
    task = packet_to_task(_frontier_packet())
    blob = "\n".join(task.facts)
    assert any(f.startswith("target: syslib:libxml2") for f in task.facts)   # slice rendered
    assert "providers: candidates=[apt:libxml2-dev]" in blob
    assert task.done_when == "pkg-config --exists libxml-2.0"
    assert task.target_node_ids == ("syslib:libxml2",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_graph_scheduler_slice.py -q`
Expected: FAIL — the facts are the old flat list (no `target:` line).

- [ ] **Step 3: Implement**

In `src/envstate/graph_scheduler.py`, add the import at the top:

```python
from python_deps.depgraph.req_slice import render_requirement_slice
```

Then in `packet_to_task`, replace the construction of `facts` so the slice supersedes the flat graph facts while keeping the service-recipe facts. The new body of `packet_to_task` (the `facts` assembly, `:21-44`) becomes:

```python
    facts: list[str] = []
    if packet.requirement_slice is not None:
        facts.extend(render_requirement_slice(packet.requirement_slice))
    else:
        # back-compat: a packet without a slice keeps the old flat facts
        if packet.evidence:
            facts.append(f"evidence: {packet.evidence}")
        if packet.depends_on:
            facts.append("depends_on: " + ", ".join(packet.depends_on))
        if packet.certified_context:
            facts.append("already satisfied: " + ", ".join(packet.certified_context))
    # Service action recipes are instructions, not graph structure — always kept.
    if packet.start_recipe and packet.start_recipe.get("start"):
        facts.append("start the service in-image (run, then the host re-checks "
                     f"`{packet.check_command}`): {packet.start_recipe['start']}")
        if packet.start_recipe.get("createdb"):
            facts.append("then create the bound database: "
                         f"{packet.start_recipe['createdb']}")
    if packet.bind_recipe:
        br = packet.bind_recipe
        au, bp = br.get("alter_user"), br.get("bind_profile")
        if au and bp:
            facts.append("Run this single command to configure the in-image database "
                         "(the host verifies it automatically afterward — do not run any check yourself): "
                         f"{au} && {bp}")
        elif au or bp:
            facts.append("Run this single command to configure the in-image database "
                         "(the host verifies it automatically afterward): "
                         f"{au or bp}")
```

Leave the `return Task(...)` (goal/done_when/layer/facts/target_node_ids) unchanged. `_discover_task` is untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_graph_scheduler_slice.py -q`
Expected: PASS. Then `python3 -m pytest tests/test_graph_scheduler_wiring.py tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py -q` — v1 + the v3 wiring unaffected (the discover-task path + service-recipe facts still work).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/graph_scheduler.py tests/test_graph_scheduler_slice.py
git commit -m "feat(req-graph): packet_to_task renders RequirementSlice into Task.facts (recipe facts kept)"
```

---

### Task 6: full-suite regression + backward-compat gate

**Files:**
- Test: (this task runs the gates; no new files)

- [ ] **Step 1: Full-suite regression**

Run: `python3 -m pytest tests -q -p no:cacheprovider`
Expected: only the 4 known pre-existing failures remain (`test_adapter_logic` nested_pytester, `test_repo2run_dataset` ×2, `test_runtime_pin_seam` floor-trap), 0 new. Any NEW failure → investigate before proceeding. Pay attention to any test that pins the exact `Task.facts` / `ObligationPacket` shape for a graph-scheduled node — if one asserts the OLD flat facts (`evidence:`/`depends_on:`/`already satisfied:`) for a frontier node, it must be updated to the rendered-slice form (this is the intended behaviour change, not a regression).

- [ ] **Step 2: Prove v1 + the depgraph engine unchanged**

Run: `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_graph_scheduler_wiring.py tests/depgraph -q`
Expected: green — v1 untouched; the depgraph engine (resolve/certify/emit/block/compose) unaffected by the pure additive `req_slice` module + the defaulted packet field.

- [ ] **Step 3: Record the result**

Append the pass/fail tally + the exact failing-test names to the run report. If only the 4 known failures remain, the enrichment is regression-clean.

---

## Done-definition

- A frontier obligation handed to the v3 agent carries a structured `RequirementSlice` (providers incl. tried-and-failed, deps-with-states, chain-to-goal, layer cohort, conflict, active gate, platform + evidence as text), rendered into `Task.facts`.
- `req_slice.py` is pure and envstate-free; `Task` and `build_agent` are unchanged; v1 untouched.
- The typed `RequirementSlice` rides on `ObligationPacket` for Slice B to consume.
- Full suite green except the 4 known pre-existing failures.

## After this plan (separate work — do NOT start here)

- **Slice B** consumes the typed `RequirementSlice` as the input half of its `RepairScope` packet: BuildAgent v3 structured-propose path → `PatchProposal` → `validate_proposal`/`apply_proposal` (wire the dead PatchGate) → `compose_script` → re-run.
- **Phase 2 (demand-pulled):** promote Provider/Gate/ActionBlock to real node types + the `provides`/`targets`/`blocked_by` edges; the `constrains` edge + Platform node (the platform-selection fix); the graph-wide evidence-id scheme.
