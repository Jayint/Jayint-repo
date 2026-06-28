# GSM Phase 2b — Slice A: Deterministic Engine Swap + Artifact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run_v3`'s deterministic emittable wave run through `compose_script → run_blocks → certify_refresh` (instead of `emit_drain`) and make the final Dockerfile spine graph-compiled (instead of ledger-replay) — all behind `enable_script_materialization` (default on; off = the pre-2b path), with v1 untouched and no LLM.

**Architecture:** A new `block_emit()` helper (new module) runs the graph-compiled blocks under a sandbox wrapper that dual-writes a minimal `ActionLedger` (the state-capture feed) alongside the typed `EvidenceBundle`. `run_v3`'s `_dep_emit_phase` calls it instead of `emit_drain` when the toggle is on. The finalizer sources the Dockerfile install spine from `compose_script(self._final_dep_graph)`; the existing pinned-closure + config + file-capture layers are retained and appended unchanged. The toggle off reverts to `emit_drain` + ledger-replay (the §14 B3 arm).

**Tech Stack:** Python 3 (`python3`), `pytest`, the existing `src/envstate/` orchestrator + agent, and the merged Phase-1/2a depgraph modules (`block`, `script`, `patch_gate`, `script_runner`, `depgraph_live`).

**Source design:** `docs/superpowers/specs/2026-06-28-gsm-phase2b-integration-design.md` (§3, §4, §5) and the master spec §18.

## Global Constraints

- **v1 is byte-identical.** Every change is behind `enable_script_materialization` (which implies the v3 arm). `emit_drain` (`depgraph_live.py:89`), `synthesis.build_commands_from_ledger` (`synthesis.py:149`), `BuildAgent.run`/`run_recipe`, and v1's `_dep_emit_phase` (`orchestrator.py:142`) are NOT modified.
- **Toggle semantics:** `enable_script_materialization` default **True** (= B5, the new path). False = B3 ablation → `_dep_emit_phase` keeps calling `emit_drain` and the finalizer keeps `build_commands_from_ledger`. The flag implies/rides the v3 arm (`enable_graph_scheduler`); it is independently settable off for the ablation.
- **No LLM in Slice A.** `block_emit` runs graph-compiled blocks deterministically; a failed block ends the wave (the repair loop is Slice B). Do NOT call `build_agent` from the block path.
- **State authority unchanged (invariants #3/#4):** only `certify_refresh` (inside `run_blocks`) writes `SATISFIED`; a block exiting 0 never certifies. The ledger dual-write records actions only; it never sets node state.
- **Evidence roles:** `EvidenceBundle` = typed graph truth; the dual-written `ActionLedger` = state-capture feed for the retained captures + `_runtime_ingest_phase`. Mirror BOTH successful and failed block commands (failures feed `_runtime_ingest_phase`, which reads `ledger.events()` with `rc != 0` at `orchestrator.py:441`).
- **Reuse, don't reimplement:** `compose_script` (`patch_gate.py`), `run_blocks` (`script_runner.py`), `render_setup_sh` (`script.py`), `certify_refresh` (`depgraph_live.py`), `ActionLedger.append`/`ActionEvent` (`ledger.py`).
- **Git hygiene:** `git add` only the exact files each task creates/modifies — NEVER `git add -A`/`.`/`<dir>`. Conventional commit messages with an Observation/Why/What/Verification body. **No `Co-Authored-By` trailer.** Do not push.

### Verified integration points (grounded against the live tree 2026-06-28)

```python
# src/envstate/orchestrator.py
def run_v3(...) -> tuple[WorldModelMap, stop_reason]              # :317  (closures: current_map, global_step, ledger, sandbox_execute, exec_readonly, ...)
#   nested def _dep_emit_phase(cycle): ...                        # :380
#       graph = certify_refresh(current_map.dep_graph, exec_readonly, cycle)         # :392
#       graph, _reports, steps = emit_drain(graph, build_agent, sandbox_execute,     # :397-400  <-- REPLACE under toggle
#                                            ledger, exec_readonly, step_offset=global_step, cycle=cycle)
#       graph, repair_steps, _repaired_n = repair_failed_nodes(...)                  # :406-409  <-- LLM repair; skip under toggle (Slice B re-adds)
#       ... fold satisfied PACKAGE facts into installed; merge_map(...)              # :416-429
#   dead branch (scheduler never emits apply_recipe_patch):       # :603-639  <-- DELETE (Task 5)
#   loop calls _dep_emit_phase(cycle)                             # :563

# src/envstate/depgraph_live.py
def emit_drain(graph, build_agent, sandbox_execute, ledger, exec_readonly, *, step_offset, cycle) -> (graph, reports, steps)   # :89
def certify_refresh(graph, exec_readonly, cycle, *, allow_service_certify=None)     # :46

# src/python_deps/depgraph/patch_gate.py
def compose_script(graph, manual_blocks=()) -> tuple[Block, ...]
# src/envstate/script_runner.py
def run_blocks(blocks, sandbox_execute, exec_readonly, graph, cycle, *, container_kind="canonical") -> (graph, EvidenceBundle, failed_block_id)
# src/python_deps/depgraph/script.py
def render_setup_sh(blocks) -> str

# src/envstate/ledger.py
class ActionEvent:  cmd:str  rc:int  stdout:str=""  ...  mutation_class:str|None=None      # :7  (frozen)
class ActionLedger:  def append(self, event: ActionEvent) -> None    # :58 ;  def events(self) -> tuple[ActionEvent,...]  # :61

# agent.py
self.enable_graph_scheduler = ...                                  # :333  (flag cascade :333-345)
final_map, stop_reason = _run_v3_loop(...)                         # :1333
self._final_dep_graph = getattr(final_map, "dep_graph", None)      # :1367  <-- finalizer's compose_script input
def _finalize_supervisor_artifacts(self, configuration_success):  # :1638 -> _synthesize_final_build_recipe(drop_replayed_state=True)
def _synthesize_final_build_recipe(self, drop_replayed_state=False):   # :2225 ; ledger path :2235-2263 ; source set :2262
```

executor callables: `sandbox_execute: Callable[[str], tuple[bool, str]]` (mutating), `exec_readonly: Callable[[str], tuple[int, str]]` (read-only).

---

### Task 1: `enable_script_materialization` flag + cascade + run_v3 param (plumbed, unused)

**Files:**
- Modify: `agent.py` (constructor signature + cascade near `:333-345`; pass-through at `:1333`)
- Modify: `src/envstate/orchestrator.py` (`run_v3` signature `:317`)
- Test: `tests/test_script_materialization_flag.py`

**Interfaces:**
- Produces: `self.enable_script_materialization: bool` on the agent, and a `run_v3(..., enable_script_materialization: bool = True)` parameter. Plumbed but NOT yet consumed (no behavior change — Task 3 consumes it).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_script_materialization_flag.py
import inspect
from agent import DockerAgent
from src.envstate import orchestrator


def test_flag_defaults_on_with_graph_scheduler():
    inst = DockerAgent(enable_graph_scheduler=True)            # real construction
    assert inst.enable_script_materialization is True


def test_flag_off_when_explicitly_disabled():
    inst = DockerAgent(enable_graph_scheduler=True, enable_script_materialization=False)
    assert inst.enable_script_materialization is False


def test_run_v3_accepts_the_param():
    sig = inspect.signature(orchestrator.run_v3)
    assert "enable_script_materialization" in sig.parameters
    assert sig.parameters["enable_script_materialization"].default is True
```

> **Implementer note:** match the existing constructor-test pattern in the suite (several tests build `DockerAgent(...)` directly; if real construction needs more args, copy the minimal arg set from an existing `DockerAgent(...)` test). The two flag tests assert the cascade; the third asserts the orchestrator param exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_script_materialization_flag.py -q`
Expected: FAIL (`AttributeError: ... enable_script_materialization` and/or the param missing).

- [ ] **Step 3: Implement**

In `agent.py` constructor: add parameter `enable_script_materialization: bool | None = None` and, in the cascade block (after `:345`):

```python
# Script-materialization (Slice A): default ON whenever the graph scheduler is on
# (B5 = compiled setup.sh drives execution + artifact). Independently settable OFF
# for the §14 B3 ablation (revert to emit_drain + ledger-replay).
self.enable_script_materialization = (
    self.enable_graph_scheduler if enable_script_materialization is None
    else bool(enable_script_materialization)
)
```

In `agent.py` at the `run_v3` call (`:1333`), pass `enable_script_materialization=self.enable_script_materialization`.

In `src/envstate/orchestrator.py`, add `enable_script_materialization: bool = True` to `run_v3`'s signature (`:317`). Do NOT consume it yet.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_script_materialization_flag.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent.py src/envstate/orchestrator.py tests/test_script_materialization_flag.py
git commit -m "feat(v3): enable_script_materialization flag + cascade (plumbed, unused)"
```

---

### Task 2: `block_emit()` — deterministic block run + ledger dual-write (new module)

**Files:**
- Create: `src/envstate/block_emit.py`
- Test: `tests/envstate/test_block_emit.py`

**Interfaces:**
- Consumes: `compose_script` (`patch_gate`), `run_blocks` (`script_runner`), `ActionLedger`/`ActionEvent` (`ledger`).
- Produces: `block_emit(graph, sandbox_execute, exec_readonly, ledger, cycle, *, manual_blocks=()) -> tuple[DepGraph, EvidenceBundle, str | None]` — compiles the graph to blocks, runs them via `run_blocks` under a sandbox wrapper that mirrors EVERY block command (success + failure) into `ledger` as an `ActionEvent`, and returns `(certified_graph, evidence, failed_block_id)`. Pure of LLM/Docker imports (the executors are injected). Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/envstate/test_block_emit.py
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from src.envstate.block_emit import block_emit
from src.envstate.ledger import ActionLedger


def _graph():
    return DepGraph().with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB,
        name="libpq.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING, check_command="ldconfig -p | grep -q libpq",
        chosen_fix="apt:libpq-dev"))


def test_runs_blocks_certifies_and_dual_writes_ledger():
    led = ActionLedger()
    def sandbox(cmd): return (True, "installed")
    def ro(cmd): return (0, "libpq") if "ldconfig" in cmd else (1, "")
    graph, bundle, failed = block_emit(_graph(), sandbox, ro, led, cycle=1)
    assert failed is None
    assert graph.get("syslib:libpq.so").state is State.SATISFIED      # host check certified
    assert len(bundle.items) == 1                                     # typed evidence
    # dual-write: the install command is mirrored into the ledger
    assert any("libpq-dev" in e.cmd and e.rc == 0 for e in led.events())


def test_failed_block_is_recorded_in_ledger_with_rc_nonzero():
    led = ActionLedger()
    def sandbox(cmd): return (False, "E: package not found")
    def ro(cmd): return (1, "")
    graph, bundle, failed = block_emit(_graph(), sandbox, ro, led, cycle=1)
    assert failed == "system.libpq.so"
    assert any(e.rc != 0 for e in led.events())                      # failures feed runtime_ingest


def test_block_rc0_without_check_does_not_certify():
    led = ActionLedger()
    def sandbox(cmd): return (True, "ok")
    def ro(cmd): return (1, "absent")
    graph, _b, _f = block_emit(_graph(), sandbox, ro, led, cycle=1)
    assert graph.get("syslib:libpq.so").state is not State.SATISFIED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/envstate/test_block_emit.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.block_emit'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/envstate/block_emit.py
"""Deterministic block-emit phase for v3 (design §5.1): compile the graph to blocks,
run them, certify via host checks, and dual-write a minimal ActionLedger (the
state-capture feed) alongside the typed EvidenceBundle. NO LLM. The v3 analog of
emit_drain, but graph-compiled and free of build_agent."""
from __future__ import annotations

from typing import Callable

from python_deps.depgraph.patch_gate import compose_script
from src.envstate.script_runner import run_blocks
from src.envstate.ledger import ActionLedger, ActionEvent


def block_emit(
    graph,
    sandbox_execute: Callable[[str], tuple[bool, str]],
    exec_readonly: Callable[[str], tuple[int, str]],
    ledger: ActionLedger,
    cycle: int,
    *,
    manual_blocks: tuple = (),
):
    """Run the graph-compiled blocks; mirror each command into `ledger`; certify via
    run_blocks' host checks. Returns (certified_graph, EvidenceBundle, failed_block_id)."""
    blocks = compose_script(graph, manual_blocks)

    def _mirroring_sandbox(cmd: str) -> tuple[bool, str]:
        ok, out = sandbox_execute(cmd)
        # State-capture feed: mutating block commands are env changes; failures (rc!=0)
        # feed _runtime_ingest_phase. State is NEVER set here — only certify writes SATISFIED.
        ledger.append(ActionEvent(cmd=cmd, rc=0 if ok else 1, stdout=out or "",
                                  mutation_class="file_or_env_change"))
        return ok, out

    return run_blocks(blocks, _mirroring_sandbox, exec_readonly, graph, cycle)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/envstate/test_block_emit.py -q`
Expected: PASS (3 tests). Confirm `ActionEvent`'s field names (`cmd`, `rc`, `stdout`, `mutation_class`) match `ledger.py:7`; if a field is required without default, supply it.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/block_emit.py tests/envstate/test_block_emit.py
git commit -m "feat(v3): block_emit — deterministic block run + ledger dual-write (no LLM)"
```

---

### Task 3: wire `block_emit` into `run_v3._dep_emit_phase` behind the toggle

**Files:**
- Modify: `src/envstate/orchestrator.py` (`run_v3._dep_emit_phase`, `:380-429`)
- Test: `tests/test_v3_block_emit_wiring.py`

**Interfaces:**
- Consumes: `block_emit` (Task 2), the `enable_script_materialization` run_v3 param (Task 1).
- Produces: under the toggle, v3's `_dep_emit_phase` certifies the emittable wave via `block_emit` (not `emit_drain`/`repair_failed_nodes`); the satisfied-PACKAGE fold + `merge_map` (`:416-429`) are unchanged. Toggle off → existing `emit_drain` path verbatim.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v3_block_emit_wiring.py
import src.envstate.depgraph_live as dl
import src.envstate.block_emit as be
from src.envstate import orchestrator
from <existing run_v3 harness> import build_run_v3_inputs   # see implementer note


def _spy(target_module, name, calls, key):
    real = getattr(target_module, name)
    def wrapper(*a, **k):
        calls.append(key)
        return real(*a, **k)
    return wrapper


def test_toggle_on_uses_block_emit(monkeypatch):
    calls = []
    monkeypatch.setattr(be, "block_emit", _spy(be, "block_emit", calls, "block"))
    monkeypatch.setattr(dl, "emit_drain", _spy(dl, "emit_drain", calls, "drain"))
    inputs = build_run_v3_inputs(single_missing_emittable_node=True)   # MISSING node, host check passes
    final_map, _ = orchestrator.run_v3(**inputs, enable_script_materialization=True)
    assert "block" in calls and "drain" not in calls
    assert final_map.dep_graph.get("syslib:libpq.so").state.name == "SATISFIED"
    assert any("libpq-dev" in e.cmd for e in inputs["ledger"].events())


def test_toggle_off_uses_emit_drain(monkeypatch):
    calls = []
    monkeypatch.setattr(be, "block_emit", _spy(be, "block_emit", calls, "block"))
    monkeypatch.setattr(dl, "emit_drain", _spy(dl, "emit_drain", calls, "drain"))
    inputs = build_run_v3_inputs(single_missing_emittable_node=True)
    orchestrator.run_v3(**inputs, enable_script_materialization=False)
    assert "drain" in calls and "block" not in calls
```

> **Implementer note:** `run_v3` has a large signature — there is no shared `build_run_v3_inputs` helper today; construct the inputs by copying the run_v1 setup in `tests/test_orchestrator_v1.py` / `tests/test_orchestrator_v1_snapshot.py` and adapting to `run_v3`'s parameters (define a local `build_run_v3_inputs` in this test file, or inline the construction). Spy via monkeypatch on `src.envstate.depgraph_live.emit_drain` and `src.envstate.block_emit.block_emit` — both are imported LAZILY inside `_dep_emit_phase` (`orchestrator.py:388` / the new branch), so patching the module attribute is picked up at call time. Keep the two assertions concrete (correct engine ran; node SATISFIED + ledger mirrored when on).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_v3_block_emit_wiring.py -q`
Expected: FAIL (toggle-on still calls `emit_drain`; `block_emit` never invoked).

- [ ] **Step 3: Implement**

In `src/envstate/orchestrator.py`, inside `run_v3._dep_emit_phase` (`:380`), replace the `emit_drain` + `repair_failed_nodes` block (`:397-415`) with a toggle branch. After the `certify_refresh` at `:392`:

```python
        if enable_script_materialization:
            # Slice A: deterministic block run replaces emit_drain on v3 (design §5.1).
            # No LLM and no host-repair here — a failed block ends the wave (Slice B
            # adds the repair loop). compose_script handles the emittable wave.
            from src.envstate.block_emit import block_emit
            graph, _bundle, _failed = block_emit(
                graph, sandbox_execute, exec_readonly, ledger, cycle,
            )
        else:
            graph, _reports, steps = emit_drain(
                graph, build_agent, sandbox_execute, ledger, exec_readonly,
                step_offset=global_step, cycle=cycle,
            )
            if steps:
                global_step += steps
            from src.envstate.depgraph_live import repair_failed_nodes
            graph, repair_steps, _repaired_n = repair_failed_nodes(
                graph, build_agent, sandbox_execute, ledger, exec_readonly,
                step_offset=global_step, cycle=cycle, repaired_ids=_repaired_ids,
            )
            if repair_steps:
                global_step += repair_steps
            if _repaired_n:
                _repair_turns -= _repaired_n
                if _repair_turns <= 0:
                    _budget_exhausted = True
```

Leave the satisfied-PACKAGE fold + `merge_map` (`:416-429`) unchanged (it runs for both branches). Add `enable_script_materialization` to the `nonlocal`/closure visibility as needed (it is a `run_v3` parameter, so it is in scope).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_v3_block_emit_wiring.py -q`
Expected: PASS (2 tests). Then `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py -q` — v1 + the toggle-off v3 path unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_v3_block_emit_wiring.py
git commit -m "feat(v3): _dep_emit_phase drives block_emit under enable_script_materialization"
```

---

### Task 4: finalizer — graph-compiled Dockerfile spine (artifact switch)

**Files:**
- Modify: `agent.py` (`_synthesize_final_build_recipe`, `:2235`)
- Test: `tests/test_v3_artifact_source.py`

**Interfaces:**
- Consumes: `self.enable_script_materialization` (Task 1), `self._final_dep_graph` (`agent.py:1367`), `compose_script`, `render_setup_sh`.
- Produces: when the toggle is on and `self._final_dep_graph` is present, the build recipe's `build_commands` are the graph-compiled block command lines (source `"compiled_setup_sh"`), and the rendered `setup.sh` is persisted as the audit/replay artifact. The retained captures (`_emit_closure_recipe` pin, `_emit_interleaved_state_recipe` file) run after, unchanged. Toggle off → existing `"action_ledger"` path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v3_artifact_source.py
from agent import DockerAgent
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy


def _graph():
    return DepGraph().with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB,
        name="libpq.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
        state=State.SATISFIED, check_command="ldconfig -p | grep -q libpq",
        chosen_fix="apt:libpq-dev"))


def _agent(materialize, graph):
    a = object.__new__(DockerAgent)               # __new__ bypass (existing test pattern)
    a.enable_envstate = True
    a.enable_script_materialization = materialize
    a._final_dep_graph = graph
    a.action_ledger = _MinimalLedger()            # non-None so the ledger branch is reachable
    a.synthesizer = _SpySynth()
    return a


def test_v3_artifact_source_is_compiled_setup_sh():
    a = _agent(True, _graph())
    a._synthesize_final_build_recipe(drop_replayed_state=True)
    assert a.build_recipe_source == "compiled_setup_sh"
    assert any("libpq-dev" in c for c in a.synthesizer.applied["build_commands"])


def test_toggle_off_keeps_action_ledger_source():
    a = _agent(False, _graph())
    a.action_ledger = _LedgerWithOneInstall()     # so build_commands_from_ledger yields a command
    a._synthesize_final_build_recipe(drop_replayed_state=True)
    assert a.build_recipe_source == "action_ledger"
```

> **Implementer note:** define the small fakes (`_SpySynth.apply_build_recipe` records the dict; `_MinimalLedger`/`_LedgerWithOneInstall` mimic `ActionLedger.events()`) in the test file. Use the `object.__new__(DockerAgent)` bypass already used across the suite to avoid heavy `__init__`. The compiled branch must NOT require `self.client`/`self.model` (it returns before the LLM synth fallback).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_v3_artifact_source.py -q`
Expected: FAIL (`build_recipe_source` is `"action_ledger"`/None, not `"compiled_setup_sh"`).

- [ ] **Step 3: Implement**

In `agent.py` `_synthesize_final_build_recipe` (`:2225`), insert the v3 branch BEFORE the existing `if getattr(self, "enable_envstate", False) and self.action_ledger is not None:` ledger block (`:2235`):

```python
        if (getattr(self, "enable_script_materialization", False)
                and getattr(self, "_final_dep_graph", None) is not None):
            # v3 (design §5.2): the compiled setup.sh is the install spine — graph-sourced,
            # NOT ledger replay (invariant #1 / §18 #2). The pinned closure + config + file
            # captures are appended afterward by _emit_closure_recipe / _emit_interleaved_state_recipe.
            from python_deps.depgraph.patch_gate import compose_script
            from python_deps.depgraph.script import render_setup_sh
            blocks = compose_script(self._final_dep_graph)
            setup_sh = render_setup_sh(blocks)
            self._persist_setup_sh(setup_sh)                     # audit/replay artifact (see below)
            build_commands = [c for b in blocks for c in b.commands]
            self.synthesizer.apply_build_recipe({
                "build_commands": build_commands,
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": [],
                "excluded_commands": [],
                "rationale": "Compiled from the certified dep-graph (setup.sh spine).",
                "confidence": "high",
            })
            self.build_recipe = {"build_commands": build_commands, "source": "compiled_setup_sh"}
            self.build_recipe_source = "compiled_setup_sh"
            return True
```

Add a tiny `_persist_setup_sh(self, text)` helper that writes `setup.sh` into `self.logs_dir` (guard `getattr(self, "logs_dir", None)`; no-op if absent) — it is the audit/replay artifact, not required for the Dockerfile. Keep the existing ledger block and LLM fallback untouched (they serve v1 + toggle-off).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_v3_artifact_source.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_v3_artifact_source.py
git commit -m "feat(v3): finalizer sources Dockerfile spine from compiled setup.sh (retain pin/config/file)"
```

---

### Task 5: delete the dead `apply_recipe_patch` branch in v3

**Files:**
- Modify: `src/envstate/orchestrator.py` (delete `:603-639`)
- Test: `tests/test_v3_no_recipe_patch_branch.py`

**Interfaces:**
- The v3 scheduler (`graph_scheduler.next_decision`) only returns `action in {"task","done"}`, so the `apply_recipe_patch` branch is unreachable. Deleting it removes v3's last `RecipePatch` dependency.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v3_no_recipe_patch_branch.py
import inspect
import src.envstate.orchestrator as orch


def test_run_v3_has_no_apply_recipe_patch_branch():
    src = inspect.getsource(orch.run_v3)
    assert "apply_recipe_patch" not in src, "dead v3 apply_recipe_patch branch must be removed"


def test_scheduler_only_emits_task_or_done():
    # next_decision returns action in {"task","done"} — guard the assumption that justifies the delete
    from src.envstate.graph_scheduler import next_decision  # noqa: F401
    import src.envstate.graph_scheduler as gs
    s = inspect.getsource(gs)
    assert 'action="apply_recipe_patch"' not in s and "'apply_recipe_patch'" not in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_v3_no_recipe_patch_branch.py -q`
Expected: FAIL (`apply_recipe_patch` still present in `run_v3` source).

- [ ] **Step 3: Implement**

Delete the `elif decision.action == "apply_recipe_patch":` branch and its body in `run_v3` (`orchestrator.py:603-639`). If `RecipePatch` is now unused in `orchestrator.py` imports (`:30`), remove that import too (confirm with `grep -n RecipePatch src/envstate/orchestrator.py` — v1's branch at `:243-277` may still use it; if so, keep the import). Do NOT touch v1's recipe branch.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_v3_no_recipe_patch_branch.py -q`
Expected: PASS (2 tests). Then `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py -q` — green (v1 unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_v3_no_recipe_patch_branch.py
git commit -m "refactor(v3): delete dead apply_recipe_patch branch (scheduler emits only task/done)"
```

---

### Task 6: L1 seam integration test (CI-safe) + real-container driver (manual gate)

**Files:**
- Create: `tests/envstate/test_sliceA_seam_integration.py` (FakeExecutor, CI-safe)
- Create: `scripts/l1_engine_swap_smoke.py` (real-container driver — manual validation)

**Interfaces:**
- The seam test exercises the full Slice-A deterministic chain in-process; the driver runs the same chain against a real container on one repo (the validation gate before relying on A).

- [ ] **Step 1: Write the seam integration test**

```python
# tests/envstate/test_sliceA_seam_integration.py
"""Slice A deterministic chain end-to-end with a FakeExecutor (no Docker, no LLM):
graph -> block_emit (compose_script -> run_blocks -> certify + ledger dual-write)
      -> compose_script/render_setup_sh artifact spine."""
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from python_deps.depgraph.patch_gate import compose_script
from python_deps.depgraph.script import render_setup_sh
from src.envstate.block_emit import block_emit
from src.envstate.ledger import ActionLedger


def _graph():
    return DepGraph().with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB,
        name="libpq.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING, check_command="ldconfig -p | grep -q libpq",
        chosen_fix="apt:libpq-dev"))


def test_block_emit_then_artifact_spine():
    led = ActionLedger()
    graph, bundle, failed = block_emit(_graph(), lambda c: (True, "ok"),
                                       lambda c: (0, "") if "ldconfig" in c else (1, ""),
                                       led, cycle=1)
    assert failed is None and graph.get("syslib:libpq.so").state is State.SATISFIED
    # the artifact the finalizer would emit for this graph:
    spine = render_setup_sh(compose_script(graph))
    assert "apt-get install -y --no-install-recommends libpq-dev" in spine
    assert "#@check ldconfig -p | grep -q libpq" in spine
    assert "SATISFIED" not in spine                                  # script carries no state
    assert any("libpq-dev" in e.cmd for e in led.events())          # dual-write happened
```

- [ ] **Step 2: Run it (expect PASS — characterization over Tasks 2)**

Run: `python3 -m pytest tests/envstate/test_sliceA_seam_integration.py -q`
Expected: PASS (1 test).

- [ ] **Step 3: Write the real-container driver (manual gate)**

```python
# scripts/l1_engine_swap_smoke.py
"""L1 validation: run the Slice-A deterministic chain against a REAL container on one repo.
Manual gate (NOT in CI — Docker is slow/flaky here). Usage:
    python3 scripts/l1_engine_swap_smoke.py <repo_path> <base_image>
Binds run_blocks' executor callables to a real `docker exec` adapter (reuse the existing
orchestrator/build_agent exec plumbing), builds the dep-graph via build_dep_graph, and asserts
compose_script -> block_emit certifies the emittable wave and render_setup_sh provisions it."""
# Implementer: wire build_dep_graph(repo) + the existing docker exec adapter (the same
# sandbox_execute/exec_readonly the orchestrator already constructs) into block_emit, print
# the per-node certified states + the rendered setup.sh. Keep it a thin driver; reuse, do not
# reimplement, the container adapter.
```

> **Implementer note:** this driver is a documented manual validation step — it imports the existing container exec adapter and `build_dep_graph`. Do NOT add it to the pytest suite (no Docker in CI). Its purpose is the human-run L1 gate before Slice A is relied on. If the exact exec-adapter entry point is unclear, report it and I will point you at it — do not guess a Docker API.

- [ ] **Step 4: Commit**

```bash
git add tests/envstate/test_sliceA_seam_integration.py scripts/l1_engine_swap_smoke.py
git commit -m "test(v3): Slice-A seam integration (CI-safe) + L1 real-container smoke driver"
```

---

### Task 7: full-suite regression + toggle-off-unchanged gate

**Files:**
- Test: (this task runs the gates; no new files)

- [ ] **Step 1: Full-suite regression**

Run: `python3 -m pytest tests -q -p no:cacheprovider`
Expected: only the 4 known pre-existing failures remain (`test_adapter_logic` nested_pytester, `test_repo2run_dataset` ×2, `test_runtime_pin_seam` floor-trap), 0 new. Any NEW failure → investigate before proceeding.

- [ ] **Step 2: Prove v1 + toggle-off v3 unchanged**

Run: `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_contract_graph_v2_integration.py -q`
Expected: green — v1 and the toggle-off (B3) path are behaviorally unchanged (no edits to `emit_drain`/`synthesis`/`BuildAgent`).

- [ ] **Step 3: Record the result**

Append the pass/fail tally + the exact failing-test names to the run report. If only the 4 known failures remain, Slice A is regression-clean.

---

## Slice A done-definition

- `run_v3` with `enable_script_materialization=True` provisions the emittable wave via `block_emit` (compose_script → run_blocks → certify), dual-writes the ledger, and emits a graph-compiled (`source="compiled_setup_sh"`) Dockerfile spine with the pin/config/file captures retained.
- Toggle off reverts to `emit_drain` + ledger-replay (B3); v1 byte-identical.
- The dead `apply_recipe_patch` branch is gone.
- Seam integration test green; L1 real-container driver available for the manual gate.
- Full suite green except the 4 known pre-existing failures.

## After Slice A (separate plans — do NOT start here)

- **L1 manual gate:** run `scripts/l1_engine_swap_smoke.py` on 1–2 real repos; confirm `setup.sh` provisions + certifies before relying on A. Then **re-baseline v3** (B5 vs B3 arm).
- **Slice B** — structured repair loop: RepairScope builder; BuildAgent v3 mode (ReAct read-only diagnose → fenced `PatchProposal` JSON); the bounded repair loop calling `validate_proposal → apply_proposal → compose_script → re-run`; thread accumulated `manual_blocks` into both `block_emit` and the finalizer's `compose_script`; carry the 2a-review hardening MUSTs (read-only allowlist, provider/promotion upgrade semantics, non-empty ScriptPatch targets, `provides` validation, widened ACTION_CLASSES, parse robustness).
- **Slice C** — config/service LLM classifier → soft-hint proposals; generalize `schedule._is_actionable`; §5.2-bundle fidelity fixes.
