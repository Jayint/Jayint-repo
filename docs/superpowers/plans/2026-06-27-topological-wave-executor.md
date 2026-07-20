# Topological-Wave Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the topological-wave executor by adding host-first failure repair (fixing the broken backoff→LLM bridge) and an LLM-only turn budget, without touching the proven batch-drain path.

**Architecture:** The current `v1gs` arm already runs the deterministic batch wave (`emit_drain`), certifies per node, runs the verified test gate at frontier exhaustion, ingests runtime failures, and finalizes via the deterministic maintainer. This plan adds the one genuinely-missing behavior — when a reciped wave node fails to certify, hand it to a *bounded host-first repair* (LLM, ≤5 turns, host-check-stopped, one attempt per node per run) instead of silently dropping it — plus a turn budget that decrements only on LLM repair.

**Tech Stack:** Python 3, pytest, the existing `python_deps.depgraph` (`emit.py`, `schedule.py`, `depgraph_live.py`) and `src.envstate` (`orchestrator.py`, `build_agent.py`) modules.

**Spec:** `docs/superpowers/specs/2026-06-26-unified-executor-loop-delta.md` (§3–§9). Read it first.

## Global Constraints

- **NO COMMITS. NO `git add`.** Leave the working tree dirty. Every task's final step is "run the tests; do NOT commit." This overrides the writing-plans skill's default commit step.
- **Default-off byte-identical.** All new behavior is gated under `enable_graph_scheduler`. With `enable_dep_emit=False` the `_dep_emit_phase` returns before any new code (orchestrator.py:115-117); non-graph-scheduler arms are unchanged. A reviewer must be able to confirm the off path is untouched. Each orchestrator-touching task re-runs `tests/test_orchestrator_v1.py`.
- **Host certifies; nothing else flips `state`.** Repair code proposes *commands*; only `certify_refresh` flips a node to SATISFIED. The LLM never writes graph state or `done_flag`. (Both `_dep_emit_phase` and `repair_failed_nodes` call `certify_refresh` — both host-side; the host is still the sole certifier.)
- **The LLM cannot self-declare done.** Repair uses `BuildAgent.run(..., check=node.check_command)`; the host check is the only stop (the existing `if finished and check is None` guard at build_agent.py:601 stays intact).
- **Immutability.** `DepGraph`, `Node`, `WorldModelMap`, `Task`, `TaskReport` are frozen — return new copies, never mutate.
- **Repair scope = reciped, host-checkable nodes only.** Repair targets PACKAGE (with `version`) and SYSTEM_LIB/TOOL (with an `apt:` fix) nodes that are MISSING, deps-satisfied, and **carry a `check_command`**. CONFIG and SERVICE stay advisory (already excluded by `_is_actionable`). A reciped node with `check_command=None` has no host stop condition and is out of repair scope (see Task 3 note).
- **Two-oracle DONE.** `done_flag` = host-verified test gate green (`_verified_test_run_passed`) AND frontier exhausted. Never a bare `pytest rc=0`.

## Phase Overview

- **Phase 1 (Tasks 1–4)** — the wave-failure host-first repair path (the broken-bridge fix). The core deliverable; stands alone and is e2e-validatable.
- **Phase 2 (Task 5)** — LLM-only turn accounting.
- **Deferred** — single-OBSERVE consolidation (rationale below; behavior already realized).
- **Task 6** — e2e validation.

## File Structure

- `src/python_deps/depgraph/emit.py` — add `next_deterministic_wave(graph)` (the named wave selector) and `failed_reciped_nodes(graph)` (the `isolate` step). Pure, no I/O.
- `src/envstate/build_agent.py` — add a `budget` parameter to `BuildAgent.run`.
- `src/envstate/depgraph_live.py` — add `repair_failed_nodes(...)`.
- `src/envstate/orchestrator.py` — wire repair into `_dep_emit_phase` (Task 4); add the turn budget (Task 5).

---

## Task 1: `next_deterministic_wave(graph)` — the named wave selector

**Files:**
- Modify: `src/python_deps/depgraph/emit.py`
- Test: `tests/depgraph/test_next_deterministic_wave.py` (create)

**Interfaces:**
- Consumes: `partition(graph) -> Partition` (field `.emittable`), `topo_order(graph, nodes) -> tuple[Node, ...]`, `build_recipe(graph, ordered) -> tuple[EmitStep, ...]` — all in `emit.py`.
- Produces: `next_deterministic_wave(graph: DepGraph) -> tuple[EmitStep, ...]` — the current topological wave as ≤2 `EmitStep`s, or `()` when nothing is emittable. (`partition().emittable` already excludes backoff-capped and CONFLICTS nodes, so no extra filtering is needed.)

Extracts the wave-selection `emit_drain` performs inline into a named, testable function (spec §3/§7). `emit_drain` is **not** changed.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_next_deterministic_wave.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from python_deps.depgraph.emit import next_deterministic_wave  # noqa: E402


def _pkg(nid, name, version, state=State.MISSING):
    return Node(
        id=nid, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=state,
        check_command=f"python -c 'import {name}'", version=version,
    )


def test_empty_graph_yields_no_wave():
    assert next_deterministic_wave(DepGraph()) == ()


def test_emittable_packages_become_one_pip_step():
    g = (DepGraph()
         .with_node(_pkg("pkg:a", "a", "1.0"))
         .with_node(_pkg("pkg:b", "b", "2.0")))
    wave = next_deterministic_wave(g)
    assert len(wave) == 1
    assert wave[0].kind == "python_install"
    assert set(wave[0].target_node_ids) == {"pkg:a", "pkg:b"}


def test_satisfied_nodes_are_not_in_the_wave():
    g = DepGraph().with_node(_pkg("pkg:a", "a", "1.0", state=State.SATISFIED))
    assert next_deterministic_wave(g) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_next_deterministic_wave.py -q`
Expected: FAIL — `cannot import name 'next_deterministic_wave'`.

- [ ] **Step 3: Implement**

Add to `src/python_deps/depgraph/emit.py`, directly after `build_recipe`:

```python
def next_deterministic_wave(graph: "DepGraph") -> tuple[EmitStep, ...]:
    """The current topological wave: the emittable frontier collapsed to ≤2 batch
    EmitSteps (apt + pip), deps-before-dependents. Empty when nothing is emittable.

    A batch IS a wave (spec §3): partition() only surfaces nodes whose REQUIRES-deps
    are already SATISFIED, and already excludes backoff-capped / conflicting nodes.
    """
    part = partition(graph)
    if not part.emittable:
        return ()
    ordered = topo_order(graph, part.emittable)
    return build_recipe(graph, ordered)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_next_deterministic_wave.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the emit suite for no regression**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_emit_partition.py tests/depgraph/test_emit_build_recipe.py tests/depgraph/test_emit_topo.py -q`
Expected: PASS.

- [ ] **Step 6: Verify; do NOT commit.**

---

## Task 2: `BuildAgent.run` gains a `budget` parameter

**Files:**
- Modify: `src/envstate/build_agent.py:546-553` (signature), the inner loop header (`for _step in range(LOCAL_BUDGET)`, ~570), and the budget-exhaustion message (~703).
- Test: `tests/test_build_agent_work_mode.py` (add one test — this file already constructs a real `ActionLedger()` and imports `complete_with_retry`'s module, mirror those).

**Interfaces:**
- Produces: `BuildAgent.run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget: int = LOCAL_BUDGET) -> TaskReport`. Default `LOCAL_BUDGET` (8) keeps every existing caller byte-identical.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_build_agent_work_mode.py (uses this file's existing imports:
# ActionLedger, BuildAgent, Task, and module `src.envstate.build_agent as bamod`)
def test_run_respects_explicit_budget(monkeypatch):
    """budget=2 caps the shell-action loop at 2 LLM steps before returning blocked."""
    import src.envstate.build_agent as bamod
    from src.envstate.build_agent import BuildAgent
    from src.envstate.world_model import Task

    calls = {"n": 0}
    def _fake(client, model, messages, *a, **k):
        calls["n"] += 1
        return f"Action: echo step-{calls['n']}", {"total_tokens": 1}, None
    # complete_with_retry is a MODULE-LEVEL name in build_agent — patch it there.
    monkeypatch.setattr(bamod, "complete_with_retry", _fake)

    ba = BuildAgent(client=None, model="test-model", synthesizer=None)
    task = Task(goal="g", done_when="false", layer="pip", facts=())
    report = ba.run(task, lambda cmd: (False, "boom"),
                    bamod.ActionLedger() if hasattr(bamod, "ActionLedger") else _new_ledger(),
                    check="false", budget=2)
    assert report.status == "blocked"
    assert calls["n"] == 2          # exactly 2 LLM steps, not LOCAL_BUDGET (8)
```

If the file already has a ledger fixture/helper, use it for the third argument instead of the `hasattr` line (the real `ActionLedger()` is what the other tests in this file pass). The fake returns a DIFFERENT action each call so the stuck-guard (which needs ≥2 prior identical-ish failures) cannot fire inside 2 steps.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_build_agent_work_mode.py::test_run_respects_explicit_budget -q`
Expected: FAIL — `run()` got an unexpected keyword argument `budget`.

- [ ] **Step 3: Implement**

Change the signature (lines 546-553):

```python
    def run(
        self,
        task: Task,
        sandbox_execute: Callable[[str], tuple[bool, str]],
        ledger: ActionLedger,
        step_offset: int = 0,
        check: str | None = None,
        budget: int = LOCAL_BUDGET,
    ) -> TaskReport:
```

Change the inner loop header from `for _step in range(LOCAL_BUDGET):` to:

```python
        for _step in range(budget):
```

Change the budget-exhaustion return message from `f"Ran out of local budget ({LOCAL_BUDGET} steps)"` to `f"Ran out of local budget ({budget} steps)"`.

Leave the host-check early-return (574-581) and the `if finished and check is None` guard (601) exactly as they are.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_build_agent_work_mode.py::test_run_respects_explicit_budget -q`
Expected: PASS.

- [ ] **Step 5: Run the full build-agent suite (default-budget byte-identical)**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_build_agent.py tests/test_build_agent_work_mode.py tests/test_build_agent_recipe.py -q`
Expected: PASS — existing callers omit `budget`, still get 8.

- [ ] **Step 6: Verify; do NOT commit.**

---

## Task 3: `failed_reciped_nodes(graph)` — the `isolate` step

**Files:**
- Modify: `src/python_deps/depgraph/emit.py`
- Test: `tests/depgraph/test_failed_reciped_nodes.py` (create)

**Interfaces:**
- Consumes: `_dependencies_satisfied(graph, node)` from `python_deps.depgraph.schedule` (lazy import inside the function — `schedule` imports from `emit`, so import at call time to avoid a cycle).
- Produces: `failed_reciped_nodes(graph: DepGraph) -> tuple[Node, ...]` — reciped nodes (PACKAGE with a `version`; SYSTEM_LIB/TOOL with an `apt:` fix) still `MISSING`, with a `check_command`, deps SATISFIED. After a drain these are the wave members the batch could not certify.

**Note (reviewed gap):** nodes with `check_command is None` are intentionally excluded — they have no host stop condition, so a host-first repair could never terminate on them. This is the same exclusion `_is_actionable` makes (`bool(node.check_command)`). Real reciped PACKAGE nodes always carry an import/`pip show` check (set by the static probe / `config_scan`); a reciped node without one is a construction error, not a repair target. If such a node ever appears MISSING it stays advisory (surfaced via the residual/sufficiency path), not silently installed.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_failed_reciped_nodes.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, Edge, NodeType, Layer, State, EdgeType, DiscoveredBy,
)
from python_deps.depgraph.emit import failed_reciped_nodes  # noqa: E402


def _pkg(nid, name, state, *, version="1.0", check="true"):
    return Node(id=nid, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=state,
                check_command=check, version=version)


def test_missing_reciped_node_is_a_culprit():
    g = DepGraph().with_node(_pkg("pkg:a", "a", State.MISSING))
    assert [n.id for n in failed_reciped_nodes(g)] == ["pkg:a"]


def test_satisfied_node_is_not_a_culprit():
    g = DepGraph().with_node(_pkg("pkg:a", "a", State.SATISFIED))
    assert failed_reciped_nodes(g) == ()


def test_node_without_check_is_excluded():
    g = DepGraph().with_node(_pkg("pkg:a", "a", State.MISSING, check=None))
    assert failed_reciped_nodes(g) == ()


def test_config_node_is_never_a_culprit():
    cfg = Node(id="config:X", type=NodeType.CONFIG, name="X", layer=Layer.CONFIG,
               discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
               check_command="printenv X")
    assert failed_reciped_nodes(DepGraph().with_node(cfg)) == ()


def test_node_with_unsatisfied_dep_is_held_back():
    g = (DepGraph()
         .with_node(_pkg("pkg:dep", "dep", State.MISSING))
         .with_node(_pkg("pkg:app", "app", State.MISSING))
         .with_edge(Edge(src="pkg:app", dst="pkg:dep", relation=EdgeType.REQUIRES)))
    assert [n.id for n in failed_reciped_nodes(g)] == ["pkg:dep"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_failed_reciped_nodes.py -q`
Expected: FAIL — `cannot import name 'failed_reciped_nodes'`.

- [ ] **Step 3: Implement**

Add to `src/python_deps/depgraph/emit.py` (use the SAME `chosen_fix` accessor `_is_emittable` reads at ~line 99 for SYSTEM_LIB/TOOL — confirmed to be `node.chosen_fix`):

```python
def _is_reciped(node: "Node") -> bool:
    """A node the deterministic recipe layer can install (mirrors _is_emittable's
    type/fix test, minus the attempt cap — a backed-off node is still 'reciped')."""
    if node.type is NodeType.PACKAGE:
        return bool(node.version)
    if node.type in (NodeType.SYSTEM_LIB, NodeType.TOOL):
        return bool(node.chosen_fix) and node.chosen_fix.startswith("apt:")
    return False


def failed_reciped_nodes(graph: "DepGraph") -> tuple["Node", ...]:
    """Reciped, host-checkable nodes still MISSING after a drain whose deps are
    SATISFIED — the spec's `isolate` (§4). Excludes CONFIG/SERVICE (advisory) and
    nodes with no check_command (no host stop condition)."""
    from python_deps.depgraph.schedule import _dependencies_satisfied
    out = []
    for n in graph.nodes:
        if n.state is not State.MISSING:
            continue
        if not n.check_command:
            continue
        if not _is_reciped(n):
            continue
        if not _dependencies_satisfied(graph, n):
            continue
        out.append(n)
    return tuple(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/depgraph/test_failed_reciped_nodes.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Verify; do NOT commit.**

---

## Task 4: Host-first repair after the drain (the broken-bridge fix)

**Files:**
- Modify: `src/envstate/depgraph_live.py` (add `repair_failed_nodes`)
- Modify: `src/envstate/orchestrator.py` — `run_v1` adds a `_repaired_ids: set[str]` local; `_dep_emit_phase` calls repair after `emit_drain`
- Test: `tests/test_depgraph_live_repair.py` (create); `tests/test_graph_scheduler_flag.py` (add an off-path source guard)

**Interfaces:**
- Consumes: `failed_reciped_nodes(graph)` (Task 3), `BuildAgent.run(..., check=, budget=)` (Task 2), `certify_refresh(graph, exec_readonly, cycle)`.
- Produces: `repair_failed_nodes(graph, build_agent, sandbox_execute, ledger, exec_readonly, *, step_offset, cycle, repaired_ids: set[str], max_repair: int = 3, budget: int = 5) -> tuple[DepGraph, int, int]` — for each failed reciped node **not already in `repaired_ids`** (capped at `max_repair` per call), frame a one-node `Task`, run a bounded host-first repair, re-certify; record the node id in `repaired_ids` so it is never repaired twice across the run. Returns `(new_graph, steps_consumed, repaired_count)`.

Replaces the broken 2-failure backoff bridge: a reciped install the batch couldn't certify is handed *directly* to the LLM with its own check as the stop (spec §0 #1, §4). `repaired_ids` gives the per-node, cross-cycle memory the reviewers found missing — one host-first repair per node per run; the global turn budget (Task 5) is the backstop.

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_depgraph_live_repair.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from src.envstate.depgraph_live import repair_failed_nodes  # noqa: E402
from src.envstate.world_model import TaskReport  # noqa: E402


class _Ledger:                       # self-contained; the fake agent ignores it
    def append(self, *a, **k): pass
    def events(self): return []


class _FakeAgent:
    def __init__(self):
        self.tasks = []
    def run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget=8):
        self.tasks.append((task.done_when, check, budget))
        return TaskReport(task.goal, "done", (), "ok")


def _pkg(nid, name, state):
    return Node(id=nid, type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=state,
                check_command=f"chk-{name}", version="1.0")


def test_repair_targets_each_failed_node_with_its_check_and_budget():
    g = DepGraph().with_node(_pkg("pkg:a", "a", State.MISSING))
    agent = _FakeAgent()
    new_graph, steps, repaired = repair_failed_nodes(
        g, agent, sandbox_execute=lambda c: (True, ""), ledger=_Ledger(),
        exec_readonly=lambda c: (0, ""),     # rc 0 → certify would pass (integer, not bool)
        step_offset=0, cycle=1, repaired_ids=set(), max_repair=3, budget=5,
    )
    assert agent.tasks == [("chk-a", "chk-a", 5)]   # check_command IS the stop; budget=5
    assert repaired == 1


def test_repair_is_capped_by_max_repair():
    g = (DepGraph()
         .with_node(_pkg("pkg:a", "a", State.MISSING))
         .with_node(_pkg("pkg:b", "b", State.MISSING))
         .with_node(_pkg("pkg:c", "c", State.MISSING)))
    agent = _FakeAgent()
    repair_failed_nodes(
        g, agent, sandbox_execute=lambda c: (True, ""), ledger=_Ledger(),
        exec_readonly=lambda c: (1, ""),     # nonzero rc → nodes stay MISSING
        step_offset=0, cycle=1, repaired_ids=set(), max_repair=2, budget=5,
    )
    assert len(agent.tasks) == 2     # capped at max_repair


def test_node_already_repaired_is_not_retried():
    g = DepGraph().with_node(_pkg("pkg:a", "a", State.MISSING))
    agent = _FakeAgent()
    seen = {"pkg:a"}                  # already repaired this run
    _, _, repaired = repair_failed_nodes(
        g, agent, sandbox_execute=lambda c: (True, ""), ledger=_Ledger(),
        exec_readonly=lambda c: (1, ""), step_offset=0, cycle=1,
        repaired_ids=seen, max_repair=3, budget=5,
    )
    assert agent.tasks == [] and repaired == 0
```

(Note: `exec_readonly` must return an **integer** rc — `False`/`True` would be coerced by `returncode == 0`, since `bool` is an `int` subclass, and silently mis-certify.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_depgraph_live_repair.py -q`
Expected: FAIL — `cannot import name 'repair_failed_nodes'`.

- [ ] **Step 3: Implement `repair_failed_nodes`**

Add to `src/envstate/depgraph_live.py` (near `emit_drain`):

```python
def repair_failed_nodes(
    graph, build_agent, sandbox_execute, ledger, exec_readonly,
    *, step_offset: int, cycle: int, repaired_ids: set, max_repair: int = 3, budget: int = 5,
):
    """Host-first repair of reciped nodes the batch wave could not certify.

    For each failed reciped node not already in `repaired_ids` (capped at max_repair),
    frame a one-node Task and run a bounded host-first repair: BuildAgent.run with
    check=node.check_command and budget=budget — the LLM only proposes commands, the
    HOST check is the stop. Re-certify after each. One repair per node per run.
    Returns (new_graph, steps_consumed, repaired_count).
    """
    from python_deps.depgraph.emit import failed_reciped_nodes
    from src.envstate.world_model import Task

    steps = 0
    repaired = 0
    new = graph
    for node in failed_reciped_nodes(new):
        if repaired >= max_repair:
            break
        if node.id in repaired_ids:
            continue
        repaired_ids.add(node.id)
        task = Task(
            goal=f"Make the host check `{node.check_command}` succeed for "
                 f"{node.type.name} '{node.name}'. A batched install left it unsatisfied; "
                 f"read the error and provide whatever it needs (e.g. a system library).",
            done_when=node.check_command,
            layer=getattr(node.layer, "name", "pip").lower(),
            facts=(f"node: {node.id}",),
            target_node_ids=(node.id,),
        )
        report = build_agent.run(
            task, sandbox_execute, ledger, step_offset=step_offset + steps,
            check=node.check_command, budget=budget,
        )
        steps += len(report.commands)
        repaired += 1
        new = certify_refresh(new, exec_readonly, cycle)   # HOST flips state, not the LLM
    return new, steps, repaired
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_depgraph_live_repair.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Wire it into `_dep_emit_phase`**

In `src/envstate/orchestrator.py`, add a `run_v1` local next to the other scheduler locals (`_handed`, `_sched_stuck`):

```python
    _repaired_ids: set[str] = set()   # nodes given a host-first repair this run (one each)
```

Add `_repaired_ids` to `_dep_emit_phase`'s `nonlocal` line (it currently declares `nonlocal current_map, global_step`):

```python
        nonlocal current_map, global_step, _repaired_ids
```

Inside `_dep_emit_phase`, immediately after the `emit_drain(...)` block and its `if steps: global_step += steps` (~line 238), before the `sat = ...` fold, add (use a LOCAL import, matching the file's in-function import style and avoiding any import cycle):

```python
        # Host-first repair of reciped nodes the batch wave could not certify (the
        # broken-bridge fix, spec §4). Gated to the graph-scheduler arm so the off
        # path and legacy arms stay byte-identical.
        if enable_graph_scheduler:
            from src.envstate.depgraph_live import repair_failed_nodes
            graph, repair_steps, _repaired_n = repair_failed_nodes(
                graph, build_agent, sandbox_execute, ledger, exec_readonly,
                step_offset=global_step, cycle=cycle, repaired_ids=_repaired_ids,
            )
            if repair_steps:
                global_step += repair_steps
```

The arguments match the Step 3 signature exactly: `sandbox_execute` is the mutating executor for the repair's install commands, `ledger` is the real ledger in scope, and `exec_readonly` is used by the re-certify. `_repaired_n` (the repaired count) is consumed by Task 5's turn accounting.

- [ ] **Step 6: Write the off-path guard test**

Add to `tests/test_graph_scheduler_flag.py` (source-inspection, matching that file's style):

```python
def test_repair_is_gated_under_graph_scheduler():
    src = (_ROOT / "src" / "envstate" / "orchestrator.py").read_text()
    # repair_failed_nodes is only reachable under the enable_graph_scheduler guard
    idx_guard = src.index("if enable_graph_scheduler:\n            from src.envstate.depgraph_live import repair_failed_nodes")
    assert idx_guard > 0
```

(Adjust the matched string to the exact indentation written in Step 5.)

- [ ] **Step 7: Run the orchestrator/scheduler suites**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_depgraph_live_repair.py tests/test_graph_scheduler_flag.py tests/test_run_v1_dep_emit.py tests/test_graph_scheduler_wiring.py tests/test_depgraph_live_emit_drain.py tests/test_orchestrator_v1.py -q`
Expected: PASS — repair is additive and gated; off path unchanged.

- [ ] **Step 8: Verify; do NOT commit.**

> **Cross-layer note (reviewed):** the repair of a node like `psycopg2` runs the LLM's own loop, which installs the missing system lib (`libpq-dev`) AND re-runs `pip install psycopg2` before its host check, so the cross-layer fix completes *within* the repair — it does not depend on a second drain pass. If a genuinely separate dependent node remains MISSING, it resolves on the next cycle's drain. This is the accepted behavior of reusing `emit_drain` (drain-all → repair) rather than building a separate `run_wave` (spec §7 / self-review).

---

## Task 5: Turn accounting — LLM repairs only

**Files:**
- Modify: `src/envstate/orchestrator.py` (`run_v1`)
- Test: `tests/test_run_v1_turn_budget.py` (create)

**Interfaces:**
- Produces: a `run_v1` local `_repair_turns: int = max_cycles`, decremented per LLM repair. Deterministic waves and the test gate never touch it. When it reaches 0 the run gives up with reason `"graph-scheduler: LLM turn budget exhausted"`. `max_cycles` remains the hard iteration backstop.

**Reviewed fix:** `_dep_emit_phase` is a void nested function and cannot `return` from `run_v1`. It sets a flag; the main loop checks the flag and returns.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_v1_turn_budget.py — source-level guard
from pathlib import Path
_SRC = Path(__file__).resolve().parents[1] / "src" / "envstate" / "orchestrator.py"

def test_turn_budget_present_and_not_in_deterministic_drain():
    src = _SRC.read_text()
    assert "_repair_turns" in src
    assert "_budget_exhausted" in src
    assert "LLM turn budget exhausted" in src
    # the deterministic emit drain must not consume the turn budget
    emit = src[src.index("graph, _reports, steps = emit_drain"):src.index("# Fold emit-certified")]
    assert "_repair_turns" not in emit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_run_v1_turn_budget.py -q`
Expected: FAIL — `_repair_turns` not present.

- [ ] **Step 3: Implement**

In `run_v1`, near the scheduler locals, add:

```python
    _repair_turns: int = max_cycles      # LLM-repair budget (NOT mechanical installs)
    _budget_exhausted: bool = False
```

Add both to `_dep_emit_phase`'s `nonlocal` line: `nonlocal current_map, global_step, _repaired_ids, _repair_turns, _budget_exhausted`.

In `_dep_emit_phase`, after the Task-4 repair call, decrement by the repaired count and set the flag (do **not** return here):

```python
            if _repaired_n:
                _repair_turns -= _repaired_n
                if _repair_turns <= 0:
                    _budget_exhausted = True
```

(Capture the third return value as `_repaired_n` — replace the `_repaired_n` placeholder from Task 4 Step 5.)

In the **main loop body**, immediately after the `_dep_emit_phase(cycle)` call, add the giveup check (this is where a real return can happen):

```python
        _dep_emit_phase(cycle)
        if enable_graph_scheduler and _budget_exhausted:
            return current_map, "planner_giveup"
```

In the residual/discover task branch (`build_agent.run(...)` at ~377-383), pass `budget=5` so sufficiency repair is bounded like wave repair (spec §4), and after the call decrement and check:

```python
            check=(task.done_when if enable_graph_scheduler else None),
            budget=(5 if enable_graph_scheduler else LOCAL_BUDGET),
        )
        if enable_graph_scheduler:
            _repair_turns -= 1
            if _repair_turns <= 0:
                return current_map, "planner_giveup"
```

The deterministic drain (`emit_drain`) and the test gate never reference `_repair_turns`.

- [ ] **Step 4: Run test + scheduler suites**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_run_v1_turn_budget.py tests/test_depgraph_live_repair.py tests/test_graph_scheduler_wiring.py tests/test_run_v1_dep_emit.py tests/test_orchestrator_v1.py -q`
Expected: PASS.

- [ ] **Step 5: Verify; do NOT commit.**

---

## Deferred: single-OBSERVE consolidation (spec D5)

**Not implemented in this plan.** The spec's "one OBSERVE writer" is a structural/paper consolidation, not a behavioral change: the OBSERVE behavior (per-node certify in `_dep_emit_phase`, runtime-classify in `_runtime_ingest_phase`, `done_flag` via the maintainer) **already runs every cycle and is validated** (the config-fix e2e). Physically merging these into one function safely requires reproducing `run_v1`'s full branch/`continue` structure (the recipe branch writes `done_flag` then `continue`s before any end-of-cycle step, so a naive relocation drops `done_flag` on that path — a real bug the plan review caught). That restructuring is best done **inline, with the whole function visible**, not via a blind subagent against a prose spec. Revisit as a focused follow-up; the paper can describe the conceptual single OBSERVE today.

---

## Task 6: End-to-end validation

**Files:** none (validation only). Uses `agent.py` + `.env` (OpenRouter).

- [ ] **Step 1: Run the full unit suite**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_benchmark_arm_v1.py --ignore=tests/test_repo2run_benchmark.py --ignore=tests/test_repo2run_concurrency.py --ignore=tests/test_repo2run_dataset.py`
Expected: green except the known pre-existing `eval`-import collection errors (the four ignored files import only on the VM).

- [ ] **Step 2: Back up the prior summary, then run the memU-server e2e**

```bash
cd /Users/john/john-planner-v3
cp workplace/agent_run_summary.json rat_run_v1gs/agent_run_summary_configfix.json 2>/dev/null
python3 agent.py https://github.com/NevaMind-AI/memU-server \
  --model deepseek/deepseek-v4-flash --steps 30 --enable-graph-scheduler \
  > rat_run_v1gs/agent_memU_wave.log 2>&1
```

- [ ] **Step 3: Confirm the result held and the repair path is exercised**

Inspect `workplace/agent_run_summary.json`: expect `configuration_success=True`, `in_build_pass_rate >= 0.8` (genuine `python -m pytest -q`), the 87-package closure still cleared by the batch wave. Compare token/command counts against `rat_run_v1gs/agent_run_summary_configfix.json` — expect parity on the clean path (the repair path only activates on a wave failure). If a reciped install failed, confirm a bounded `check`-bearing repair ran (not a silent drop) and that no node was repaired twice (per-run `_repaired_ids`).

- [ ] **Step 4: Do NOT commit. Report the comparison.**

---

## Self-Review notes (author, post multi-agent review)

- **Spec coverage:** Task 1 = `next_deterministic_wave` (D1); Tasks 3+4 = `isolate`+repair (D2/D3, broken-bridge fix #1); Task 2 = host-first `budget` (D4); Task 5 = turn accounting (D6). **D5 (single OBSERVE) is deferred** (see above). `run_wave` is intentionally not a separate function — `emit_drain` already runs the batch wave; the cross-layer case completes inside the repair (Task 4 note).
- **Review fixes applied:** void-function giveup → `_budget_exhausted` flag checked in the main loop (Task 5); per-node cross-cycle repair memory → `_repaired_ids` (Task 4); test `_FakeLedger` → self-contained `_Ledger` stub / real `ActionLedger`; `exec_readonly` returns integer rc (not bool); dead instance monkeypatch removed (Task 2); `check_command=None` exclusion documented (Task 3); sufficiency repair bounded to `budget=5` (Task 5).
- **Deferred (spec §10):** learned-recipe cache, removing deprecated LLM Planner/Maintainer/contract-graph, proactive CONFIG/SERVICE provisioning, single-OBSERVE consolidation.
- **Risk note:** Tasks 4–5 touch `run_v1` (the most-tested loop). All changes are gated under `enable_graph_scheduler`; the off path returns at orchestrator.py:115-117. Each touches-orchestrator task re-runs `test_orchestrator_v1.py`.
