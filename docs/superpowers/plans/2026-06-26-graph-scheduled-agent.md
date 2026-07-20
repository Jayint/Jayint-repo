# Graph-Scheduled Agent Architecture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dependency graph schedule a bounded LLM executor — the graph picks the next actionable obligation (what & when), the agent satisfies it one at a time (how), and the host certifies (whether) — behind a default-off flag, byte-identical when off.

**Architecture:** A new pure scheduling layer (`schedule.py`) selects and frames actionable `MISSING` obligations from the live `DepGraph`; a thin envstate adapter (`graph_scheduler.py`) turns the next obligation into a `PlannerDecision(action="task")`; `run_v1` gates the LLM `planner.decide` call behind `enable_graph_scheduler` and routes to the scheduler instead; `BuildAgent.run` gains a host-`check` stop condition so the agent never self-declares done. The strategic LLM `Planner` is deprecated-and-retained (marked dormant, routed around, **not deleted**).

**Tech Stack:** Python 3, pytest, frozen dataclasses, the existing `src/python_deps/depgraph/` (pure graph layer) and `src/envstate/` (orchestrator layer).

**Design spec:** `docs/superpowers/specs/2026-06-26-graph-scheduled-agent-architecture-design.md`

> **Plan revised after a 5-agent review** (api-grounding, architecture, spec-coverage, tests, integration-risk). Verified facts baked in below: the real constructor is `DepGraph()` (no `.empty()`); the LLM call is the module-level `complete_with_retry(self.client, ...)` (no `_ask_llm`); the run accumulator is `history`, the done-predicate `_is_worker_finished(text)`; `_dep_emit_phase` runs `certify_refresh` then `emit_drain`, so the scheduler keeps certify and suppresses only the drain. `topo_order` returns `tuple[Node, ...]`.

## Global Constraints

These bind every task. Copy them into each reviewer prompt.

- **NO COMMITS. No `git add`. Leave the working tree dirty.** Every task's final step is "run tests, verify green; do NOT commit or stage." This overrides the writing-plans default "Commit" step everywhere.
- **`python3 -m pytest`** for all test runs.
- **Default OFF, byte-identical when off.** The new flag `enable_graph_scheduler` defaults `False`. With it off, `run_v1` calls `planner.decide` exactly as today, `_dep_emit_phase` runs the emit drain exactly as today, and `BuildAgent.run` behaves exactly as today (`check=None`). Existing tests and the `v1`/`v1g`/`v1gde`/`v1gder` arms are byte-for-byte unchanged.
- **Certify yes, deterministic drain no.** Under `enable_graph_scheduler`, `_dep_emit_phase` still runs `certify_refresh` (the CERTIFY role — this is what flips `UNKNOWN`→`MISSING`/`SATISFIED` and populates the frontier) but MUST skip `emit_drain` (the deterministic auto-fix tier the spec excludes from v1). The agent replaces the drain.
- **Pure graph layer stays pure.** `src/python_deps/depgraph/schedule.py` MUST NOT import anything from `src.envstate`. It depends only on the depgraph schema + emit helpers. The `Task`/`PlannerDecision` conversion lives in the envstate layer (`graph_scheduler.py`).
- **Graph decides what/when; agent decides how; host decides whether; Maintainer is the single graph-writer.** No new code writes graph state directly outside the existing certify/Maintainer paths.
- **Experiments, not facts.** The agent path (`BuildAgent.run`) takes no graph and writes none. A scheduler obligation becomes `SATISFIED` solely because the host `certify_refresh` re-ran its `check_command`. Runtime ingest appends `state=UNKNOWN` only (unchanged).
- **Host check is the only done-signal in scheduler mode.** When `BuildAgent.run` is given a `check`, the LLM emitting "Final Answer: Success" does NOT finalize — only the host `check` passing does.
- **Immutability.** `DepGraph`, `Node`, `Edge`, `Task`, `PlannerDecision` are all `@dataclass(frozen=True)`. Produce new objects via `dataclasses.replace` / `graph.with_node` / `graph.with_edge`. Never mutate in place.
- **Deprecate-retain the Planner.** Mark `src/envstate/planner.py` dormant with a module note; do NOT delete it or remove its tests.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/python_deps/depgraph/schedule.py` | **Create** | Pure: `ObligationPacket`, `scheduler_frontier(graph)`, `frame_obligation(graph, node)`. |
| `tests/depgraph/test_scheduler_frontier.py` | **Create** | Unit tests for `scheduler_frontier`. |
| `tests/depgraph/test_obligation_framing.py` | **Create** | Unit tests for `frame_obligation`. |
| `src/envstate/build_agent.py` | **Modify** | Add `check: str | None = None` to `run` — host-check stop. |
| `tests/test_build_agent_work_mode.py` | **Create** | Unit tests for the `check`-gated stop + the graph-blind invariant. |
| `src/envstate/graph_scheduler.py` | **Create** | `packet_to_task`, `next_decision(graph, run_tests, handed, attempt_cap)`. |
| `tests/test_graph_scheduler_decision.py` | **Create** | Unit tests for `next_decision` / `packet_to_task` (RED before orchestrator wiring). |
| `src/envstate/orchestrator.py` | **Modify** | `enable_graph_scheduler`; gate `planner.decide`; suppress drain; oscillation + stuck counters; pass `check`. |
| `tests/test_graph_scheduler_wiring.py` | **Create** | Orchestrator wiring intents (flag off→planner; on→scheduler; drain suppressed). |
| `src/envstate/planner.py` | **Modify** | Module-level deprecation note (no behavior change). |
| `agent.py` | **Modify** | Flag derivation + ctor param + argparse + forward into `run_v1`. |
| `run_rat_benchmark.py` | **Modify** | `v1gs` arm + arm ladder + env-var override. |
| `run_repo2run_benchmark.py` | **Modify** | `_ARM_PRESETS["v1gs"]` + subprocess forward. |
| `multi_docker_eval_adapter.py` | **Modify** | Read + forward `DOCKERAGENT_ENABLE_GRAPH_SCHEDULER`. |
| `tests/test_graph_scheduler_flag.py` | **Create** | Flag-plumbing smoke (implication chain). |

---

## Task 1: Pure scheduler frontier

**Files:**
- Create: `src/python_deps/depgraph/schedule.py`
- Test: `tests/depgraph/test_scheduler_frontier.py`

**Interfaces:**
- Consumes: `DepGraph`, `Node`, `Edge`, `State`, `EdgeType`, `NodeType`, `Layer`, `DiscoveredBy` from `src/python_deps/depgraph/schema.py`; `topo_order` from `src/python_deps/depgraph/emit.py` (returns `tuple[Node, ...]`).
- Produces: `scheduler_frontier(graph: DepGraph) -> tuple[Node, ...]` — `MISSING` obligations with a host `check_command`, every dependency `SATISFIED`, topologically ordered deps-before-dependents. SERVICE nodes are excluded (routed through the sufficiency-stuck branch in v1, since certify hard-skips SERVICE).

**Before writing:** confirm read accessors in `schema.py` — expected `graph.nodes`, `graph.edges`, `graph.get(node_id)`, and that `Edge` has a field named `relation`. The constructor is `DepGraph()` (there is no `.empty()`). `Node.tier` is auto-derived from `type`, so test constructors may omit it.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_scheduler_frontier.py
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, Edge, NodeType, Layer, State, EdgeType, DiscoveredBy,
)
from python_deps.depgraph.schedule import scheduler_frontier  # noqa: E402


def _node(nid, ntype, name, state, *, check="true", layer=Layer.PIP):
    return Node(
        id=nid, type=ntype, name=name, layer=layer,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=state, check_command=check,
    )


def test_missing_node_with_check_is_actionable():
    g = DepGraph().with_node(_node("pkg:requests", NodeType.PACKAGE, "requests", State.MISSING))
    assert [n.id for n in scheduler_frontier(g)] == ["pkg:requests"]


def test_satisfied_and_unknown_are_excluded():
    g = (
        DepGraph()
        .with_node(_node("pkg:a", NodeType.PACKAGE, "a", State.SATISFIED))
        .with_node(_node("pkg:b", NodeType.PACKAGE, "b", State.UNKNOWN))
    )
    assert scheduler_frontier(g) == ()


def test_missing_without_check_command_is_excluded():
    g = DepGraph().with_node(_node("pkg:c", NodeType.PACKAGE, "c", State.MISSING, check=None))
    assert scheduler_frontier(g) == ()


def test_node_with_unsatisfied_dependency_is_held_back():
    g = (
        DepGraph()
        .with_node(_node("syslib:libpq", NodeType.SYSTEM_LIB, "libpq", State.MISSING, layer=Layer.SYSTEM))
        .with_node(_node("pkg:app", NodeType.PACKAGE, "app", State.MISSING))
        .with_edge(Edge(src="pkg:app", dst="syslib:libpq", relation=EdgeType.REQUIRES))
    )
    assert [n.id for n in scheduler_frontier(g)] == ["syslib:libpq"]


def test_dependencies_ordered_before_dependents():
    g = (
        DepGraph()
        .with_node(_node("syslib:libpq", NodeType.SYSTEM_LIB, "libpq", State.SATISFIED, layer=Layer.SYSTEM))
        .with_node(_node("pkg:psycopg2", NodeType.PACKAGE, "psycopg2", State.MISSING))
        .with_node(_node("pkg:app", NodeType.PACKAGE, "app", State.MISSING))
        .with_edge(Edge(src="pkg:app", dst="pkg:psycopg2", relation=EdgeType.REQUIRES))
    )
    front = [n.id for n in scheduler_frontier(g)]
    assert front.index("pkg:psycopg2") < front.index("pkg:app")


def test_diamond_orders_root_first():
    # d depends on b and c; b and c depend on a → a before b,c before d
    g = DepGraph()
    for nid in ("a", "b", "c", "d"):
        g = g.with_node(_node(f"pkg:{nid}", NodeType.PACKAGE, nid, State.MISSING))
    g = (
        g.with_edge(Edge(src="pkg:b", dst="pkg:a", relation=EdgeType.REQUIRES))
        .with_edge(Edge(src="pkg:c", dst="pkg:a", relation=EdgeType.REQUIRES))
        .with_edge(Edge(src="pkg:d", dst="pkg:b", relation=EdgeType.REQUIRES))
        .with_edge(Edge(src="pkg:d", dst="pkg:c", relation=EdgeType.REQUIRES))
    )
    front = [n.id for n in scheduler_frontier(g)]
    # all deps are MISSING so only the root is actionable this pass
    assert front == ["pkg:a"]


def test_service_nodes_excluded():
    g = DepGraph().with_node(
        _node("service:postgres", NodeType.SERVICE, "postgres", State.MISSING, layer=Layer.SERVICES)
    )
    assert scheduler_frontier(g) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_scheduler_frontier.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.schedule'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/schedule.py
"""Pure scheduling layer: select and frame the next actionable obligation.

The DECIDE role's "what & when". Given a host-certified DepGraph, pick the MISSING
obligations whose every dependency is already SATISFIED and that carry a host
check_command (the agent's stop condition), ordered deps-before-dependents.

PURE: must not import from src.envstate. Depends only on the depgraph schema and
emit helpers.
"""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.schema import DepGraph, Node, NodeType, State, EdgeType
from python_deps.depgraph.emit import topo_order


def _dependencies_satisfied(graph: DepGraph, node: Node) -> bool:
    """True when every node this one REQUIRES is SATISFIED."""
    for edge in graph.edges:
        if edge.src == node.id and edge.relation is EdgeType.REQUIRES:
            dep = graph.get(edge.dst)
            if dep is None or dep.state is not State.SATISFIED:
                return False
    return True


def _is_actionable(graph: DepGraph, node: Node) -> bool:
    return (
        node.state is State.MISSING
        and node.type is not NodeType.SERVICE     # services flow through the sufficiency branch in v1
        and bool(node.check_command)              # the agent needs a host stop condition
        and _dependencies_satisfied(graph, node)
    )


def scheduler_frontier(graph: DepGraph) -> tuple[Node, ...]:
    """Actionable MISSING obligations, topologically ordered (deps first)."""
    actionable = [n for n in graph.nodes if _is_actionable(graph, n)]
    if not actionable:
        return ()
    return tuple(topo_order(graph, tuple(actionable)))   # topo_order returns tuple[Node, ...]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_scheduler_frontier.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Verify, do NOT commit**

Run: `python3 -m pytest tests/depgraph/ -q`. **Do NOT commit or `git add`.**

---

## Task 2: Obligation framing packet

**Files:**
- Modify: `src/python_deps/depgraph/schedule.py` (add `ObligationPacket` + `frame_obligation`)
- Test: `tests/depgraph/test_obligation_framing.py`

**Interfaces:**
- Produces:
  - `ObligationPacket` — frozen dataclass: `node_id: str`, `node_type: str`, `tier: int`, `layer: str`, `goal: str`, `evidence: str`, `check_command: str`, `depends_on: tuple[str, ...]`, `blocks: tuple[str, ...]`, `certified_context: tuple[str, ...]`.
  - `frame_obligation(graph: DepGraph, node: Node) -> ObligationPacket` — assembles the agent's problem statement entirely from the graph.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_obligation_framing.py
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, Edge, NodeType, Layer, State, EdgeType, DiscoveredBy,
)
from python_deps.depgraph.schedule import frame_obligation, ObligationPacket  # noqa: E402


def _node(nid, ntype, name, state, *, check="true", evidence="", layer=Layer.SYSTEM):
    return Node(
        id=nid, type=ntype, name=name, layer=layer,
        discovered_by=DiscoveredBy.RUNTIME, state=state,
        check_command=check, evidence=evidence,
    )


def test_packet_carries_node_identity_check_and_rich_goal():
    g = DepGraph().with_node(
        _node("syslib:libpq", NodeType.SYSTEM_LIB, "libpq", State.MISSING,
              check="ldconfig -p | grep libpq", evidence="ImportError: libpq.so.5")
    )
    pkt = frame_obligation(g, g.get("syslib:libpq"))
    assert isinstance(pkt, ObligationPacket)
    assert pkt.node_id == "syslib:libpq"
    assert pkt.check_command == "ldconfig -p | grep libpq"
    assert "libpq.so.5" in pkt.evidence
    assert pkt.node_type == NodeType.SYSTEM_LIB.value
    assert pkt.layer == Layer.SYSTEM.value
    # goal is a real instruction, not just the bare name
    assert "libpq" in pkt.goal
    assert pkt.check_command in pkt.goal


def test_packet_carries_dependency_and_certified_context():
    g = (
        DepGraph()
        .with_node(_node("syslib:libpq", NodeType.SYSTEM_LIB, "libpq", State.SATISFIED))
        .with_node(_node("pkg:psycopg2", NodeType.PACKAGE, "psycopg2", State.MISSING, layer=Layer.PIP))
        .with_edge(Edge(src="pkg:psycopg2", dst="syslib:libpq", relation=EdgeType.REQUIRES))
    )
    pkt = frame_obligation(g, g.get("pkg:psycopg2"))
    assert "syslib:libpq" in pkt.depends_on
    assert "syslib:libpq" in pkt.certified_context
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_obligation_framing.py -q`
Expected: FAIL with `ImportError: cannot import name 'frame_obligation'`.

- [ ] **Step 3: Write minimal implementation** (append to `schedule.py`)

```python
@dataclass(frozen=True)
class ObligationPacket:
    """The agent's problem statement for one obligation — assembled from the graph."""
    node_id: str
    node_type: str
    tier: int
    layer: str
    goal: str
    evidence: str
    check_command: str
    depends_on: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    certified_context: tuple[str, ...] = ()


def frame_obligation(graph: DepGraph, node: Node) -> ObligationPacket:
    depends_on = tuple(
        e.dst for e in graph.edges
        if e.src == node.id and e.relation is EdgeType.REQUIRES
    )
    blocks = tuple(
        e.src for e in graph.edges
        if e.dst == node.id and e.relation is EdgeType.REQUIRES
    )
    certified_context = tuple(n.id for n in graph.nodes if n.state is State.SATISFIED)
    goal = (
        f"Satisfy obligation '{node.name}' ({node.type.value}, tier {node.tier}): "
        f"make the host check `{node.check_command}` succeed."
    )
    return ObligationPacket(
        node_id=node.id,
        node_type=node.type.value,
        tier=node.tier,
        layer=node.layer.value,
        goal=goal,
        evidence=node.evidence or "",
        check_command=node.check_command or "",
        depends_on=depends_on,
        blocks=blocks,
        certified_context=certified_context,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_obligation_framing.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Verify, do NOT commit**

Run: `python3 -m pytest tests/depgraph/ -q`. **Do NOT commit or `git add`.**

---

## Task 3: `work` mode — host-check stop in BuildAgent

**Files:**
- Modify: `src/envstate/build_agent.py` (`BuildAgent.run`, lines 546–691)
- Test: `tests/test_build_agent_work_mode.py`

**Interfaces:**
- Current: `BuildAgent.run(task, sandbox_execute, ledger, step_offset=0) -> TaskReport`. Ctor: `BuildAgent(client, model, synthesizer, container_id="unknown", on_usage=None, log_path=None)`. LLM call inside the loop: module-level `complete_with_retry(self.client, self.model, messages, ...) -> (text, usage, raw)`. Accumulator: `history: list[CommandRecord]`. Done predicate: `_is_worker_finished(text)`.
- Produces: `BuildAgent.run(task, sandbox_execute, ledger, step_offset=0, check: str | None = None)`. When `check` is set: at the top of each iteration run `ok, _ = sandbox_execute(check)` and return `TaskReport(status="done")` the instant it passes; `_is_worker_finished(text)` is ignored while `check` is set. When `check=None`: byte-identical to today.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_agent_work_mode.py
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import inspect  # noqa: E402
import src.envstate.build_agent as ba  # noqa: E402
from src.envstate.world_model import Task  # noqa: E402
from src.envstate.ledger import ActionLedger  # noqa: E402


def _task(check="true"):
    return Task(goal="make check pass", done_when=check, layer="system", facts=())


def _agent():
    # client=None is fine: complete_with_retry is monkeypatched in every test that reaches the LLM.
    return ba.BuildAgent(client=None, model="test-model", synthesizer=None)


def test_check_passes_immediately_returns_done_without_llm(monkeypatch):
    calls = {"n": 0}

    def _fake(client, model, messages, **kw):
        calls["n"] += 1
        return ("Action: echo hi", {}, {})

    monkeypatch.setattr(ba, "complete_with_retry", _fake)
    report = _agent().run(_task("true"), lambda cmd: (True, ""), ActionLedger(), check="true")
    assert report.status == "done"
    assert calls["n"] == 0   # host check short-circuited before any LLM call


def test_llm_success_ignored_when_check_active(monkeypatch):
    # LLM claims success but the host check never passes → must NOT finalize (anti-hollow-success)
    def _fake(client, model, messages, **kw):
        return ("Final Answer: Success", {}, {})

    monkeypatch.setattr(ba, "complete_with_retry", _fake)
    report = _agent().run(_task("false"), lambda cmd: (False, ""), ActionLedger(), check="false")
    assert report.status == "blocked"


def test_check_none_preserves_llm_finalize(monkeypatch):
    # legacy path: with check=None the LLM's Final Answer still finalizes
    def _fake(client, model, messages, **kw):
        return ("Final Answer: Success", {}, {})

    monkeypatch.setattr(ba, "complete_with_retry", _fake)
    report = _agent().run(_task("x"), lambda cmd: (True, ""), ActionLedger(), check=None)
    assert report.status == "done"


def test_run_is_graph_blind(monkeypatch):
    # §7 experiments-not-facts: the agent path cannot write graph state — it takes no graph.
    params = inspect.signature(ba.BuildAgent.run).parameters
    assert not any("graph" in p.lower() for p in params)
```

**Implementer note:** confirm the exact string `_is_worker_finished` treats as "finished" (read `_is_worker_finished` and `BUILD_AGENT_SYSTEM_PROMPT`). If it is not literally `"Final Answer: Success"`, use the real trigger string in the two `_fake` returns above. The four assertions (check-passes→done+zero-LLM; LLM-success-ignored-when-check-active; check=None→legacy finalize; run-is-graph-blind) are the contract — keep all four.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_build_agent_work_mode.py -q`
Expected: FAIL — `run()` does not accept `check` (`TypeError`).

- [ ] **Step 3: Write minimal implementation**

In `BuildAgent.run`, add `check: str | None = None` to the signature. At the **top of the `for _step in range(LOCAL_BUDGET):` loop**, before the `complete_with_retry(...)` call:

```python
        for _step in range(LOCAL_BUDGET):
            if check is not None:
                ok, _out = sandbox_execute(check)
                if ok:
                    return TaskReport(
                        task_goal=task.goal,
                        status="done",
                        commands=tuple(history),
                        learning=f"host check satisfied: {check}",
                    )
            text, usage, raw_response = complete_with_retry(...)   # unchanged
```

And guard the existing finalize so the self-declaration is ignored while a host `check` is active:

```python
            finished = _is_worker_finished(text)
            if finished and check is None:
                return TaskReport(
                    task_goal=task.goal,
                    status="done",
                    commands=tuple(history),
                    learning=f"Task criterion met: {task.done_when}",
                )
            # when check is not None: fall through — only the host check (top of loop) finalizes
```

Every `check=None` path stays byte-identical. The budget-exhaustion / stuck / empty-response branches are untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_build_agent_work_mode.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify, do NOT commit**

Run: `python3 -m pytest tests/ -q -k "build_agent"` — existing build-agent tests still pass (proves `check=None` byte-identical). **Do NOT commit or `git add`.**

---

## Task 4: Scheduler adapter + orchestrator wiring

**Files:**
- Create: `src/envstate/graph_scheduler.py`
- Test (unit, RED before wiring): `tests/test_graph_scheduler_decision.py`
- Modify: `src/envstate/orchestrator.py` (`run_v1` sig ~68; `_dep_emit_phase` ~110–138; gate ~179; task-branch `check` ~322–330)
- Test (wiring): `tests/test_graph_scheduler_wiring.py`

**Interfaces:**
- Produces:
  - `packet_to_task(packet: ObligationPacket) -> Task`
  - `next_decision(graph, run_tests: Callable[[], bool], handed: dict[str, int] | None = None, attempt_cap: int = 3) -> tuple[PlannerDecision, str | None]` — returns the per-cycle decision **and** the chosen obligation id (or `None`). It owns oscillation filtering, so the orchestrator calls this one tested function (no split-brain).
  - `run_v1(..., enable_graph_scheduler: bool = False, graph_scheduler_attempt_cap: int = 3)`.

**Design contract (from the spec):**
- Actionable frontier (after oscillation filter) → `PlannerDecision(action="task", task=packet_to_task(...))`, chosen id returned. `done_when` = the obligation's `check_command`.
- Frontier empty (or all over the attempt cap) → `run_tests()`. Green → `action="done"`. Red → a **discover** task (`done_when = VERIFY_TEST_CMD`); chosen id `None`.
- **Oscillation guard:** `handed` is the orchestrator's per-run hand-out counter; nodes at/over `attempt_cap` are filtered out (fall to the sufficiency branch).

### 4a — `graph_scheduler.py` + its unit test

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_graph_scheduler_decision.py
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from src.envstate.graph_scheduler import next_decision, packet_to_task  # noqa: E402
from src.envstate.orchestrator import VERIFY_TEST_CMD  # noqa: E402
from python_deps.depgraph.schedule import frame_obligation  # noqa: E402


def _missing(state=State.MISSING):
    return DepGraph().with_node(Node(
        id="pkg:requests", type=NodeType.PACKAGE, name="requests", layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=state,
        check_command="python -c 'import requests'",
    ))


def test_actionable_frontier_yields_task_and_chosen_id():
    decision, chosen = next_decision(_missing(), run_tests=lambda: False)
    assert decision.action == "task"
    assert decision.task.target_node_ids == ("pkg:requests",)
    assert decision.task.done_when == "python -c 'import requests'"
    assert chosen == "pkg:requests"


def test_clean_frontier_passing_tests_yields_done():
    decision, chosen = next_decision(_missing(State.SATISFIED), run_tests=lambda: True)
    assert decision.action == "done"
    assert chosen is None


def test_clean_frontier_failing_tests_yields_discover_task():
    decision, chosen = next_decision(DepGraph(), run_tests=lambda: False)
    assert decision.action == "task"
    assert decision.task.done_when == VERIFY_TEST_CMD
    assert chosen is None


def test_none_graph_falls_to_sufficiency():
    decision, chosen = next_decision(None, run_tests=lambda: True)
    assert decision.action == "done"
    decision2, _ = next_decision(None, run_tests=lambda: False)
    assert decision2.action == "task"   # discover task when no graph and tests red


def test_oscillation_cap_skips_over_handed_node():
    # the only frontier node is at the cap → fall to the sufficiency branch
    decision, chosen = next_decision(
        _missing(), run_tests=lambda: True, handed={"pkg:requests": 3}, attempt_cap=3,
    )
    assert decision.action == "done"   # frontier filtered empty, tests green → done
    assert chosen is None


def test_packet_to_task_maps_fields():
    g = _missing()
    t = packet_to_task(frame_obligation(g, g.get("pkg:requests")))
    assert t.target_node_ids == ("pkg:requests",)
    assert t.done_when == "python -c 'import requests'"
    assert t.layer == Layer.PIP.value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_graph_scheduler_decision.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.graph_scheduler'`.

- [ ] **Step 3: Write `graph_scheduler.py`**

```python
# src/envstate/graph_scheduler.py
"""Envstate adapter for the graph scheduler.

Turns the pure scheduling layer's next actionable obligation into a
PlannerDecision the orchestrator already knows how to execute. The graph decides
*what & when*; this module only translates that into the existing Task/Planner
message types. It writes no graph state.
"""
from __future__ import annotations

from typing import Callable

from python_deps.depgraph.schema import DepGraph
from python_deps.depgraph.schedule import (
    ObligationPacket, frame_obligation, scheduler_frontier,
)
from src.envstate.world_model import PlannerDecision, Task


def packet_to_task(packet: ObligationPacket) -> Task:
    facts = []
    if packet.evidence:
        facts.append(f"evidence: {packet.evidence}")
    if packet.depends_on:
        facts.append("depends_on: " + ", ".join(packet.depends_on))
    if packet.certified_context:
        facts.append("already satisfied: " + ", ".join(packet.certified_context))
    return Task(
        goal=packet.goal,
        done_when=packet.check_command,
        layer=packet.layer,
        facts=tuple(facts),
        target_node_ids=(packet.node_id,),
    )


def _discover_task() -> Task:
    # Lazy import: orchestrator imports this module, so import its constant at call
    # time to avoid a circular import and keep a single source of truth.
    from src.envstate.orchestrator import VERIFY_TEST_CMD
    return Task(
        goal=(
            "All known requirements are satisfied but the test suite still fails. "
            "Run the suite, read the failure, and install or provide whatever the "
            "running code actually needs (a missing dynamic import, a system "
            "library, a runtime env var, or a service) until the tests pass."
        ),
        done_when=VERIFY_TEST_CMD,
        layer="tests",
        facts=(),
    )


def next_decision(
    graph: DepGraph | None,
    run_tests: Callable[[], bool],
    handed: dict[str, int] | None = None,
    attempt_cap: int = 3,
) -> tuple[PlannerDecision, str | None]:
    """Decide the next action from the certified graph (no LLM).

    Returns (decision, chosen_obligation_id). chosen_id is the node handed to the
    agent (so the caller can bump its oscillation counter), or None for the
    done / discover-task branches.
    """
    handed = handed or {}
    frontier = scheduler_frontier(graph) if graph is not None else ()
    eligible = [n for n in frontier if handed.get(n.id, 0) < attempt_cap]
    if eligible:
        node = eligible[0]
        decision = PlannerDecision(
            action="task", task=packet_to_task(frame_obligation(graph, node))
        )
        return decision, node.id
    if run_tests():
        return PlannerDecision(
            action="done", reason="graph-scheduler: frontier clean, tests pass"
        ), None
    return PlannerDecision(action="task", task=_discover_task()), None
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `python3 -m pytest tests/test_graph_scheduler_decision.py -q`
Expected: PASS (6 tests).

### 4b — orchestrator wiring

- [ ] **Step 5: Write the failing wiring test**

Create `tests/test_graph_scheduler_wiring.py` with three intents (mirror the construction in `tests/test_runtime_feedback_wiring.py` / `tests/test_orchestrator_v1.py` — same stubs, `initial_map`, `ActionLedger`, `max_cycles=1`):

1. **Flag off → planner drives.** `run_v1(..., enable_graph_scheduler=False)` with a `planner` stub whose `decide` sets a flag; assert `decide` was called.
2. **Flag on → scheduler drives, planner untouched.** Call `run_v1(..., enable_graph_scheduler=True, enable_dep_emit=True)` — **both** flags are required when calling `run_v1` directly, because the implication `graph_scheduler → dep_emit` lives in `DockerAgent.__init__`, not `run_v1`; without `enable_dep_emit=True`, `_dep_emit_phase` early-returns and certify never runs. Seed `initial_world_map` with a `dep_graph` holding one `MISSING` node (`check_command` set); pass a `planner` stub whose `decide` raises if called; pass `exec_readonly=lambda cmd: (1, "missing")` so `certify_refresh` keeps the node `MISSING` (rc≠0) and the frontier is non-empty; pass a `build_agent` stub whose `run(self, task, sandbox_execute, ledger, step_offset=0, check=None)` records the `task` and `check` it received and returns `TaskReport(status="blocked", ...)`. Assert: `planner.decide` never called; `build_agent.run` received a task with `target_node_ids == ("pkg:...",)` and `check == task.done_when`.
3. **Drain suppressed under the flag.** With `enable_graph_scheduler=True, enable_dep_emit=True`, monkeypatch `src.envstate.depgraph_live.emit_drain` to a spy; run one cycle; assert the spy was **not** called (the deterministic drain is off). For contrast, with `enable_dep_emit=True, enable_graph_scheduler=False`, assert the spy **is** called. (Patching the module attribute works because `_dep_emit_phase` does a function-local `from src.envstate.depgraph_live import emit_drain` at call time, reading the already-patched attribute.)
4. **Stuck → giveup.** With `enable_graph_scheduler=True, enable_dep_emit=True, max_cycles=4`, a `dep_graph` whose nodes have NO `check_command` (frontier always empty; certify reveals nothing new), `exec_readonly=lambda cmd: (1, "")`, and `sandbox_execute` always failing (so `run_tests()` stays red and no new obligations appear), assert the run terminates with `stop_reason == "planner_giveup"` (the `_sched_stuck >= 2` branch fired) rather than running to `max_cycles`.

**Stub note:** every `build_agent` stub's `run` MUST include `check=None` in its signature, or scheduler mode (which passes `check=`) raises `TypeError`. This applies to the EXISTING stubs too — see Step 7f.

- [ ] **Step 6: Run wiring test to verify it fails**

Run: `python3 -m pytest tests/test_graph_scheduler_wiring.py -q`
Expected: FAIL — `run_v1` does not accept `enable_graph_scheduler` yet.

- [ ] **Step 7: Wire `run_v1`**

(a) Add to the `run_v1` keyword-only signature (after `enable_runtime_feedback`):

```python
    enable_graph_scheduler: bool = False,
    graph_scheduler_attempt_cap: int = 3,
```

(b) Near `_rt_mark`, add the scheduler counters:

```python
    _handed: dict[str, int] = {}       # graph-scheduler: per-obligation hand-out counts
    _sched_stuck: int = 0              # consecutive discover cycles with no new obligations
    _sched_last_nodes: int = -1        # dep-graph node count at the last discover cycle
```

These are plain locals of `run_v1` (exactly like `global_step` and `_rt_mark`). The gate in Step 7d runs **inline in the `for cycle` loop body**, so assigning them needs **no** `nonlocal` — a `nonlocal` statement at loop scope is a `SyntaxError`. Do NOT wrap the gate in a nested function.

(c) **Suppress the drain** in `_dep_emit_phase`. Replace the unconditional `emit_drain(...)` call with:

```python
        graph = certify_refresh(current_map.dep_graph, exec_readonly, cycle)
        if not enable_graph_scheduler:
            graph, _reports, steps = emit_drain(
                graph, build_agent, sandbox_execute, ledger, exec_readonly,
                step_offset=global_step, cycle=cycle,
            )
            global_step += steps
        # (installed-fold + render + merge_map below run on `graph` exactly as today)
```

Certify still runs (populating the frontier); the deterministic drain is skipped under the scheduler. Only the `emit_drain(...)` call and its `global_step += steps` move inside the guard — `ensure_python_shim`, `certify_refresh`, the installed-fold, `render_depgraph_planner`, and `merge_map` all stay exactly as today, operating on the certified `graph`.

(d) Replace `decision = planner.decide(current_map)` (line 179) with the gate:

```python
        # ── 1. Decide what to do next ───────────────────────────────────────
        if enable_graph_scheduler:
            from src.envstate.graph_scheduler import next_decision
            decision, chosen = next_decision(
                current_map.dep_graph,
                lambda: sandbox_execute(VERIFY_TEST_CMD)[0],
                handed=_handed,
                attempt_cap=graph_scheduler_attempt_cap,
            )
            if chosen is not None:
                _handed[chosen] = _handed.get(chosen, 0) + 1
                _sched_stuck = 0
            elif decision.action == "task":          # discover task → sufficiency-stuck
                n_nodes = len(current_map.dep_graph.nodes) if current_map.dep_graph else 0
                _sched_stuck = _sched_stuck + 1 if n_nodes <= _sched_last_nodes else 0
                _sched_last_nodes = n_nodes
                if _sched_stuck >= 2:                  # consecutive discover rounds revealed no new obligations (bounded anyway by max_cycles)
                    decision = PlannerDecision(
                        action="giveup",
                        reason="graph-scheduler: no new obligations after 2 sufficiency rounds",
                    )
        else:
            decision: PlannerDecision = planner.decide(current_map)
```

Add `from python_deps.depgraph.schedule import scheduler_frontier` is NOT needed here (the adapter owns it); ensure `PlannerDecision` is already imported at the top of `orchestrator.py` (it is — used at line 179 today).

(e) In the task-branch (~322), pass the host `check` only in scheduler mode, and floor the step advance so an early-returning check (zero commands) still advances the ledger offset:

```python
        report = build_agent.run(
            task,
            sandbox_execute,
            ledger,
            step_offset=global_step,
            check=(task.done_when if enable_graph_scheduler else None),
        )
        global_step += max(len(report.commands), 1)
```

(f) **Update the existing build_agent stubs** so the unconditional `check=` kwarg doesn't `TypeError` on the off-path. Add `check=None` to the `run` signature of `FakeBuildAgent.run` in `tests/test_orchestrator_v1.py` (~line 76) and `_StubBuildAgent.run` in `tests/test_runtime_feedback_wiring.py` (~line 54): `def run(self, task, sandbox_execute, ledger, step_offset=0, check=None):`. No behavior change — they just accept and ignore it.

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_graph_scheduler_decision.py tests/test_graph_scheduler_wiring.py tests/test_orchestrator_v1.py -q`
Expected: PASS (6 unit + 3 wiring + existing orchestrator tests unchanged).

- [ ] **Step 9: Verify, do NOT commit**

Run: `python3 -m pytest tests/ -q -k "orchestrator or graph_scheduler or runtime_feedback or dep_emit"`. Confirm the off-path is byte-identical. **Do NOT commit or `git add`.**

---

## Task 5: Flag/arm plumbing + Planner deprecation marker

**Files:**
- Modify: `agent.py` (ctor param ~238, derivation ~285–291, argparse ~3193, `DockerAgent(...)` ~3249, `run_v1(...)` ~1255)
- Modify: `run_rat_benchmark.py` (arm choices ~802, arm ladder ~839–843, env override ~392–396)
- Modify: `run_repo2run_benchmark.py` (`_ARM_PRESETS` ~3182, subprocess forward ~218)
- Modify: `multi_docker_eval_adapter.py` (read env ~780, forward ~802)
- Modify: `src/envstate/planner.py` (module deprecation note)
- Test: `tests/test_graph_scheduler_flag.py`

**Implication chain:** the graph scheduler needs the dep graph **built and certified** each cycle. `_dep_emit_phase` (gated on `enable_dep_emit`) is what runs `certify_refresh` — and Task 4(c) makes it skip the deterministic drain under the scheduler. So `enable_graph_scheduler` implies `enable_dep_emit` (for certify + render), with the drain suppressed. Set it in `agent.py` **before** the `enable_dep_emit` line (≈285), ORed in:

```python
        self.enable_graph_scheduler: bool = bool(enable_graph_scheduler)
        self.enable_runtime_feedback: bool = bool(enable_runtime_feedback)
        self.enable_dep_emit: bool = (
            bool(enable_dep_emit)
            or self.enable_runtime_feedback
            or self.enable_graph_scheduler
        )
        self.enable_dep_graph = enable_dep_graph or self.enable_dep_emit
```

(Confirm `enable_v1` is derived from `self.enable_dep_graph` downstream, as the runtime-feedback path is.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_scheduler_flag.py
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from agent import DockerAgent  # noqa: E402


def test_graph_scheduler_flag_implies_dep_graph_and_emit():
    a = DockerAgent(enable_graph_scheduler=True)
    assert a.enable_graph_scheduler is True
    assert a.enable_dep_emit is True      # needed for certify_refresh (drain suppressed in orchestrator)
    assert a.enable_dep_graph is True


def test_default_off():
    a = DockerAgent()
    assert getattr(a, "enable_graph_scheduler", False) is False
```

(If `DockerAgent()` is too heavy for a unit test, mirror exactly what `tests/test_runtime_feedback_flag.py` does — match that file's construction and assertion style.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_graph_scheduler_flag.py -q`
Expected: FAIL (`enable_graph_scheduler` not accepted).

- [ ] **Step 3: Implement the plumbing** (mirror `enable_runtime_feedback` at every site)

1. `agent.py` ctor param `enable_graph_scheduler=False`.
2. `agent.py` derivation block above, inserted **before** line 286.
3. `agent.py` argparse: `parser.add_argument("--enable-graph-scheduler", action="store_true", help="Graph schedules the agent (DECIDE=graph, EXECUTE=agent, CERTIFY=host).")`.
4. `agent.py` `DockerAgent(...)` call: `enable_graph_scheduler=args.enable_graph_scheduler`.
5. `agent.py` `run_v1(...)` call (~1255): `enable_graph_scheduler=getattr(self, "enable_graph_scheduler", False)`.
6. `run_rat_benchmark.py`: add `"v1gs"` to the `--arm` choices (~802).
7. `run_rat_benchmark.py` arm ladder (~839–843): widen the predicate so `v1gs` also sets the four implied env vars `v1gder` already sets (V1, CONTRACT_GRAPH, DEP_GRAPH, DEP_EMIT) — edit each of those four lines so `args.arm in ("v1gder", "v1gs")` (or equivalent) enables them — then add the fifth line `os.environ["DOCKERAGENT_ENABLE_GRAPH_SCHEDULER"] = "1" if args.arm == "v1gs" else "0"`.
8. `run_rat_benchmark.py` env-var arm override (~392–396): `if os.environ.get("DOCKERAGENT_ENABLE_GRAPH_SCHEDULER") == "1": arm = "v1gs"`.
9. `run_repo2run_benchmark.py` `_ARM_PRESETS` (~3182): add `"v1gs"` = the `v1gde` preset plus `"enable_graph_scheduler": True` and `"_label": "armV1gs_graph_scheduler"`.
10. `run_repo2run_benchmark.py` subprocess forward (~218): `if getattr(args, "enable_graph_scheduler", False): command.append("--enable-graph-scheduler")`.
11. `multi_docker_eval_adapter.py` read env (~780): `_enable_graph_scheduler = os.environ.get("DOCKERAGENT_ENABLE_GRAPH_SCHEDULER", "").lower() in ("1", "true", "yes", "on")`.
12. `multi_docker_eval_adapter.py` forward to ctor (~802): `enable_graph_scheduler=_enable_graph_scheduler`.

- [ ] **Step 4: Add the Planner deprecation marker** (`src/envstate/planner.py`, under the module docstring)

```python
# DEPRECATED (2026-06-26): The strategic LLM Planner is retained but dormant under
# the graph-scheduled architecture (enable_graph_scheduler). run_v1 routes around
# planner.decide when the flag is on; strategy now lives in graph topology. Kept
# for possible reuse (ambiguous-frontier ordering, whole-repo give-up). Do not
# delete. See docs/superpowers/specs/2026-06-26-graph-scheduled-agent-architecture-design.md.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_graph_scheduler_flag.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Verify, do NOT commit**

Run: `python3 -m pytest tests/test_graph_scheduler_flag.py tests/test_runtime_feedback_flag.py -q`. **Do NOT commit or `git add`.**

---

## Final Verification (controller, after all tasks)

```bash
python3 -m pytest tests/depgraph/ tests/test_build_agent_work_mode.py \
  tests/test_graph_scheduler_decision.py tests/test_graph_scheduler_wiring.py \
  tests/test_graph_scheduler_flag.py tests/test_orchestrator_v1.py \
  tests/test_runtime_feedback_wiring.py tests/test_runtime_feedback_flag.py -q
```

Expected: all green. Then a broad whole-branch review (most-capable model) for cross-task gaps — the pass that caught the render/duplicate/inert-flag gaps in the runtime-feedback feature. **Nothing is committed; the working tree stays dirty.**

## Known v1 limitations (documented, not bugs)
- **1-cycle discovery lag:** a runtime-discovered `UNKNOWN` obligation is ingested at cycle N's step 0b, certified to `MISSING` by `_dep_emit_phase` at cycle N+1, and first schedulable at N+1's gate. Bounded by `max_cycles`; the wiring test seeds an already-`MISSING` node to stay deterministic.
- **Double pytest on a stuck cycle:** the empty-frontier `run_tests()` probe plus the discover task's `check=VERIFY_TEST_CMD` run pytest twice on the first stuck iteration. Accepted for v1 (the result feeds the agent's context and the ledger).

## Deferred (NOT in this plan — see spec §10)
- Learned-recipe cache (memoize certified agent-fixes → deterministic recipes).
- Full removal of the strategic LLM Planner.
- SERVICE-tier necessity scheduling (v1 routes services through the sufficiency-stuck branch).
- Multi-extract per observation; finer per-module attribution; richer progress-based escalation beyond the 2-round stuck counter.
