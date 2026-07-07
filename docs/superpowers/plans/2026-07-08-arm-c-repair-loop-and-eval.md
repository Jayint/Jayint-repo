# Arm C — Build-Script Repair with a Memory: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build arm C — a repair loop that keeps the graph-centric paradigm (host-certified state, typed-patch gate, render-from-graph, clean replay) but replaces the confusing three-level nesting + amnesiac agent with one loop over errors and one sustained per-error `RepairSession`, and ship a checked-in offline eval that validates it e2e.

**Architecture:** The real arm-C loop is written with dependency injection (`replay`, `certify`, `agent` are callables), so the *same* loop runs against Docker in production and against a `FakeWorld` in the eval. Every durable change flows through the existing `patch_gate`; only a host check flips state; the agent carries a compounding per-error conversation. Built as a new self-contained module beside the current loop — the current loop is untouched.

**Tech Stack:** Python 3, pytest, the existing `python_deps.depgraph` package (schema/patch/patch_gate/certify), `src/envstate`.

## Global Constraints

- **Graph-centric (spec §3):** every mutation flows through `admit_proposal`; only `certify`/`certify_all` (a host check) flips `State.SATISFIED`; it is revocable. The agent NEVER writes state.
- **No live container mutation (spec §N1):** the agent's only actions are a **read-only probe** and a **typed `PatchProposal`**. No `apt`/`pip`/file-edit verbs.
- **Clean replay only (spec §N2):** every replay is a full run from a clean base. No incremental execution.
- **Do not touch (spec §N3/§N4):** `run_v1`, `emit_drain`, `repair_failed_nodes`, `block_emit`, `trace_verify`, the construction pipeline, `render_build_script`, `partition`/`emit`, `certify`, `patch_gate` validation rules. Reuse them; never modify them.
- **Decisions locked (spec §13):** one session follows the failure forward across nodes (§13.1); the session transcript persists to the node's `attempts` axis (§13.2); per-error hard turn cap = **15** (§13.3); stall limit = **2** consecutive no-progress patches.
- **Reference implementation:** a proven offline spike exists at `scratchpad/repair_arm_eval.py` (all 3 scenarios green). This plan promotes and splits it, and refactors the loop to be the *real* DI arm C that production will use.
- **Import path:** `python_deps` lives under `src/`; tests and modules import as `from python_deps.depgraph... import ...` and `from src.envstate... import ...` (existing pattern).
- **Scope note:** the A/B comparison eval (arm-B cold-agent head-to-head) and the production cutover are explicitly OUT of this plan (deferred per user direction). This plan delivers arm C + its offline mechanics eval.

---

## File Structure

**Core arm C (new, `src/envstate/`):**
- `repair_types.py` — `ReplayResult` (normalized replay outcome shared by production + eval).
- `repair_session.py` — `Step`, `RepairSession`, `made_progress`, `persist_session_to_attempts`.
- `repair_fix.py` — `fix_one_error` (the per-error session loop).
- `repair_arm.py` — `run_repair_arm` (the error-loop driver + global termination).
- `session_agent.py` — `SessionAgent` (LLM port: sustained conversation, PROBE + PATCH → typed patch).

**Eval harness (new, `src/eval/repair_arm_eval/`):**
- `mock_world.py` — `RealNode`, `FakeWorld` (offline reality model → `replay`/`certify` fakes).
- `design_log.py` — `DesignLog` (design-point logger tagged to spec sections).
- `scripted_agent.py` — `ScriptedSolver` + patch builders (deterministic agent).
- `scenarios.py` — `scenario_simple`, `scenario_chain`, `scenario_stall`.
- `run_eval.py` — CLI runner + design-coverage report.

**Tests (`tests/`):**
- `tests/envstate/test_repair_session.py`, `tests/envstate/test_repair_fix.py`, `tests/envstate/test_repair_arm.py`, `tests/envstate/test_session_agent.py`
- `tests/eval/test_repair_arm_eval.py`

**Shared interface contract (locked across all tasks):**
```python
ReplayResult(ok: bool, failing_node: str|None, failing_cap: str|None,
             failing_command: str|None, output: str)                    # repair_types.py
replay:  Callable[[DepGraph, tuple[Block,...]], ReplayResult]           # injected
certify: Callable[[DepGraph], DepGraph]                                 # injected (wraps certify_all)
agent.next_action(session: RepairSession, failure: ReplayResult, log)   # -> Action
Action = ("probe", cmd: str, cap: str) | ("patch", PatchProposal, cap: str)
fix_one_error(graph, error: ReplayResult, *, agent, replay, certify, log,
              stall_limit=2, turn_cap=15) -> tuple[DepGraph, str]        # str in {"resolved","stalled"}
run_repair_arm(graph, *, replay, certify, agent, log, localize, diagnose,
               max_errors=20) -> tuple[str, DepGraph]                    # str in {"DONE","GIVEUP"}
```

---

### Task 1: `ReplayResult` + `FakeWorld` (offline test substrate)

**Files:**
- Create: `src/envstate/repair_types.py`
- Create: `src/eval/repair_arm_eval/__init__.py` (empty)
- Create: `src/eval/repair_arm_eval/mock_world.py`
- Test: `tests/eval/test_mock_world.py`

**Interfaces:**
- Produces: `ReplayResult`; `RealNode(provides, requires, check_command)`; `FakeWorld(reality, base=("python",))` with `.replay_from_base(graph) -> ReplayResult`, `.certify(graph) -> graph` (wraps real `certify_all`), `.readonly(cmd) -> (rc, str)`.

- [ ] **Step 1: Write the failing test**
```python
# tests/eval/test_mock_world.py
import pytest
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from src.eval.repair_arm_eval.mock_world import FakeWorld, RealNode

def _pkg(nid, name, ver="1.0"):
    return Node(id=nid, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING, version=ver,
                check_command=f"python -c 'import {name}'")

def test_replay_fails_when_real_dep_missing():
    reality = {"pkg:foo": RealNode("foo", frozenset({"libx"}), "python -c 'import foo'")}
    world = FakeWorld(reality)
    g = DepGraph().with_node(_pkg("pkg:foo", "foo"))
    r = world.replay_from_base(g)
    assert not r.ok and r.failing_node == "pkg:foo" and r.failing_cap == "libx"

def test_certify_flips_only_present_nodes():
    from python_deps.depgraph.schema import Edge, EdgeType
    reality = {"pkg:foo": RealNode("foo", frozenset(), "python -c 'import foo'")}
    world = FakeWorld(reality)
    g = DepGraph().with_node(_pkg("pkg:foo", "foo"))
    world.replay_from_base(g)                 # installs foo (no reqs)
    g = world.certify(g)
    assert g.get("pkg:foo").state is State.SATISFIED
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/eval/test_mock_world.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**
```python
# src/envstate/repair_types.py
"""Normalized replay outcome shared by the production Docker path and the offline eval."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ReplayResult:
    ok: bool
    failing_node: str | None = None
    failing_cap: str | None = None
    failing_command: str | None = None
    output: str = ""
```
```python
# src/eval/repair_arm_eval/mock_world.py
"""Offline 'reality' model: a node installs iff its REAL deps (which the graph does not yet
know) are present; a check passes iff the node's capability is present. Uses the REAL
certify_all + REAL graph — only the container is faked. See spec 2026-07-08."""
from __future__ import annotations
from dataclasses import dataclass

from python_deps.depgraph.schema import DepGraph, Node, NodeType, State
from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.executor import CommandResult
from src.envstate.repair_types import ReplayResult

@dataclass(frozen=True)
class RealNode:
    provides: str
    requires: frozenset
    check_command: str

class FakeWorld:
    def __init__(self, reality: dict[str, RealNode], base=("python",)):
        self.reality = reality
        self.base = frozenset(base)
        self.present: set[str] = set(self.base)
        self.check_map = {rn.check_command: rn.provides for rn in reality.values()}

    def _installable(self, n: Node) -> bool:
        if n.type is NodeType.PACKAGE:
            return bool(n.version)
        if n.type in (NodeType.SYSTEM_LIB, NodeType.TOOL):
            return bool(n.chosen_fix)
        return False

    def replay_from_base(self, graph: DepGraph, manual_blocks=()) -> ReplayResult:
        self.present = set(self.base)
        for n in sorted((n for n in graph.nodes if self._installable(n)), key=lambda n: n.tier):
            r = self.reality.get(n.id)
            if r is None:
                continue
            missing = [req for req in sorted(r.requires) if req not in self.present]
            if missing:
                cmd = n.chosen_fix or f"pip install {n.name}"
                return ReplayResult(False, n.id, missing[0], cmd, f"{missing[0]}: not found")
            self.present.add(r.provides)
        return ReplayResult(True)

    def _executor(self):
        world = self
        class _Ex:
            def run(self, command, *, timeout=300):
                cap = world.check_map.get(command)
                ok = cap is not None and cap in world.present
                return CommandResult(command, 0 if ok else 1, "", "" if ok else "not found")
        return _Ex()

    def certify(self, graph: DepGraph) -> DepGraph:
        return certify_all(graph, self._executor())

    def readonly(self, command) -> tuple[int, str]:
        cap = self.check_map.get(command)
        ok = cap is not None and cap in self.present
        return (0 if ok else 1, "present" if ok else "absent")
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/eval/test_mock_world.py -v` → 2 passed.

- [ ] **Step 5: Commit**
```bash
git add src/envstate/repair_types.py src/eval/repair_arm_eval/ tests/eval/test_mock_world.py
git commit -m "feat(arm-c): ReplayResult + FakeWorld offline eval substrate"
```

---

### Task 2: `RepairSession` + `Step` (the notebook)

**Files:**
- Create: `src/envstate/repair_session.py`
- Test: `tests/envstate/test_repair_session.py`

**Interfaces:**
- Produces: `Step(kind, summary, cap=None, accepted=None, replay=None, progress=None)`; `RepairSession(seed_node, seed_cap, steps=[])` with `.probed(cap) -> bool`, `.render_for_agent() -> str`.

- [ ] **Step 1: Write the failing test**
```python
# tests/envstate/test_repair_session.py
from src.envstate.repair_session import RepairSession, Step
from src.envstate.repair_types import ReplayResult

def test_render_shows_full_history():
    s = RepairSession("pkg:psycopg2", "libpq")
    s.steps.append(Step("patch", "add:['syslib:libpq']", cap="libpq",
                        replay=ReplayResult(False, "pkg:psycopg2", "pg_config", "pip install", "")))
    rendered = s.render_for_agent()
    assert "syslib:libpq" in rendered and "pg_config" in rendered

def test_probed_tracks_per_cap():
    s = RepairSession("pkg:x", "libx")
    assert not s.probed("libx")
    s.steps.append(Step("probe", "probe:ldconfig", cap="libx"))
    assert s.probed("libx") and not s.probed("liby")
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/envstate/test_repair_session.py -v` → FAIL.

- [ ] **Step 3: Implement**
```python
# src/envstate/repair_session.py
"""The per-error notebook (spec §5.2). Immutable-append step history the agent reasons over."""
from __future__ import annotations
from dataclasses import dataclass, field
from src.envstate.repair_types import ReplayResult

@dataclass
class Step:
    kind: str                       # "probe" | "patch"
    summary: str
    cap: str | None = None
    accepted: bool | None = None
    replay: ReplayResult | None = None
    progress: bool | None = None

@dataclass
class RepairSession:
    seed_node: str
    seed_cap: str
    steps: list = field(default_factory=list)

    def probed(self, cap) -> bool:
        return any(s.kind == "probe" and s.cap == cap for s in self.steps)

    def render_for_agent(self) -> str:
        if not self.steps:
            return f"(fresh) failing node {self.seed_node}, missing {self.seed_cap}"
        parts = []
        for i, s in enumerate(self.steps):
            tail = ""
            if s.kind == "patch" and s.replay is not None:
                tail = "→ok" if s.replay.ok else f"→{s.replay.failing_cap}"
            parts.append(f"{i+1}.{s.summary}{tail}")
        return " | ".join(parts)
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/envstate/test_repair_session.py -v` → passed.
- [ ] **Step 5: Commit** — `git add src/envstate/repair_session.py tests/envstate/test_repair_session.py && git commit -m "feat(arm-c): RepairSession notebook"`

---

### Task 3: `made_progress` (the single progress rule)

**Files:**
- Modify: `src/envstate/repair_session.py` (append `made_progress`)
- Test: `tests/envstate/test_repair_session.py` (append)

**Interfaces:**
- Consumes: `RepairSession`, `ReplayResult`.
- Produces: `made_progress(session, result: ReplayResult) -> bool`.

- [ ] **Step 1: Write the failing test**
```python
# append to tests/envstate/test_repair_session.py
from src.envstate.repair_session import made_progress

def test_progress_true_when_missing_cap_changes():
    s = RepairSession("pkg:p", "libpq")
    s.steps.append(Step("patch", "add libpq", cap="libpq",
                        replay=ReplayResult(False, "pkg:p", "libpq", "c", "")))
    assert made_progress(s, ReplayResult(False, "pkg:p", "pg_config", "c", "")) is True

def test_progress_false_when_signature_unchanged():
    s = RepairSession("pkg:p", "libx")
    s.steps.append(Step("patch", "add dummy", cap="libx",
                        replay=ReplayResult(False, "pkg:p", "libx", "c", "")))
    assert made_progress(s, ReplayResult(False, "pkg:p", "libx", "c", "")) is False

def test_progress_true_on_resolution():
    s = RepairSession("pkg:p", "libx")
    assert made_progress(s, ReplayResult(True)) is True
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/envstate/test_repair_session.py -k progress -v` → FAIL.

- [ ] **Step 3: Implement** (append to `src/envstate/repair_session.py`)
```python
def made_progress(session: RepairSession, result: ReplayResult) -> bool:
    """Spec §5.4: progress iff resolved, or the missing capability changed vs the last
    patch's replay. (Certified-delta and block-moved are subsumed by cap-change in the
    fake world; the production adapter feeds the same signal.)"""
    if result.ok:
        return True
    last = next((s for s in reversed(session.steps)
                 if s.kind == "patch" and s.replay is not None), None)
    if last is None:
        return True
    return result.failing_cap != last.replay.failing_cap
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/envstate/test_repair_session.py -k progress -v` → passed.
- [ ] **Step 5: Commit** — `git commit -am "feat(arm-c): structured progress rule"`

---

### Task 4: `persist_session_to_attempts` (spec §13.2)

**Files:**
- Modify: `src/envstate/repair_session.py`
- Test: `tests/envstate/test_repair_session.py`

**Interfaces:**
- Consumes: `DepGraph`, `RepairSession`, node id, `Attempt` (from schema).
- Produces: `persist_session_to_attempts(graph, session, node_id) -> DepGraph`.

- [ ] **Step 1: Write the failing test**
```python
# append to tests/envstate/test_repair_session.py
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from src.envstate.repair_session import persist_session_to_attempts

def test_patch_steps_land_on_attempts_axis():
    g = DepGraph().with_node(Node(id="pkg:p", type=NodeType.PACKAGE, name="p", layer=Layer.PIP,
                                  discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING, version="1"))
    s = RepairSession("pkg:p", "libx")
    s.steps.append(Step("probe", "probe:x", cap="libx"))
    s.steps.append(Step("patch", "add libx", cap="libx", accepted=True,
                        replay=ReplayResult(True)))
    g2 = persist_session_to_attempts(g, s, "pkg:p")
    attempts = g2.get("pkg:p").attempts
    assert len(attempts) == 1 and attempts[0].outcome == "succeeded"
    assert attempts[0].check == "repair_session"
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement** (append)
```python
from python_deps.depgraph.schema import Attempt

def persist_session_to_attempts(graph, session: RepairSession, node_id: str):
    """Spec §13.2: fold each PATCH step onto the target node's attempts axis (durable,
    to_dict-serialized). Probes are investigation, not attempts — skipped."""
    node = graph.get(node_id)
    if node is None:
        return graph
    for s in session.steps:
        if s.kind != "patch":
            continue
        outcome = "succeeded" if (s.replay and s.replay.ok) else "failed"
        node = node.with_attempt(Attempt(command=s.summary, outcome=outcome,
                                         check="repair_session", cycle=len(node.attempts)))
    return graph.with_node(node)
```

- [ ] **Step 4: Run to verify it passes** — passed.
- [ ] **Step 5: Commit** — `git commit -am "feat(arm-c): persist session transcript to attempts axis"`

---

### Task 5: `fix_one_error` (the per-error session loop)

**Files:**
- Create: `src/envstate/repair_fix.py`
- Test: `tests/envstate/test_repair_fix.py`

**Interfaces:**
- Consumes: `RepairSession`, `made_progress`, `persist_session_to_attempts`, `admit_proposal` (real gate), injected `agent`/`replay`/`certify`/`log`.
- Produces: `fix_one_error(graph, error, *, agent, replay, certify, log, stall_limit=2, turn_cap=15) -> (graph, "resolved"|"stalled")`.

- [ ] **Step 1: Write the failing test** (uses a tiny fake agent + FakeWorld)
```python
# tests/envstate/test_repair_fix.py
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy, Edge, EdgeType
from python_deps.depgraph.patch import PatchProposal, NodeSpec, ProviderSpec, EdgeSpec
from src.envstate.repair_fix import fix_one_error
from src.envstate.repair_types import ReplayResult
from src.eval.repair_arm_eval.mock_world import FakeWorld, RealNode

class _NullLog:
    def d(self, *a, **k): pass

class _OneShotAgent:
    """Emits one patch that adds the missing syslib, then nothing."""
    def next_action(self, session, failure, log):
        p = PatchProposal(
            add_requirements=(NodeSpec(id="syslib:ffi", type="SystemLib", name="ffi",
                layer="system", check_command="ldconfig -p | grep -q libffi", evidence_ref="ev.1"),),
            add_providers=(ProviderSpec(id="apt:libffi-dev", kind="apt",
                command="apt-get install -y libffi-dev", provides=("syslib:ffi",)),),
            add_edges=(EdgeSpec(source="pkg:cryptography", target="syslib:ffi", hard=True),))
        return ("patch", p, "ffi")

def test_fix_one_error_resolves():
    reality = {"pkg:cryptography": RealNode("cryptography", frozenset({"ffi"}), "python -c 'import cryptography'"),
               "syslib:ffi": RealNode("ffi", frozenset(), "ldconfig -p | grep -q libffi")}
    world = FakeWorld(reality)
    g = DepGraph().with_node(Node(id="pkg:cryptography", type=NodeType.PACKAGE, name="cryptography",
        layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING, version="1"))
    err = world.replay_from_base(g)
    g2, outcome = fix_one_error(g, err, agent=_OneShotAgent(),
        replay=lambda gr, mb=(): world.replay_from_base(gr),
        certify=world.certify, log=_NullLog())
    assert outcome == "resolved"
    assert g2.get("syslib:ffi") is not None
    assert g2.get("pkg:cryptography").attempts  # persisted
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement**
```python
# src/envstate/repair_fix.py
"""fix_one_error — the sustained per-error session loop (spec §5). One conversation per
error; the agent SEES its own patch history; only a host check flips state; typed patches
only. Injected agent/replay/certify keep it Docker-free and unit-testable."""
from __future__ import annotations

from python_deps.depgraph.patch_gate import admit_proposal
from python_deps.depgraph.schema import State
from src.envstate.repair_session import (
    RepairSession, Step, made_progress, persist_session_to_attempts,
)

EVIDENCE = frozenset({"ev.1"})   # eval evidence id; production supplies real evidence ids

def fix_one_error(graph, error, *, agent, replay, certify, log,
                  stall_limit: int = 2, turn_cap: int = 15):
    session = RepairSession(error.failing_node, error.failing_cap)
    log.d("SESSION_START", f"error_key=({error.failing_node}, {error.failing_cap})")
    no_progress = 0
    current = error
    while len(session.steps) < turn_cap:
        act = agent.next_action(session, current, log)
        if act[0] == "probe":
            _, cmd, cap = act
            session.steps.append(Step("probe", f"probe:{cmd}", cap=cap))
            log.d("SESSION_PROBE", f"{cmd} (read-only, no mutation)")
            continue
        _, patch, cap = act
        admit = admit_proposal(graph, patch, known_evidence_ids=EVIDENCE)
        added = [r.id for r in patch.add_requirements]
        log.d("GATE", f"add={added} accepted={admit.accepted} errs={list(admit.errors)}")
        if not admit.accepted:
            session.steps.append(Step("patch", f"REJECTED:{added}", cap=cap, accepted=False))
            no_progress += 1
            if no_progress >= stall_limit:
                log.d("SESSION_STALL", f"{no_progress} rejects — give up {error.failing_node}")
                return persist_session_to_attempts(graph, session, error.failing_node), "stalled"
            continue
        graph = admit.graph
        before = {n.id for n in graph.nodes if n.state is State.SATISFIED}
        result = replay(graph, admit.manual_blocks)
        log.d("CLEAN_REPLAY", f"from base → {'OK' if result.ok else f'FAIL {result.failing_cap}'}")
        graph = certify(graph)
        newly = sorted({n.id for n in graph.nodes if n.state is State.SATISFIED} - before)
        log.d("HOST_CERTIFY", f"newly SATISFIED: {newly or '∅'}")
        prog = made_progress(session, result)
        session.steps.append(Step("patch", f"add:{added}", cap=cap, accepted=True,
                                  replay=result, progress=prog))
        log.d("SESSION_PATCH", f"applied {added}; replay {'green' if result.ok else result.failing_cap}")
        log.d("PROGRESS", f"progress={prog}")
        if result.ok or result.failing_node != error.failing_node:
            log.d("SESSION_RESOLVED", f"{error.failing_node} past seed error")
            graph = persist_session_to_attempts(graph, session, error.failing_node)
            log.d("ATTEMPTS_PERSIST", f"{sum(1 for s in session.steps if s.kind=='patch')} "
                                      f"patch-steps → {error.failing_node}.attempts")
            return graph, "resolved"
        current = result
        no_progress = 0 if prog else no_progress + 1
        if no_progress >= stall_limit:
            log.d("SESSION_STALL", f"{no_progress} no-progress — give up {error.failing_node}")
            return persist_session_to_attempts(graph, session, error.failing_node), "stalled"
    log.d("SESSION_STALL", f"turn cap {turn_cap} hit")
    return persist_session_to_attempts(graph, session, error.failing_node), "stalled"
```

- [ ] **Step 4: Run to verify it passes** — passed.
- [ ] **Step 5: Commit** — `git add src/envstate/repair_fix.py tests/envstate/test_repair_fix.py && git commit -m "feat(arm-c): fix_one_error sustained session loop"`

---

### Task 6: `run_repair_arm` (error-loop driver + global termination)

**Files:**
- Create: `src/envstate/repair_arm.py`
- Test: `tests/envstate/test_repair_arm.py`

**Interfaces:**
- Consumes: `fix_one_error`, injected `replay`/`certify`/`agent`/`log`/`localize`/`diagnose`.
- Produces: `run_repair_arm(graph, *, replay, certify, agent, log, localize=None, diagnose=None, max_errors=20) -> ("DONE"|"GIVEUP", graph)`.

- [ ] **Step 1: Write the failing test**
```python
# tests/envstate/test_repair_arm.py — a stall scenario must GIVEUP, a solvable must DONE
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from src.envstate.repair_arm import run_repair_arm
from src.eval.repair_arm_eval.mock_world import FakeWorld, RealNode

class _NullLog:
    def d(self, *a, **k): pass

class _NoFixAgent:  # can never fix -> stall -> giveup
    def next_action(self, session, failure, log):
        from python_deps.depgraph.patch import PatchProposal, NodeSpec, ProviderSpec
        nid = f"syslib:dummy-{failure.failing_cap}"
        p = PatchProposal(
            add_requirements=(NodeSpec(id=nid, type="SystemLib", name="d", layer="system",
                check_command=f"ldconfig -p | grep -q d{failure.failing_cap}", evidence_ref="ev.1"),),
            add_providers=(ProviderSpec(id="apt:dummy", kind="apt",
                command="apt-get install -y dummy", provides=(nid,)),))
        return ("patch", p, failure.failing_cap)

def test_unfixable_gives_up():
    reality = {"pkg:m": RealNode("m", frozenset({"libz"}), "python -c 'import m'")}
    world = FakeWorld(reality)
    g = DepGraph().with_node(Node(id="pkg:m", type=NodeType.PACKAGE, name="m", layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING, version="1"))
    outcome, _ = run_repair_arm(g, replay=lambda gr, mb=(): world.replay_from_base(gr),
        certify=world.certify, agent=_NoFixAgent(), log=_NullLog())
    assert outcome == "GIVEUP"
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement**
```python
# src/envstate/repair_arm.py
"""run_repair_arm — the arm-C error loop (spec §4-5.1). Render → clean replay → host
certify → localize → route → fix_one_error → repeat. The build script's execution order
IS the schedule (no separate scheduler). Global termination: DONE (green) / GIVEUP."""
from __future__ import annotations
from src.envstate.repair_fix import fix_one_error

def _default_localize(result):
    return result                                   # the failing ReplayResult IS the error

def _default_diagnose(error):
    return "ENVIRONMENT"                            # production injects the real DiagnosisRouter

def run_repair_arm(graph, *, replay, certify, agent, log,
                   localize=None, diagnose=None, max_errors: int = 20):
    localize = localize or _default_localize
    diagnose = diagnose or _default_diagnose
    stuck: dict[str, int] = {}
    for _ in range(max_errors):
        log.d("RENDER", f"rendered script from {len(graph.nodes)} graph nodes")
        result = replay(graph, ())
        graph = certify(graph)
        if result.ok:
            log.d("DONE", "clean replay green — build works")
            return "DONE", graph
        error = localize(result)
        log.d("LOCALIZE", f"first failure at {error.failing_node} (missing {error.failing_cap})")
        route = diagnose(error)
        log.d("DIAGNOSE", f"route={route}")
        if route != "ENVIRONMENT":
            stuck[error.failing_node] = stuck.get(error.failing_node, 0) + 1
            if stuck[error.failing_node] >= 2:
                log.d("GIVEUP", f"non-env error at {error.failing_node} — give up")
                return "GIVEUP", graph
            continue
        graph, outcome = fix_one_error(graph, error, agent=agent, replay=replay,
                                       certify=certify, log=log)
        if outcome == "stalled":
            k = error.failing_node
            stuck[k] = stuck.get(k, 0) + 1
            if stuck[k] >= 2:
                log.d("GIVEUP", f"same error at {k} unrepaired — honest give-up")
                return "GIVEUP", graph
    return "GIVEUP", graph
```

- [ ] **Step 4: Run to verify it passes** — passed.
- [ ] **Step 5: Commit** — `git add src/envstate/repair_arm.py tests/envstate/test_repair_arm.py && git commit -m "feat(arm-c): run_repair_arm error-loop driver"`

---

### Task 7: `DesignLog` (design-point logger)

**Files:**
- Create: `src/eval/repair_arm_eval/design_log.py`
- Test: `tests/eval/test_design_log.py`

**Interfaces:**
- Produces: `DesignLog()` with `.d(tag, msg)`, `.count(tag)`, `.events`; module constant `DESIGN` (tag → spec-guarantee string).

- [ ] **Step 1: Write the failing test**
```python
# tests/eval/test_design_log.py
from src.eval.repair_arm_eval.design_log import DesignLog, DESIGN
def test_records_and_counts():
    log = DesignLog(silent=True)
    log.d("HOST_CERTIFY", "x"); log.d("HOST_CERTIFY", "y")
    assert log.count("HOST_CERTIFY") == 2
    assert "HOST_CERTIFY" in DESIGN
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement** — copy the `DESIGN` dict + `DesignLog` from `scratchpad/repair_arm_eval.py`, add a `silent=False` flag that suppresses printing (for tests). Each `.d()` appends `(tag, msg)` to `.events` and, unless silent, prints `[DESIGN:<tag>] <msg>` plus the guarantee line.

- [ ] **Step 4: Run to verify it passes** — passed.
- [ ] **Step 5: Commit** — `git add src/eval/repair_arm_eval/design_log.py tests/eval/test_design_log.py && git commit -m "feat(arm-c): design-point logger"`

---

### Task 8: Scripted agent + scenarios

**Files:**
- Create: `src/eval/repair_arm_eval/scripted_agent.py`
- Create: `src/eval/repair_arm_eval/scenarios.py`
- Test: `tests/eval/test_scenarios.py`

**Interfaces:**
- Consumes: `PatchProposal`/`NodeSpec`/`ProviderSpec`/`EdgeSpec`, `FakeWorld`, `RealNode`.
- Produces: `ScriptedSolver(cap_to_fix)` with `.next_action(session, failure, log)`; `Fix(probe, patch)`; `scenario_simple()/scenario_chain()/scenario_stall()` each returning `(graph, FakeWorld, ScriptedSolver)`.

- [ ] **Step 1: Write the failing test**
```python
# tests/eval/test_scenarios.py
from src.eval.repair_arm_eval.scenarios import scenario_chain, scenario_stall
def test_chain_shape():
    g, world, agent = scenario_chain()
    assert g.get("pkg:psycopg2") is not None
    r = world.replay_from_base(g)
    assert not r.ok and r.failing_cap == "libpq"   # first failure is libpq
def test_stall_has_no_fix():
    g, world, agent = scenario_stall()
    assert agent.cap_to_fix == {}
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement** — promote `ScriptedSolver`, `Fix`, the `_syslib_patch`/`_tool_patch`/`_noop_patch` builders, `_base_graph`, and `scenario_simple`/`scenario_chain`/`scenario_stall` verbatim from `scratchpad/repair_arm_eval.py` (proven green). Ensure `ScriptedSolver.next_action` returns 3-tuples in every branch (the fixed `_noop_patch` path returns `("patch", _noop_patch(cap), cap)`).

- [ ] **Step 4: Run to verify it passes** — passed.
- [ ] **Step 5: Commit** — `git add src/eval/repair_arm_eval/scripted_agent.py src/eval/repair_arm_eval/scenarios.py tests/eval/test_scenarios.py && git commit -m "feat(arm-c): eval scripted agent + scenarios"`

---

### Task 9: End-to-end mechanics eval (the whole point)

**Files:**
- Create: `src/eval/repair_arm_eval/run_eval.py`
- Test: `tests/eval/test_repair_arm_eval.py`

**Interfaces:**
- Consumes: `run_repair_arm`, `DesignLog`, `scenario_*`.
- Produces: `run_one(name, expect, factory, silent=False) -> (ok: bool, fired: set)`; `main()` (CLI) printing the design log + coverage report.

- [ ] **Step 1: Write the failing test**
```python
# tests/eval/test_repair_arm_eval.py
from src.eval.repair_arm_eval.run_eval import run_one
from src.eval.repair_arm_eval.scenarios import scenario_simple, scenario_chain, scenario_stall
from src.eval.repair_arm_eval.design_log import DESIGN

def test_all_scenarios_reach_expected_outcome():
    assert run_one("simple", "DONE", scenario_simple, silent=True)[0]
    assert run_one("chain", "DONE", scenario_chain, silent=True)[0]
    assert run_one("stall", "GIVEUP", scenario_stall, silent=True)[0]

def test_full_design_point_coverage():
    fired = set()
    for exp, fac in [("DONE", scenario_simple), ("DONE", scenario_chain), ("GIVEUP", scenario_stall)]:
        fired |= run_one("s", exp, fac, silent=True)[1]
    missing = set(DESIGN) - fired
    assert not missing, f"design-points never exercised: {sorted(missing)}"
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement** `run_one` — build `(graph, world, agent)` from the factory, make a `DesignLog(silent=silent)`, call `run_repair_arm(graph, replay=lambda gr, mb=(): world.replay_from_base(gr), certify=world.certify, agent=agent, log=log)`, return `(outcome == expect, {t for t,_ in log.events})`. `main()` loops the three scenarios, prints per-scenario result + the cross-scenario coverage report (mirroring the spike's summary), `sys.exit(0 if all pass else 1)`.

- [ ] **Step 4: Run to verify it passes** — `pytest tests/eval/test_repair_arm_eval.py -v` → passed; and `python3 -m src.eval.repair_arm_eval.run_eval` prints the tagged design log and "full coverage".

- [ ] **Step 5: Commit** — `git add src/eval/repair_arm_eval/run_eval.py tests/eval/test_repair_arm_eval.py && git commit -m "feat(arm-c): e2e mechanics eval with design-point coverage"`

---

### Task 10: `SessionAgent` — the LLM port (sustained conversation)

**Files:**
- Create: `src/envstate/session_agent.py`
- Test: `tests/envstate/test_session_agent.py`

**Interfaces:**
- Consumes: `RepairSession.render_for_agent`, `render_repair_scope`/`PATCH_SCHEMA_HINT` (reuse from `repair_scope.py`), `parse_patch_proposal`, an OpenAI-compatible `client`.
- Produces: `SessionAgent(client, model)` with `.next_action(session, failure, log) -> Action`. It builds messages as `[system, seed] + rendered-history-so-far`, calls the LLM once, and parses the reply into a PROBE (a read-only `Action:` line) or a PATCH (a fenced JSON `PatchProposal`). Unlike `V3BuildAgent.propose`, the message history is derived from the SESSION each turn (compounding memory), not reset per patch.

- [ ] **Step 1: Write the failing test** (fake client returns a canned patch JSON; assert PATCH parsed and that the prompt includes prior-step history)
```python
# tests/envstate/test_session_agent.py
from src.envstate.repair_session import RepairSession, Step
from src.envstate.repair_types import ReplayResult
from src.envstate.session_agent import SessionAgent

class _FakeClient:
    def __init__(self, reply): self.reply = reply; self.last_messages = None
    # mimic the minimal surface complete_with_retry uses (adapt to real client in impl)

def test_patch_parsed_and_history_in_prompt(monkeypatch):
    import src.envstate.session_agent as sa
    captured = {}
    def fake_complete(client, model, messages, **k):
        captured["messages"] = messages
        return ('```json\n{"patch":{"add_requirements":[{"id":"syslib:ffi","type":"SystemLib",'
                '"name":"ffi","layer":"system","check_command":"ldconfig -p | grep -q libffi",'
                '"evidence_ref":"ev.1"}]}}\n```', {}, "raw")
    monkeypatch.setattr(sa, "complete_with_retry", fake_complete)
    s = RepairSession("pkg:cryptography", "ffi")
    s.steps.append(Step("patch", "add:['syslib:x']", cap="x", replay=ReplayResult(False,"pkg:cryptography","ffi","c","")))
    agent = SessionAgent(client=object(), model="m")
    kind, patch, cap = agent.next_action(s, ReplayResult(False,"pkg:cryptography","ffi","c",""), log=_L())
    assert kind == "patch" and patch.add_requirements[0].id == "syslib:ffi"
    assert "syslib:x" in "".join(m["content"] for m in captured["messages"])  # history present

class _L:
    def d(self,*a,**k): pass
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement** — model on `V3BuildAgent.propose` (v3_build_agent.py:148) but: build the user message from `render_repair_scope`-style seed **plus** `session.render_for_agent()` (the notebook), append `PATCH_SCHEMA_HINT`, call `complete_with_retry(client, model, messages, temperature=0, stop=["Observation:"])`, then: if the reply contains a fenced JSON → `parse_patch_proposal` → `("patch", proposal, failure.failing_cap)`; elif it contains `Action: <cmd>` and `is_read_only(cmd)` → `("probe", cmd, failure.failing_cap)`; else re-prompt once, else fall through to a no-op patch signal the loop treats as a stall step. Reuse `is_read_only` from `patch_gate`.

- [ ] **Step 4: Run to verify it passes** — passed.
- [ ] **Step 5: Commit** — `git add src/envstate/session_agent.py tests/envstate/test_session_agent.py && git commit -m "feat(arm-c): SessionAgent LLM port with sustained memory"`

---

### Task 11: Wire as a new arm (loop_mode + production replay/certify adapters)

**Files:**
- Create: `src/envstate/repair_arm_entry.py`
- Modify: `src/envstate/run_trace.py` (add `loop_mode` value, no behavior change to existing arms)
- Test: `tests/envstate/test_repair_arm_entry.py`

**Interfaces:**
- Consumes: `run_repair_arm`, `SessionAgent`, `render_build_script`, `reset_to_base`/`run_install_script`, `certify_all`, `localize_install_failure`, `DiagnosisRouter`.
- Produces: `run_v3_session(graph, sandbox, client, model, ...) -> ("DONE"|"GIVEUP", graph)` — the production entry. It builds the `replay` adapter (`render_build_script` → `reset_to_base` → `run_install_script` → normalize `InstallResult` into `ReplayResult` via `localize_install_failure`), the `certify` adapter (`certify_all` with the sandbox executor), the real `diagnose` (DiagnosisRouter), and a `SessionAgent`, then calls `run_repair_arm`. Tags `RunTrace.loop_mode="v3_session_repair"`.

- [ ] **Step 1: Write the failing test** — inject fakes for `sandbox`/`client` (reuse `FakeWorld` for replay/certify via small adapters); assert `run_v3_session` reaches `DONE` on the chain scenario and that `loop_mode` is set. (Adapter-level test; the deep e2e is the mechanics eval.)

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement** the two adapters (replay: render→reset→install→`ReplayResult`; certify: `certify_all`) and the `run_v3_session` entry that assembles them + `SessionAgent` + real `diagnose`/`localize` and calls `run_repair_arm`. Add `"v3_session_repair"` as a recognized `loop_mode`. Do NOT modify `run_v1`/`run_v3`/`trace_verify` behavior.

- [ ] **Step 4: Run to verify it passes** — passed; run the full suite `pytest tests/envstate tests/eval -q` → all green.

- [ ] **Step 5: Commit** — `git add src/envstate/repair_arm_entry.py src/envstate/run_trace.py tests/envstate/test_repair_arm_entry.py && git commit -m "feat(arm-c): production arm entry run_v3_session (new arm, no cutover)"`

---

## Deferred (explicitly NOT in this plan)

- **A/B comparison eval** (arm-B cold-agent head-to-head, ambiguous-error scenario). Deferred per user direction.
- **Real-LLM intelligence eval** (SessionAgent vs a cold agent on memory-demanding scenarios).
- **Cutover / promotion** (retiring `run_structured_repair`, making arm C canonical). This plan ships arm C as a *new arm only*; promotion is a later, gated step.
- **Incremental-execution optimization** (spec §N2) — deliberately excluded.

## Self-Review

- **Spec coverage:** §4 error loop → Task 6; §5.2 session → Tasks 2-4; §5.3 agent contract → Tasks 5,8,10; §5.4 progress/termination → Tasks 3,5,6; §7 preserved guarantees → reused unmodified (Tasks 1,5); §13.1 follow-forward → Task 5 (`failing_node != seed` resolution); §13.2 attempts persistence → Task 4; §13.3 turn cap 15 → Task 5 default; §11 new-arm migration → Task 11. Design-point coverage asserted in Task 9.
- **Placeholders:** none — every task has concrete test + implementation code or an explicit "promote verbatim from the proven spike" instruction with the exact source.
- **Type consistency:** `ReplayResult`, `Step`, `RepairSession`, `fix_one_error`, `run_repair_arm`, and the `agent.next_action` Action tuple use identical signatures across Tasks 1-11 (locked in the File Structure contract block).
