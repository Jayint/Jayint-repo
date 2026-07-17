# GSM Phase 2b — Slice A: Deterministic Engine Swap + Artifact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run_v3`'s deterministic emittable wave run through `compose_script → run_blocks → certify_refresh` (instead of `emit_drain`) and make the final Dockerfile spine graph-compiled (instead of ledger-replay) — all behind `enable_script_materialization` (default on; off = the pre-2b path), with v1 untouched and no LLM.

**Architecture:** A new `block_emit()` helper (new module) runs the graph-compiled blocks under a sandbox wrapper that dual-writes a minimal `ActionLedger` (the state-capture feed) alongside the typed `EvidenceBundle`. `run_v3`'s `_dep_emit_phase` calls it instead of `emit_drain` when the toggle is on. The finalizer sources the Dockerfile install spine from a new **state-independent** `compile_replay_blocks(self._final_dep_graph)` (the emit-phase `compile_blocks` only emits MISSING nodes, so it yields nothing once everything is certified); the existing pinned-closure + config + file-capture layers are retained and appended unchanged. The toggle off reverts to `emit_drain` + ledger-replay (the §14 B3 arm).

**Tech Stack:** Python 3 (`python3`), `pytest`, the existing `src/envstate/` orchestrator + agent, and the merged Phase-1/2a depgraph modules (`block`, `emit`, `script`, `patch_gate`, `script_runner`, `depgraph_live`).

**Source design:** `docs/superpowers/specs/2026-06-28-gsm-phase2b-integration-design.md` (§3, §4, §5) and the master spec §18.

## Global Constraints

- **v1 is byte-identical.** Every change is behind `enable_script_materialization` (which implies the v3 arm). `emit_drain` (`depgraph_live.py:89`), `synthesis.build_commands_from_ledger` (`synthesis.py:149`), `BuildAgent.run`/`run_recipe`, and v1's `run_v1`/recipe branch are NOT modified.
- **Toggle semantics:** `enable_script_materialization` default **True** (= B5, the new path). False = B3 ablation → `_dep_emit_phase` keeps calling `emit_drain` + `repair_failed_nodes` and the finalizer keeps `build_commands_from_ledger`. The flag rides the v3 arm (`enable_graph_scheduler`); it is independently settable off for the ablation.
- **No LLM in Slice A.** `block_emit` runs graph-compiled blocks deterministically; a failed block ends the wave (the repair loop is Slice B). Do NOT call `build_agent` from the block path.
- **State authority unchanged (invariants #3/#4):** only `certify_refresh` (inside `run_blocks`) writes `SATISFIED`; a block exiting 0 never certifies. The ledger dual-write records actions only; it never sets node state.
- **Evidence roles:** `EvidenceBundle` = typed graph truth; the dual-written `ActionLedger` = state-capture feed for the retained captures + `_runtime_ingest_phase`. Mirror BOTH successful and failed block commands. Mirroring **failures** is intentional and required: `_runtime_ingest_phase` reads `ledger.events()` filtered to `rc != 0` (`orchestrator.py:441`) to discover new nodes — the pre-2b `emit_drain` path fed it the same way, so the block path must too to keep the discovery loop alive. (This extends the spec §5.1 wording "on a successful block command"; the spec is being updated to "on each block command (success or failure)".)
- **Reuse, don't reimplement:** `compose_script` (`patch_gate.py`), `compile_blocks`/`_is_reciped`/`topo_order`/`_command_for`/`_block_id_for` (`block.py`/`emit.py`), `run_blocks` (`script_runner.py`), `render_setup_sh` (`script.py`), `certify_refresh` (`depgraph_live.py`), `ActionLedger.append`/`ActionEvent` (`ledger.py`).
- **Git hygiene:** `git add` only the exact files each task creates/modifies — NEVER `git add -A`/`.`/`<dir>`. Conventional commit messages with an Observation/Why/What/Verification body. **No `Co-Authored-By` trailer.** Do not push.

### Verified integration points (grounded against the live tree 2026-06-28)

```python
# src/envstate/orchestrator.py
def run_v3(build_agent, maintainer, initial_world_map, ledger, sandbox_execute,    # :317
           max_cycles=MAX_CYCLES, on_cycle=None, *, probe=None, manifest=None,
           exec_readonly=None, enable_dep_emit=True, enable_runtime_feedback=True,
           graph_scheduler_attempt_cap=3)                # NOTE: run_v3 has NO enable_graph_scheduler param — it IS the v3 arm.
#   nested def _dep_emit_phase(cycle):                            # :380
#       nonlocal current_map, global_step, _repaired_ids, _repair_turns, _budget_exhausted   # :381
#       ... guards (enable_dep_emit / dep_graph None / exec_readonly None)        # :382-385
#       from src.envstate.depgraph_live import certify_refresh, emit_drain, ensure_python_shim  # :388 (LAZY, inside the closure)
#       ensure_python_shim(sandbox_execute)                      # :391  <-- KEEP (runs in both branches)
#       graph = certify_refresh(current_map.dep_graph, exec_readonly, cycle)      # :392  <-- KEEP (runs in both branches)
#       graph, _reports, steps = emit_drain(...)                 # :397-400  <-- REPLACE-under-toggle span START
#       if steps: global_step += steps                           # :401-402
#       from src.envstate.depgraph_live import repair_failed_nodes                # :405
#       graph, repair_steps, _repaired_n = repair_failed_nodes(..., repaired_ids=_repaired_ids)  # :406-409
#       if repair_steps: global_step += repair_steps             # :410-411
#       if _repaired_n: _repair_turns -= _repaired_n; ... _budget_exhausted=True  # :412-415  <-- REPLACE-under-toggle span END
#       ... satisfied-PACKAGE fold (Fact) + merge_map(...)       # :416-429  <-- KEEP (runs in both branches)
#   dead branch: if decision.action == "apply_recipe_patch": ... continue   # comment :602, body :603-639  <-- DELETE (Task 6)
#   loop calls _dep_emit_phase(cycle)                            # :563

# src/envstate/depgraph_live.py
def emit_drain(graph, build_agent, sandbox_execute, ledger, exec_readonly, *, step_offset, cycle, max_drain=4)  # :89
def certify_refresh(graph, exec_readonly, cycle, *, allow_service_certify=None)   # :39
def repair_failed_nodes(graph, build_agent, sandbox_execute, ledger, exec_readonly, *, step_offset, cycle, repaired_ids) -> (graph, steps, n)

# src/python_deps/depgraph/patch_gate.py
def compose_script(graph, manual_blocks=()) -> tuple[Block, ...]         # = compile_blocks(graph) ∪ governed manual blocks (MISSING wave only)
# src/python_deps/depgraph/block.py
def compile_blocks(graph) -> tuple[Block, ...]                            # emit phase: ONLY partition().emittable (state==MISSING)
#   (NEW in Task 4) def compile_replay_blocks(graph) -> tuple[Block, ...] # artifact: ALL _is_reciped nodes, state-independent
# src/python_deps/depgraph/emit.py
def _is_reciped(node) -> bool          # PACKAGE w/ version OR SYSTEM_LIB/TOOL w/ apt: fix — state-independent
def topo_order(graph, nodes) -> tuple[Node, ...]
# src/envstate/script_runner.py
def run_blocks(blocks, sandbox_execute, exec_readonly, graph, cycle, *, container_kind="canonical") -> (graph, EvidenceBundle, failed_block_id)
# src/python_deps/depgraph/script.py
def render_setup_sh(blocks) -> str     # emits per block: "#@action ...", "#@targets ...", "#@check <chk>", then the command

# src/envstate/ledger.py
@dataclass(frozen=True)
class ActionEvent:  step:int  cmd:str  rc:int  task_id=None  stdout:str=""  ...  mutation_class:str|None=None  # :7
#   ^^^ step IS REQUIRED and FIRST (no default). ActionEvent(cmd=..., rc=...) WITHOUT step raises TypeError.
class ActionLedger:  def append(self, event)  # :58 ;  def events(self) -> tuple[ActionEvent,...]  # :61 ;  internal list self._events

# agent.py
self.enable_graph_scheduler: bool = bool(enable_graph_scheduler)         # :333  (flag cascade :333-353)
final_map, stop_reason = ... run_v3-call ...                             # :1333  (pass enable_script_materialization here)
self._final_dep_graph = getattr(final_map, "dep_graph", None)           # :1367  <-- finalizer's compile_replay_blocks input
def _finalize_supervisor_artifacts(self, configuration_success):        # :1638 (calls _synthesize_final_build_recipe, THEN the retained captures)
def _synthesize_final_build_recipe(self, drop_replayed_state=False):     # :2225 ; ledger block :2235-2263 ; source set :2262
#   retained captures run AFTER _synthesize_final_build_recipe returns, in _finalize_supervisor_artifacts (NOT inside it):
#     _emit_interleaved_state_recipe() (file capture), _emit_closure_recipe() (pin), _bake_test_env_vars() (config ENV)
#   => an early `return True` from _synthesize_final_build_recipe does NOT drop the retained captures.
```

executor callables: `sandbox_execute: Callable[[str], tuple[bool, str]]` (mutating), `exec_readonly: Callable[[str], tuple[int, str]]` (read-only).

### Review resolutions baked into this plan (2026-06-28)

Three sonnet reviews (code-grounding / spec-alignment / plan-quality) found and this revision fixes:
- **`compose_script` on a certified graph yields an empty spine** (compile_blocks emits only `State.MISSING`). → **Task 4 adds `compile_replay_blocks`** (state-independent); Task 5/Task 7 use it for the artifact.
- **`ActionEvent` needs a required `step`** → block_emit supplies `step=len(ledger.events())` (Task 2).
- **`DockerAgent(...)` is too heavy to construct in tests** → Task 1 uses source-inspection (the established `test_graph_scheduler_flag.py` pattern).
- **Task 3's `build_run_v3_inputs` was a `<placeholder>`** → now a concrete local harness modeled on `test_graph_scheduler_wiring.py`; OFF test also asserts `repair_failed_nodes` ran.
- **Task 5 fakes were unspecified** → fully specified `_SpySynth` + a real empty `ActionLedger` for the OFF path.
- Line-drift corrected in the integration-points header above.

---

### Task 1: `enable_script_materialization` flag + cascade + run_v3 param (plumbed, unused)

**Files:**
- Modify: `agent.py` (constructor param + cascade after `:353`; pass-through at the run_v3 call `:1333`)
- Modify: `src/envstate/orchestrator.py` (`run_v3` signature `:317`)
- Test: `tests/test_script_materialization_flag.py`

**Interfaces:**
- Produces: `self.enable_script_materialization: bool` on the agent (cascade: defaults to `self.enable_graph_scheduler`, overridable), and a `run_v3(..., enable_script_materialization: bool = True)` keyword parameter. Plumbed but NOT yet consumed (no behavior change — Task 3 consumes it).

- [ ] **Step 1: Write the failing test** (source-inspection — `DockerAgent.__init__` is too heavyweight to construct, mirroring `tests/test_graph_scheduler_flag.py`)

```python
# tests/test_script_materialization_flag.py
"""Slice A: enable_script_materialization plumbing. DockerAgent.__init__ builds an
OpenAI client + Docker sandbox, so (exactly like tests/test_graph_scheduler_flag.py)
we verify the cascade via source text + the run_v3 param via signature inspection."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_constructor_accepts_param_default_none():
    src = (_ROOT / "agent.py").read_text()
    assert "enable_script_materialization=None" in src, (
        "DockerAgent.__init__ must accept enable_script_materialization (default None = inherit graph-scheduler)"
    )


def test_cascade_defaults_to_graph_scheduler_when_none():
    """When the param is None, the flag inherits enable_graph_scheduler (B5 ON with v3);
    when explicitly set, bool() of the value wins (B3 ablation can force it OFF)."""
    src = (_ROOT / "agent.py").read_text()
    assert (
        "self.enable_script_materialization = (" in src
        and "self.enable_graph_scheduler if enable_script_materialization is None" in src
        and "else bool(enable_script_materialization)" in src
    ), "cascade must inherit enable_graph_scheduler when None, else bool(value)"
    # the cascade must come AFTER enable_graph_scheduler is assigned
    assert src.index("self.enable_graph_scheduler: bool = bool(enable_graph_scheduler)") < \
        src.index("self.enable_script_materialization = ("), \
        "enable_graph_scheduler must be assigned before the script-materialization cascade reads it"


def test_run_v3_call_passes_the_flag():
    src = (_ROOT / "agent.py").read_text()
    assert "enable_script_materialization=self.enable_script_materialization" in src, (
        "the run_v3 invocation must forward the flag"
    )


def test_run_v3_accepts_the_param():
    from src.envstate import orchestrator
    sig = inspect.signature(orchestrator.run_v3)
    assert "enable_script_materialization" in sig.parameters
    assert sig.parameters["enable_script_materialization"].default is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_script_materialization_flag.py -q`
Expected: FAIL (the cascade text and the run_v3 param do not exist yet).

- [ ] **Step 3: Implement**

In `agent.py` constructor: add a keyword param `enable_script_materialization=None` (place it next to `enable_graph_scheduler` in the signature). In the cascade block, AFTER `self.enable_envstate = (...)` (around `:353`, so `enable_graph_scheduler` at `:333` is already set), add:

```python
        # Script-materialization (Slice A): default ON whenever the graph scheduler is on
        # (B5 = compiled setup.sh drives execution + artifact). Independently settable OFF
        # for the §14 B3 ablation (revert to emit_drain + ledger-replay).
        self.enable_script_materialization = (
            self.enable_graph_scheduler if enable_script_materialization is None
            else bool(enable_script_materialization)
        )
```

At the `run_v3` call (`:1333`), add the kwarg `enable_script_materialization=self.enable_script_materialization`.

In `src/envstate/orchestrator.py`, add `enable_script_materialization: bool = True` to `run_v3`'s keyword-only block (after `graph_scheduler_attempt_cap`, `:331`). Do NOT consume it yet.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_script_materialization_flag.py -q`
Expected: PASS (4 tests).

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
- Produces: `block_emit(graph, sandbox_execute, exec_readonly, ledger, cycle, *, manual_blocks=()) -> tuple[DepGraph, EvidenceBundle, str | None]` — compiles the graph's emittable wave to blocks, runs them via `run_blocks` under a sandbox wrapper that mirrors EVERY block command (success + failure) into `ledger` as an `ActionEvent`, and returns `(certified_graph, evidence, failed_block_id)`. Pure of LLM/Docker imports (the executors are injected). Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/envstate/test_block_emit.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

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
    assert len(bundle.items) >= 1                                     # typed evidence emitted
    # dual-write: the install command is mirrored into the ledger with rc 0
    assert any("libpq-dev" in e.cmd and e.rc == 0 for e in led.events())


def test_failed_block_is_recorded_in_ledger_with_rc_nonzero():
    led = ActionLedger()
    def sandbox(cmd): return (False, "E: package not found")
    def ro(cmd): return (1, "")
    graph, bundle, failed = block_emit(_graph(), sandbox, ro, led, cycle=1)
    assert failed == "system.libpq.so"
    assert any(e.rc != 0 for e in led.events())                      # failures feed runtime_ingest


def test_check_fails_so_node_not_certified():
    led = ActionLedger()
    def sandbox(cmd): return (True, "ok")          # install "succeeds" ...
    def ro(cmd): return (1, "absent")              # ... but the host check fails
    graph, _b, _f = block_emit(_graph(), sandbox, ro, led, cycle=1)
    assert graph.get("syslib:libpq.so").state is not State.SATISFIED   # block rc=0 never certifies
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/envstate/test_block_emit.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.block_emit'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/envstate/block_emit.py
"""Deterministic block-emit phase for v3 (design §5.1): compile the graph's emittable
wave to blocks, run them, certify via host checks, and dual-write a minimal ActionLedger
(the state-capture feed) alongside the typed EvidenceBundle. NO LLM. The v3 analog of
emit_drain, but graph-compiled and free of build_agent."""
from __future__ import annotations

from typing import Callable

from python_deps.depgraph.patch_gate import compose_script
from src.envstate.script_runner import run_blocks
from src.envstate.ledger import ActionEvent, ActionLedger


def block_emit(
    graph,
    sandbox_execute: Callable[[str], tuple[bool, str]],
    exec_readonly: Callable[[str], tuple[int, str]],
    ledger: ActionLedger,
    cycle: int,
    *,
    manual_blocks: tuple = (),
):
    """Run the graph-compiled blocks; mirror each command into ``ledger``; certify via
    run_blocks' host checks. Returns (certified_graph, EvidenceBundle, failed_block_id).

    The dual-write records ACTIONS only — node state is written exclusively by
    certify_refresh inside run_blocks (invariants #3/#4). Both successful and failed
    commands are mirrored; failures (rc != 0) feed _runtime_ingest_phase."""
    blocks = compose_script(graph, manual_blocks)

    def _mirroring_sandbox(cmd: str) -> tuple[bool, str]:
        ok, out = sandbox_execute(cmd)
        ledger.append(ActionEvent(
            step=len(ledger.events()),          # monotonic step (ActionEvent.step is required)
            cmd=cmd,
            rc=0 if ok else 1,
            stdout=out or "",
            mutation_class="file_or_env_change",
        ))
        return ok, out

    return run_blocks(blocks, _mirroring_sandbox, exec_readonly, graph, cycle)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/envstate/test_block_emit.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/block_emit.py tests/envstate/test_block_emit.py
git commit -m "feat(v3): block_emit — deterministic block run + ledger dual-write (no LLM)"
```

---

### Task 3: wire `block_emit` into `run_v3._dep_emit_phase` behind the toggle

**Files:**
- Modify: `src/envstate/orchestrator.py` (`run_v3._dep_emit_phase`, replace the `:397-415` span with a toggle branch)
- Test: `tests/test_v3_block_emit_wiring.py`

**Interfaces:**
- Consumes: `block_emit` (Task 2), the `enable_script_materialization` run_v3 param (Task 1).
- Produces: under the toggle, v3's `_dep_emit_phase` certifies the emittable wave via `block_emit` (not `emit_drain`/`repair_failed_nodes`); `ensure_python_shim` + `certify_refresh` (before) and the satisfied-PACKAGE fold + `merge_map` (after, `:416-429`) are unchanged. Toggle off → existing `emit_drain` + `repair_failed_nodes` path verbatim.

- [ ] **Step 1: Write the failing test** (a concrete in-process harness modeled on `tests/test_graph_scheduler_wiring.py` — no Docker, no LLM)

```python
# tests/test_v3_block_emit_wiring.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.envstate.block_emit as be
import src.envstate.depgraph_live as dl
from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import TaskReport, initial_map, merge_map
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


# --- minimal in-process fakes (copied from test_graph_scheduler_wiring.py) ---
class _RecordingBuildAgent:
    def __init__(self): self.tasks = []
    def run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget=None):
        self.tasks.append(task)
        return TaskReport(task_goal="t", status="blocked", commands=(), learning="b")
    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        return TaskReport(task_goal="r", status="done", commands=(), learning="ok")


class _NoopMaintainer:
    def update(self, world_map, report): return world_map


def _syslib_map():
    """A WorldModelMap with one MISSING SystemLib whose apt fix + ldconfig check let
    block_emit (and emit_drain) install + certify it deterministically."""
    node = Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB, name="libpq.so",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev")
    base = initial_map(base_image="python:3.11-slim", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, dep_graph=DepGraph().with_node(node))


def build_run_v3_inputs():
    """Fresh run_v3 kwargs each call. The check is STATEFUL: ldconfig fails UNTIL the apt
    install runs, then passes. This matters because `certify_refresh` runs BEFORE the emit
    phase (orchestrator.py:392) — an unconditional pass there would pre-satisfy the node,
    leaving an empty emittable wave (block_emit compiles 0 blocks, no install, no dual-write).
    With the stateful check the node stays MISSING at certify, block_emit installs it, and the
    post-install check certifies it."""
    state = {"installed": False}
    led = ActionLedger()
    def sandbox(cmd):
        if "libpq-dev" in cmd:
            state["installed"] = True
        return (True, "installed")
    def ro(cmd):
        if "ldconfig" in cmd:
            return (0, "libpq") if state["installed"] else (1, "")
        return (1, "")
    return dict(
        build_agent=_RecordingBuildAgent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_syslib_map(),
        ledger=led,
        sandbox_execute=sandbox,
        max_cycles=1,
        exec_readonly=ro,
        enable_dep_emit=True,
    )


def _spy(mod, name, calls, key):
    real = getattr(mod, name)
    def wrapper(*a, **k):
        calls.append(key)
        return real(*a, **k)        # passthrough so the real phase still runs
    return wrapper


def test_toggle_on_uses_block_emit(monkeypatch):
    calls = []
    monkeypatch.setattr(be, "block_emit", _spy(be, "block_emit", calls, "block"))
    monkeypatch.setattr(dl, "emit_drain", _spy(dl, "emit_drain", calls, "drain"))
    inputs = build_run_v3_inputs()
    final_map, _ = orchestrator.run_v3(**inputs, enable_script_materialization=True)
    assert "block" in calls and "drain" not in calls
    assert final_map.dep_graph.get("syslib:libpq.so").state is State.SATISFIED
    assert any("libpq-dev" in e.cmd for e in inputs["ledger"].events())   # dual-write happened


def test_toggle_off_uses_emit_drain_and_repair(monkeypatch):
    calls = []
    monkeypatch.setattr(be, "block_emit", _spy(be, "block_emit", calls, "block"))
    monkeypatch.setattr(dl, "emit_drain", _spy(dl, "emit_drain", calls, "drain"))
    monkeypatch.setattr(dl, "repair_failed_nodes", _spy(dl, "repair_failed_nodes", calls, "repair"))
    inputs = build_run_v3_inputs()
    orchestrator.run_v3(**inputs, enable_script_materialization=False)
    assert "drain" in calls and "repair" in calls and "block" not in calls
```

> **Implementer note:** both engines are imported LAZILY inside `_dep_emit_phase` (`emit_drain` via the `from src.envstate.depgraph_live import ...` at `:388`; `repair_failed_nodes` via the import at `:405`; `block_emit` via the new branch import). Python re-executes `from X import Y` on every call, so patching the module attribute (`dl.emit_drain`, `dl.repair_failed_nodes`, `be.block_emit`) is picked up at call time — this is exactly how `tests/test_graph_scheduler_wiring.py::test_drain_runs_under_flag_as_prefix` works.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_v3_block_emit_wiring.py -q`
Expected: FAIL (`test_toggle_on_uses_block_emit`: toggle-on still calls `emit_drain`; `block_emit` never invoked).

- [ ] **Step 3: Implement**

In `src/envstate/orchestrator.py`, inside `run_v3._dep_emit_phase`, KEEP `ensure_python_shim(sandbox_execute)` (`:391`) and `graph = certify_refresh(...)` (`:392`) as-is. Then REPLACE the span `:397-415` (the `emit_drain(...)` call through the `_budget_exhausted` accounting) with a toggle branch. Leave `:388`'s lazy import line (which includes `emit_drain`) intact:

```python
        if enable_script_materialization:
            # Slice A: deterministic block run replaces emit_drain on v3 (design §5.1).
            # No LLM and no host-repair here — a failed block ends the wave (Slice B adds
            # the repair loop). compose_script handles the emittable wave; certify writes state.
            # NOTE: global_step is intentionally not advanced here (block_emit owns no LLM
            # turns); the repair loop's step accounting is wired in Slice B.
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
            # Host-first repair of reciped nodes the batch wave could not certify.
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

Leave the satisfied-PACKAGE fold + `merge_map` (`:416-429`) unchanged (it runs after, for both branches). `enable_script_materialization` is a `run_v3` parameter so it is already in `_dep_emit_phase`'s closure scope — no `nonlocal` needed (it is read-only here).

**Also update the one existing test that pins the OLD v3 default.** Because the toggle defaults **on** (B5), `run_v3` no longer runs `emit_drain` by default — `tests/test_graph_scheduler_wiring.py::test_drain_runs_under_flag_as_prefix` asserts the pre-2b default (emit_drain runs in v3 with no flag) and will now fail. This is the expected, intended consequence of the default flip, NOT a weakening: the test's real subject ("emit_drain runs as the deterministic prefix") is now the B3/off path. Update its `run_v3(...)` call to pass `enable_script_materialization=False` (the run_v1 portion is unaffected — v1 has no such flag). Do not change the test's assertions; only add the flag so it exercises the emit_drain path it was written to check. (If the full-suite gate in Task 8 surfaces other tests that assert the old v3 default, those get the same one-line `enable_script_materialization=False` update — but only that test is known to break here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_v3_block_emit_wiring.py -q`
Expected: PASS (2 tests). Then `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_graph_scheduler_wiring.py -q` — green: v1 unchanged, the toggle-off v3 path unchanged, and the updated `test_drain_runs_under_flag_as_prefix` exercises emit_drain via `enable_script_materialization=False`.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_v3_block_emit_wiring.py tests/test_graph_scheduler_wiring.py
git commit -m "feat(v3): _dep_emit_phase drives block_emit under enable_script_materialization"
```

---

### Task 4: `compile_replay_blocks` — state-independent artifact compile (new function)

**Files:**
- Modify: `src/python_deps/depgraph/block.py` (extract a shared `_block_for(node)` helper; add `compile_replay_blocks`)
- Test: `tests/depgraph/test_compile_replay_blocks.py`

**Interfaces:**
- Consumes: `_is_reciped` (`emit.py`), `topo_order`/`_apt_name` (`emit.py`), `_command_for`/`_block_id_for` (`block.py`).
- Produces: `compile_replay_blocks(graph) -> tuple[Block, ...]` — one block per `_is_reciped` installable node (PACKAGE with a version, or SYSTEM_LIB/TOOL with an `apt:` fix), in dependency (topo) order, **regardless of node state**. This is the artifact/replay projection that reproduces the certified environment on a fresh container, where `compile_blocks` (emit-phase, MISSING-only) would yield nothing once certified. Consumed by Task 5 and Task 7.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_compile_replay_blocks.py
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.block import compile_blocks, compile_replay_blocks
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, EdgeType, Layer, Node, NodeType, State,
)


def _satisfied_syslib():
    return DepGraph().with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB,
        name="libpq.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
        state=State.SATISFIED, check_command="ldconfig -p | grep -q libpq",
        chosen_fix="apt:libpq-dev"))


def test_replay_includes_satisfied_node_emit_excludes_it():
    g = _satisfied_syslib()
    # the emit-phase compiler emits nothing (node is already certified) ...
    assert compile_blocks(g) == ()
    # ... but the replay compiler reproduces the install spine regardless of state.
    blocks = compile_replay_blocks(g)
    assert [b.block_id for b in blocks] == ["system.libpq.so"]
    assert blocks[0].commands == ("apt-get install -y --no-install-recommends libpq-dev",)
    assert blocks[0].check_commands == ("ldconfig -p | grep -q libpq",)


def test_replay_orders_system_before_pip_dep():
    g = DepGraph()
    g = g.with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB, name="libpq.so",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED,
        check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev"))
    g = g.with_node(Node(id="pkg:psycopg2==2.9.9", type=NodeType.PACKAGE, name="psycopg2",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED,
        version="2.9.9", check_command="python3 -m pip show psycopg2"))
    g = g.with_edge(src="pkg:psycopg2==2.9.9", dst="syslib:libpq.so", relation=EdgeType.REQUIRES)
    ids = [b.block_id for b in compile_replay_blocks(g)]
    assert ids.index("system.libpq.so") < ids.index("pip.psycopg2==2.9.9")


def test_replay_skips_nodes_without_install_command():
    # a TEST-goal node is not _is_reciped -> no block
    g = DepGraph().with_node(Node(id="test:repo_tests_pass", type=NodeType.TEST, name="tests",
        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.SATISFIED))
    assert compile_replay_blocks(g) == ()
```

> **Implementer note:** confirm the exact `with_edge` keyword API against `schema.py` (`DepGraph.with_edge`) and `EdgeType.REQUIRES`; if `with_edge` takes a positional/`Edge` object, adapt the edge construction in the second test to match (mirror how `tests/depgraph/test_compose_script.py` or `test_build.py` build edges).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_compile_replay_blocks.py -q`
Expected: FAIL with `ImportError: cannot import name 'compile_replay_blocks'`.

- [ ] **Step 3: Implement**

In `src/python_deps/depgraph/block.py`: add `_is_reciped` to the existing emit import, extract the block-construction body into a shared `_block_for(node)` helper, and add `compile_replay_blocks`. The existing `compile_blocks` must keep identical behavior (use the helper):

```python
from python_deps.depgraph.emit import partition, topo_order, _apt_name, _pip_spec, _is_reciped
```

```python
def _block_for(node: Node) -> Block | None:
    """Build the one-action block for an installable node, or None if it has no command."""
    cmd = _command_for(node)
    if not cmd:
        return None
    apt = _apt_name(node)
    return Block(
        block_id=_block_id_for(node),
        wave=node.layer.value,
        commands=(cmd,),
        target_node_ids=(node.id,),
        provider_ids=(node.chosen_fix,) if apt is not None else (),
        check_commands=(node.check_command,) if node.check_command else (),
    )


def compile_blocks(graph: DepGraph) -> tuple[Block, ...]:
    """Emit-phase compile: ONLY the emittable wave (partition().emittable = MISSING nodes
    whose deps are satisfied). Used by the live block-emit phase."""
    if graph is None:
        return ()
    ready = topo_order(graph, partition(graph).emittable)
    return tuple(b for n in ready if (b := _block_for(n)) is not None)


def compile_replay_blocks(graph: DepGraph) -> tuple[Block, ...]:
    """Artifact/replay compile: one block per installable (_is_reciped) node, in topo
    order, REGARDLESS of state. Reproduces the certified environment on a fresh
    container — so SATISFIED nodes ARE included (unlike compile_blocks). Pure."""
    if graph is None:
        return ()
    installable = tuple(n for n in graph.nodes if _is_reciped(n))
    ready = topo_order(graph, installable)
    return tuple(b for n in ready if (b := _block_for(n)) is not None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_compile_replay_blocks.py tests/depgraph/test_compose_script.py -q`
Expected: PASS (the new tests + the existing compose_script/compile_blocks tests still green after the `_block_for` extraction). Also run any existing block test: `python3 -m pytest tests/depgraph -q -k "block or compose or build"`.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/block.py tests/depgraph/test_compile_replay_blocks.py
git commit -m "feat(depgraph): compile_replay_blocks — state-independent artifact spine"
```

---

### Task 5: finalizer — graph-compiled Dockerfile spine (artifact switch)

**Files:**
- Modify: `agent.py` (`_synthesize_final_build_recipe`, insert a v3 branch before the ledger block at `:2235`; add a `_persist_setup_sh` helper)
- Test: `tests/test_v3_artifact_source.py`

**Interfaces:**
- Consumes: `self.enable_script_materialization` (Task 1), `self._final_dep_graph` (`agent.py:1367`), `compile_replay_blocks` (Task 4), `render_setup_sh`.
- Produces: when the toggle is on and `self._final_dep_graph` is present, the build recipe's `build_commands` are the **replay-compiled** block command lines (source `"compiled_setup_sh"`), and the rendered `setup.sh` is persisted as the audit/replay artifact. The retained captures — `_emit_interleaved_state_recipe` (file), `_emit_closure_recipe` (pinned closure), `_bake_test_env_vars` (config ENV) — run AFTER, unchanged, in `_finalize_supervisor_artifacts` (they are not inside `_synthesize_final_build_recipe`, so the early `return True` preserves them). Toggle off → existing `"action_ledger"` path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v3_artifact_source.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent import DockerAgent
from src.envstate.ledger import ActionLedger
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy


class _SpySynth:
    """Records the recipe dict; exposes the attr the toggle-OFF ledger path reads."""
    def __init__(self):
        self.applied = None
    def apply_build_recipe(self, recipe):
        self.applied = recipe
    def _extract_recordable_setup_commands(self, cmd):   # used as `distill` by build_commands_from_ledger
        return cmd


def _graph(state):
    # SATISFIED on purpose: proves the artifact compiles the *certified* graph
    # (compile_blocks would yield nothing here; compile_replay_blocks must not).
    return DepGraph().with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB,
        name="libpq.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
        state=state, check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev"))


def _agent(materialize, graph):
    a = object.__new__(DockerAgent)               # __new__ bypass (established suite pattern)
    a.enable_script_materialization = materialize
    a._final_dep_graph = graph
    a.enable_envstate = True
    a.action_ledger = ActionLedger()              # real, empty (toggle-off path applies w/ drop_replayed_state)
    a.synthesizer = _SpySynth()
    a.setup_log_dir = None                        # _persist_setup_sh no-ops without a dir
    return a


def test_v3_artifact_source_is_compiled_setup_sh():
    a = _agent(True, _graph(State.SATISFIED))
    assert a._synthesize_final_build_recipe(drop_replayed_state=True) is True
    assert a.build_recipe_source == "compiled_setup_sh"
    assert any("libpq-dev" in c for c in a.synthesizer.applied["build_commands"])


def test_toggle_off_keeps_action_ledger_source():
    a = _agent(False, _graph(State.SATISFIED))
    assert a._synthesize_final_build_recipe(drop_replayed_state=True) is True
    assert a.build_recipe_source == "action_ledger"
```

> **Implementer note:** with `drop_replayed_state=True` the existing ledger block applies even on an empty ledger (`if ledger_commands or drop_replayed_state:`), so the toggle-OFF test needs no ledger contents — only that the new v3 branch is skipped. The `object.__new__(DockerAgent)` bypass avoids the heavy `__init__`. The compiled branch must NOT reference `self.client`/`self.model`/`self.action_ledger` (it returns before them).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_v3_artifact_source.py -q`
Expected: FAIL (`test_v3_artifact_source_is_compiled_setup_sh`: `build_recipe_source` is `"action_ledger"`, not `"compiled_setup_sh"`).

- [ ] **Step 3: Implement**

In `agent.py` `_synthesize_final_build_recipe` (`:2225`), insert the v3 branch BEFORE the existing `if getattr(self, "enable_envstate", False) and self.action_ledger is not None:` ledger block (`:2235`):

```python
        if (getattr(self, "enable_script_materialization", False)
                and getattr(self, "_final_dep_graph", None) is not None):
            # v3 (design §5.2): the compiled setup.sh is the install spine — graph-sourced,
            # NOT ledger replay (invariant #1 / §18 #2). compile_replay_blocks (state-independent)
            # reproduces the certified closure; compile_blocks would be empty here (all SATISFIED).
            # The pinned closure + config ENV + file captures are appended AFTER, by
            # _finalize_supervisor_artifacts (_emit_closure_recipe / _bake_test_env_vars /
            # _emit_interleaved_state_recipe) — so this early return does not drop them.
            from python_deps.depgraph.block import compile_replay_blocks
            from python_deps.depgraph.script import render_setup_sh
            blocks = compile_replay_blocks(self._final_dep_graph)
            build_commands = [c for b in blocks for c in b.commands]
            self._persist_setup_sh(render_setup_sh(blocks))      # audit/replay artifact
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

Add a small helper method on `DockerAgent` (near `_synthesize_final_build_recipe`):

```python
    def _persist_setup_sh(self, text):
        """Write the rendered setup.sh into the run's log dir as the audit/replay artifact.
        No-op when no log dir is set (e.g. unit tests)."""
        import os
        d = getattr(self, "setup_log_dir", None) or getattr(self, "logs_dir", None)
        if not d:
            return
        try:
            with open(os.path.join(d, "setup.sh"), "w") as fh:
                fh.write(text)
        except OSError:
            pass
```

Keep the existing ledger block and LLM fallback untouched (they serve v1 + toggle-off).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_v3_artifact_source.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_v3_artifact_source.py
git commit -m "feat(v3): finalizer sources Dockerfile spine from compiled replay blocks (retain pin/config/file)"
```

---

### Task 6: delete the dead `apply_recipe_patch` branch in v3

**Files:**
- Modify: `src/envstate/orchestrator.py` (delete the comment `:602` + the `if decision.action == "apply_recipe_patch":` block `:603-639`)
- Test: `tests/test_v3_no_recipe_patch_branch.py`

**Interfaces:**
- The v3 scheduler (`graph_scheduler.next_decision`) only returns `action in {"task","done"}`, so the `apply_recipe_patch` branch is unreachable. Deleting it removes v3's last `RecipePatch` dependency in this code path. v1's recipe branch and the `RecipePatch` import stay.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v3_no_recipe_patch_branch.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import inspect
import src.envstate.orchestrator as orch


def test_run_v3_has_no_apply_recipe_patch_branch():
    src = inspect.getsource(orch.run_v3)
    assert "apply_recipe_patch" not in src, "dead v3 apply_recipe_patch branch must be removed"


def test_run_v1_still_has_recipe_branch():
    # guard: the deletion must not touch v1's recipe handling
    src = inspect.getsource(orch.run_v1)
    assert "RecipePatch" in src or "recipe" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_v3_no_recipe_patch_branch.py -q`
Expected: FAIL (`apply_recipe_patch` still present in `run_v3` source).

- [ ] **Step 3: Implement**

In `run_v3` (`orchestrator.py`), delete the `if decision.action == "apply_recipe_patch":` branch and its body (the block is `:603-639`, with its leading comment at `:602` — delete that comment too). Then check whether `RecipePatch` is still referenced anywhere in `orchestrator.py`:

```bash
grep -n "RecipePatch" src/envstate/orchestrator.py
```

v1's branch still uses it, so KEEP the `RecipePatch` import (`:30`). Do NOT touch v1's recipe branch.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_v3_no_recipe_patch_branch.py -q`
Expected: PASS (2 tests). Then `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_graph_scheduler_wiring.py -q` — green (v1 + v3 loop unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_v3_no_recipe_patch_branch.py
git commit -m "refactor(v3): delete dead apply_recipe_patch branch (scheduler emits only task/done)"
```

---

### Task 7: L1 seam integration test (CI-safe) + real-container driver (manual gate)

**Files:**
- Create: `tests/envstate/test_sliceA_seam_integration.py` (FakeExecutor, CI-safe)
- Create: `scripts/l1_engine_swap_smoke.py` (real-container driver — manual validation)

**Interfaces:**
- The seam test exercises the full Slice-A deterministic chain in-process — emit-phase (MISSING → block_emit → certify) AND the artifact projection (certified graph → `compile_replay_blocks` → `render_setup_sh`). The driver runs the same chain against a real container on one repo (the human-run validation gate before relying on A).

- [ ] **Step 1: Write the seam integration test**

```python
# tests/envstate/test_sliceA_seam_integration.py
"""Slice A deterministic chain end-to-end with a FakeExecutor (no Docker, no LLM):
emit phase   : graph(MISSING) -> block_emit (compose_script -> run_blocks -> certify + ledger dual-write)
artifact spine: certified graph -> compile_replay_blocks -> render_setup_sh."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.block import compile_replay_blocks
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from python_deps.depgraph.script import render_setup_sh
from src.envstate.block_emit import block_emit
from src.envstate.ledger import ActionLedger


def _graph():
    return DepGraph().with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB,
        name="libpq.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING, check_command="ldconfig -p | grep -q libpq",
        chosen_fix="apt:libpq-dev"))


def test_block_emit_then_replay_artifact_spine():
    led = ActionLedger()
    graph, bundle, failed = block_emit(_graph(), lambda c: (True, "ok"),
                                       lambda c: (0, "") if "ldconfig" in c else (1, ""),
                                       led, cycle=1)
    assert failed is None and graph.get("syslib:libpq.so").state is State.SATISFIED
    assert any("libpq-dev" in e.cmd for e in led.events())          # dual-write happened

    # the artifact the finalizer emits for the CERTIFIED graph (replay, state-independent):
    spine = render_setup_sh(compile_replay_blocks(graph))
    assert "apt-get install -y --no-install-recommends libpq-dev" in spine
    assert "#@check ldconfig -p | grep -q libpq" in spine
    assert "SATISFIED" not in spine                                  # script carries no state
```

- [ ] **Step 2: Run it (expect PASS — characterization over Tasks 2 & 4)**

Run: `python3 -m pytest tests/envstate/test_sliceA_seam_integration.py -q`
Expected: PASS (1 test).

- [ ] **Step 3: Write the real-container driver (manual gate)**

```python
# scripts/l1_engine_swap_smoke.py
"""L1 validation (MANUAL — not in CI; Docker is slow/flaky in CI): run the Slice-A
deterministic chain against a REAL container on one repo and print the certified
states + the rendered replay spine.

    python3 scripts/l1_engine_swap_smoke.py <repo_path> <base_image>

Wiring: build the dep-graph via the existing build path, bind run_blocks' executor
callables to the real `docker exec` adapter the orchestrator already constructs
(sandbox_execute: (cmd)->(ok,out), exec_readonly: (cmd)->(rc,out)), run block_emit,
then render compile_replay_blocks(certified_graph). Reuse — do NOT reimplement — the
container adapter and the graph builder.
"""
# Implementer: this is a thin driver, not a unit test. Import the existing container
# exec adapter + dep-graph builder used by the orchestrator/build path, run block_emit,
# print each node's certified state and render_setup_sh(compile_replay_blocks(graph)).
# If the exact exec-adapter / build_dep_graph entry points are unclear, report it and
# STOP (do not guess a Docker API). Excluded from pytest collection by living in scripts/.
```

> **Implementer note:** do NOT add the driver to the pytest suite (no Docker in CI). It is the human-run L1 gate before Slice A is relied on. If the exec-adapter or graph-builder entry point is unclear, report it as a NEEDS_CONTEXT rather than guessing.

- [ ] **Step 4: Commit**

```bash
git add tests/envstate/test_sliceA_seam_integration.py scripts/l1_engine_swap_smoke.py
git commit -m "test(v3): Slice-A seam integration (CI-safe) + L1 real-container smoke driver"
```

---

### Task 8: full-suite regression + toggle-off-unchanged gate

**Files:**
- Test: (this task runs the gates; no new files)

- [ ] **Step 1: Full-suite regression**

Run: `python3 -m pytest tests -q -p no:cacheprovider`
Expected: only the 4 known pre-existing failures remain (`test_adapter_logic` nested_pytester, `test_repo2run_dataset` ×2, `test_runtime_pin_seam` floor-trap), 0 new. Any NEW failure → investigate before proceeding.

- [ ] **Step 2: Prove v1 + toggle-off v3 unchanged**

Run: `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_graph_scheduler_wiring.py tests/test_contract_graph_v2_integration.py -q`
Expected: green — v1 and the toggle-off (B3) path are behaviorally unchanged (no edits to `emit_drain`/`synthesis`/`BuildAgent`).

- [ ] **Step 3: Record the result**

Append the pass/fail tally + the exact failing-test names to the run report. If only the 4 known failures remain, Slice A is regression-clean.

---

## Slice A done-definition

- `run_v3` with `enable_script_materialization=True` provisions the emittable wave via `block_emit` (compose_script → run_blocks → certify), dual-writes the ledger, and emits a graph-compiled (`source="compiled_setup_sh"`) Dockerfile spine via `compile_replay_blocks` with the pin/config/file captures retained.
- Toggle off reverts to `emit_drain` + `repair_failed_nodes` + ledger-replay (B3); v1 byte-identical.
- The dead `apply_recipe_patch` branch is gone.
- Seam integration test green; L1 real-container driver available for the manual gate.
- Full suite green except the 4 known pre-existing failures.

## After Slice A (separate plans — do NOT start here)

- **L1 manual gate:** run `scripts/l1_engine_swap_smoke.py` on 1–2 real repos; confirm the replay `setup.sh` provisions + certifies before relying on A. Then **re-baseline v3** (B5 vs B3 arm).
- **Slice B** — structured repair loop: RepairScope builder; BuildAgent v3 mode (ReAct read-only diagnose → fenced `PatchProposal` JSON); the bounded repair loop calling `validate_proposal → apply_proposal → compose_script → re-run`; thread accumulated `manual_blocks` into both `block_emit` and the finalizer's replay compile; wire `global_step` accounting for the repair loop; carry the 2a-review hardening MUSTs (read-only allowlist, provider/promotion upgrade semantics, non-empty ScriptPatch targets, `provides` validation, widened ACTION_CLASSES, parse robustness).
- **Slice C** — config/service LLM classifier → soft-hint proposals; generalize `schedule._is_actionable`; §5.2-bundle fidelity fixes.
