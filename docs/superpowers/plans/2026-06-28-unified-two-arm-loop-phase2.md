# Unified Two-Arm Loop — Phase 2 (Split + Simplify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Finish the two-arm unification: make v3's dep-graph the sole world-model, unify v3 termination, split `run_v1` into `run_v1()`+`run_v3()`, collapse `run_repo2run_benchmark.py`'s arms, and restore deferred coverage. Behavior-CHANGING for v3 (re-baseline after); v1 unchanged.

**Architecture:** Reduce v3's `DeterministicMaintainer` to a done-gate (drop the vestigial `contract_graph` write); introduce an internal `TerminationReason` mapped to existing stop-reason strings; split the loop into two focused functions sharing a small helper module; mirror Phase 1's arm collapse in the repo2run harness.

**Tech Stack:** Python 3.10, pytest. Files: `src/envstate/{deterministic_maintainer,orchestrator}.py`, new `src/envstate/_loop_common.py`, `agent.py`, `run_repo2run_benchmark.py`, `tests/`.

**Reference:** Spec `docs/superpowers/specs/2026-06-28-unified-two-arm-loop-phase2-design.md`. Detailed research maps: `.superpowers/sdd/phase2-research-{A,B,C,D}-*.md` (read the relevant one per task).

## Global Constraints

- **v1 behavior is preserved exactly.** Only the v3 (graph-scheduler) arm changes behavior. Every v1 test must stay green unchanged.
- **External contract preserved.** `run_v3` must return the SAME `stop_reason` strings the pre-split loop returned for the v3 arm (`planner_done`/`done_flag`/`planner_giveup`/`max_cycles`); `agent.py`'s consumption is unchanged.
- **Anti-hollow holds.** The v3 done-gate still requires `_verified_test_run_passed(report)` (a real rc-0 verified test pass); never an LLM/action-implied finalize.
- **`contracts/` package stays** (v1 maintainers use it). v3 simply stops writing `contract_graph`.
- **No large verbatim duplication** when splitting — extract genuinely-shared helpers; each arm owns its loop body. A reviewer will flag a duplicated logic block.
- **Do NOT touch** v0/legacy code (`enable_supervisor`/`enable_fullstate_worker`/`enable_envstate`), `arm0` removal, the dep-graph engine, scheduler logic, or certification semantics.
- The full suite is the regression gate. 5 known pre-existing failures (verified in Phase 1: adapter format assertion; repo2run_dataset ×2 missing-PDF; runtime_pin floor-pin; depgraph_live_certify ordering-flake) are out of scope — do not "fix" them.
- Commits: conventional, **NO `Co-Authored-By`**. `git add` only the specific files per task — never `git add -A`/a directory (repo has unrelated WIP). Line anchors are from base `c6e88c8` and will drift — re-locate by `grep -n`.

---

## Task 1 (C5): Collapse `run_repo2run_benchmark.py` arms to {0, v1, v3} + fix service-provision forwarding

**Files:**
- Modify: `run_repo2run_benchmark.py` (`_ARM_PRESETS` ~:3177-3246; `--arm` choices ~:3406; `build_agent_command` ~:200-222)
- Test: `tests/test_arm_v1g.py`, `tests/test_graph_scheduler_flag.py`, `tests/test_runtime_pin_flag.py`

**Interfaces:** Produces `_ARM_PRESETS` with keys `{"0","v1","v3"}`; `--arm choices=["0","v1","v3"]`; `build_agent_command` forwarding `enable_service_provision`.

- [ ] **Step 1:** Read `.superpowers/sdd/phase2-research-D-repo2run.md` for exact line refs and the preset/test details.

- [ ] **Step 2 (RED):** In `tests/test_graph_scheduler_flag.py:119` change the repo2run assertion `'"v1gs"'`→`'"v3"'`; in `tests/test_runtime_pin_flag.py:40` change `'"v1gsp"'`→`'"v3"'`. Delete `test_repo2run_has_v1g_preset` in `tests/test_arm_v1g.py:5-10` (asserts a removed preset). Run those 3 files:
Run: `python3 -m pytest tests/test_graph_scheduler_flag.py tests/test_runtime_pin_flag.py tests/test_arm_v1g.py -q`
Expected: FAIL (the v3 assertions fail — preset still named v1gsps / intermediate arms present).

- [ ] **Step 3 (GREEN):** In `run_repo2run_benchmark.py`: reduce `_ARM_PRESETS` to `"0"` (legacy, all-off), `"v1"` (the existing v1 preset), and `"v3"` (the existing `v1gsps` preset value, renamed; `_label`="armV3_graph_scheduled"). Delete the 6 intermediate keys (`v1g/v1gd/v1gde/v1gder/v1gs/v1gsp`). Update `--arm choices=["0","v1","v3"]` and its help text. Verify `grep -n "v1gsps\|v1gsp\|v1gder\|v1gde\|v1gd\b\|v1g\b\|v1gs\b" run_repo2run_benchmark.py` returns nothing.

- [ ] **Step 4 (GREEN):** Fix the forwarding bug: in `build_agent_command` add a branch forwarding `enable_service_provision` (mirror how `enable_runtime_pin`/`enable_graph_scheduler` are forwarded — set the `DOCKERAGENT_ENABLE_SERVICE_PROVISION` env or the corresponding `--enable-*` flag, matching the existing pattern in that function). Confirm the v3 preset's `enable_service_provision=True` now reaches the child.

- [ ] **Step 5:** Run the 3 test files + a repo2run import smoke:
Run: `python3 -m pytest tests/test_graph_scheduler_flag.py tests/test_runtime_pin_flag.py tests/test_arm_v1g.py tests/test_deletions_final_verification.py tests/test_benchmark_arm_v1.py -q` → expect PASS.
Run: `python3 -c "import run_repo2run_benchmark"` → expect no error.

- [ ] **Step 6: Commit**
```bash
git add run_repo2run_benchmark.py tests/test_graph_scheduler_flag.py tests/test_runtime_pin_flag.py tests/test_arm_v1g.py
git commit -m "refactor(repo2run): collapse arms to {0,v1,v3}; forward enable_service_provision"
```

---

## Task 2 (C1): Reduce v3's maintainer to a done-gate (dep-graph as sole world-model)

**Files:**
- Modify: `src/envstate/deterministic_maintainer.py` (add `v3_only` + `_v3_done_gate`)
- Modify: `agent.py` (~:1207 maintainer construction)
- Test: `tests/test_deterministic_maintainer.py` (or the file that tests it — confirm by grep)

**Interfaces:**
- Produces: `DeterministicMaintainer(v3_only: bool = False)`; module-level `_v3_done_gate(current_map, report) -> WorldModelMap` that sets `done_flag`/`progress` with NO `contract_graph` write.
- Consumes: existing `_verified_test_run_passed`, `_progress_synced_with_done` (from `maintainer.py`), `merge_map`.

- [ ] **Step 1:** Read `.superpowers/sdd/phase2-research-B-worldmodel.md` for the consumer map and exact line refs.

- [ ] **Step 2 (RED):** Add a test in the maintainer test file:
```python
def test_v3_only_maintainer_does_not_write_contract_graph():
    m = DeterministicMaintainer(v3_only=True)
    base = _world_map_with_failing_report_input()   # reuse the file's fixture builder; a map with a non-empty contract_graph baseline
    report = TaskReport("t", "blocked", (CommandRecord("pytest", 1, "E ModuleNotFoundError: foo"),), "")
    out = m.update(base, report)
    assert out.contract_graph == base.contract_graph   # unchanged — no blocker write in v3
def test_v3_only_maintainer_sets_done_flag_on_verified_pass():
    m = DeterministicMaintainer(v3_only=True)
    base = _empty_world_map()
    report = TaskReport("t", "done", (CommandRecord("python -m pytest -q", 0, "3 passed"),), "")
    out = m.update(base, report)
    assert out.done_flag is True
def test_default_maintainer_still_writes_blockers():
    m = DeterministicMaintainer()   # v3_only defaults False → v1 behavior preserved
    base = _empty_world_map()
    report = TaskReport("t", "blocked", (CommandRecord("pytest", 1, "E ImportError: bar"),), "")
    out = m.update(base, report)
    assert not out.contract_graph.is_empty()   # blocker written (v1 path unchanged)
```
Use the test file's existing fixture builders (`_empty_world_map` etc.); confirm `_verified_test_run_passed`'s exact pass-signal expectation from `maintainer.py` so the "done" report triggers it.

- [ ] **Step 3 (RED run):** `python3 -m pytest <maintainer test file> -q -k "v3_only or still_writes"` → FAIL (`v3_only` not accepted yet).

- [ ] **Step 4 (GREEN):** In `deterministic_maintainer.py` add:
```python
def _v3_done_gate(current_map: WorldModelMap, report: TaskReport) -> WorldModelMap:
    """v3 done-gate only: host-evidence done_flag + progress, NO contract_graph write.
    The dep-graph is the sole world-model in v3; the blocker patch is vestigial there."""
    done = current_map.done_flag or _verified_test_run_passed(report)
    return merge_map(
        current_map,
        done_flag=done,
        progress=_progress_synced_with_done(current_map, done),
    )
```
and:
```python
class DeterministicMaintainer:
    def __init__(self, v3_only: bool = False):
        self._v3_only = v3_only
    def update(self, current_map: WorldModelMap, report: TaskReport) -> WorldModelMap:
        if self._v3_only:
            return _v3_done_gate(current_map, report)
        return maintain(current_map, report)
```
(Keep `maintain()` exactly as-is for the v1/default path.)

- [ ] **Step 5 (GREEN):** In `agent.py` (~:1207) construct it v3-aware:
```python
maintainer = DeterministicMaintainer(v3_only=self.enable_graph_scheduler)
```
(Confirm `self.enable_graph_scheduler` is the right v3 discriminator at that point — it is the scheduler gate.)

- [ ] **Step 6:** Run maintainer tests + a v3 orchestrator smoke + the v1 maintainer tests:
Run: `python3 -m pytest <maintainer test file> tests/test_orchestrator_v1.py -q` → PASS.

- [ ] **Step 7: Commit**
```bash
git add src/envstate/deterministic_maintainer.py agent.py <maintainer test file>
git commit -m "refactor(v3): reduce DeterministicMaintainer to done-gate; drop vestigial contract_graph write in v3"
```

---

## Task 3 (C2): Unify v3 termination behind an internal `TerminationReason`

**Files:**
- Modify: `src/envstate/orchestrator.py` (the v3 termination sites; still pre-split)
- Test: `tests/test_orchestrator_v1.py`

**Interfaces:** Produces an internal `TerminationReason` enum and a `_v3_stop_reason(reason) -> str` mapper. The function's RETURNED stop_reason strings are unchanged.

- [ ] **Step 1:** Read `.superpowers/sdd/phase2-research-C-termination.md` for the signal inventory + the six reasons.

- [ ] **Step 2 (RED):** Add a mapping test:
```python
from src.envstate.orchestrator import TerminationReason, _v3_stop_reason
def test_termination_reason_maps_to_legacy_strings():
    assert _v3_stop_reason(TerminationReason.DONE) == "planner_done"
    assert _v3_stop_reason(TerminationReason.DONE_FLAG) == "done_flag"
    assert _v3_stop_reason(TerminationReason.GIVEUP_RESIDUAL) == "planner_giveup"
    assert _v3_stop_reason(TerminationReason.GIVEUP_BUDGET) == "planner_giveup"
    assert _v3_stop_reason(TerminationReason.GIVEUP_STUCK) == "planner_giveup"
    assert _v3_stop_reason(TerminationReason.MAX_CYCLES) == "max_cycles"
```
Run: `python3 -m pytest tests/test_orchestrator_v1.py -k termination_reason -q` → FAIL (symbols absent).

- [ ] **Step 3 (GREEN):** Add to `orchestrator.py`:
```python
import enum
class TerminationReason(enum.Enum):
    DONE = "done"; DONE_FLAG = "done_flag"; GIVEUP_RESIDUAL = "giveup_residual"
    GIVEUP_BUDGET = "giveup_budget"; GIVEUP_STUCK = "giveup_stuck"; MAX_CYCLES = "max_cycles"
_TERMINATION_TO_STOP_REASON = {
    TerminationReason.DONE: "planner_done",
    TerminationReason.DONE_FLAG: "done_flag",
    TerminationReason.GIVEUP_RESIDUAL: "planner_giveup",
    TerminationReason.GIVEUP_BUDGET: "planner_giveup",
    TerminationReason.GIVEUP_STUCK: "planner_giveup",
    TerminationReason.MAX_CYCLES: "max_cycles",
}
def _v3_stop_reason(reason: "TerminationReason") -> str:
    return _TERMINATION_TO_STOP_REASON[reason]
```

- [ ] **Step 4 (GREEN):** Thread `TerminationReason` through the v3 termination sites: where the v3 path currently `return current_map, "<string>"`, compute a `TerminationReason` and return `current_map, _v3_stop_reason(reason)`. Map: `_budget_exhausted`/`_repair_turns<=0` → `GIVEUP_BUDGET`; `_residual_giveup` → `GIVEUP_RESIDUAL`; `_sched_stuck>=2` → `GIVEUP_STUCK`; `next_decision`→done → `DONE`; `done_flag` → `DONE_FLAG`; loop fallthrough → `MAX_CYCLES`. Do NOT change the v1 path's returns. The external strings are identical, so no other test changes.

- [ ] **Step 5:** Run orchestrator + the 12 stop-reason tests:
Run: `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py -q` → PASS (unchanged strings).

- [ ] **Step 6: Commit**
```bash
git add src/envstate/orchestrator.py tests/test_orchestrator_v1.py
git commit -m "refactor(v3): unify termination behind TerminationReason mapped to legacy stop strings"
```

---

## Task 4 (C3): Split `run_v1` into `run_v1()` + `run_v3()` sharing `_loop_common.py`

This is the largest, highest-risk task. The safety net: every existing orchestrator test (for both arms) must stay green, except the explicitly-named signature/wiring moves.

**Files:**
- Create: `src/envstate/_loop_common.py`
- Modify: `src/envstate/orchestrator.py` (split `run_v1`), `agent.py` (~:1330 dispatch)
- Test: `tests/test_orchestrator_v1.py`, `tests/test_graph_scheduler_flag.py`, the 3 wiring tests that monkeypatch `run_v1` by string (find via `grep -rn "orchestrator.run_v1\|orchestrator\", \"run_v1" tests/`)

**Interfaces:**
- Produces: `run_v1(planner, build_agent, maintainer, initial_world_map, ledger, sandbox_execute, max_cycles=..., local_budget=..., on_cycle=None, *, probe=None, manifest=None, exec_readonly=None, enable_dep_emit=False, enable_runtime_feedback=False)` — NO `enable_graph_scheduler`/`graph_scheduler_attempt_cap`.
- Produces: `run_v3(build_agent, maintainer, initial_world_map, ledger, sandbox_execute, max_cycles=..., on_cycle=None, *, probe=None, manifest=None, exec_readonly=None, graph_scheduler_attempt_cap=3)` — always graph-scheduler; dep_emit + runtime_feedback always on.
- Shared in `_loop_common.py`: `current_revision(ledger)`, `host_refresh_facts(current_map, probe, manifest)` (the `apply_deterministic(probe())` step), and any small ledger/step helpers — each a pure function taking explicit args (no closures).

- [ ] **Step 1:** Read `.superpowers/sdd/phase2-research-A-split.md` for the branch inventory and shared/arm-specific classification.

- [ ] **Step 2:** Create `src/envstate/_loop_common.py` with the genuinely-shared helpers as free functions (move `_current_revision` logic and the `probe()`+`apply_deterministic` refresh; keep signatures explicit). Add a focused unit test `tests/test_loop_common.py` for each moved helper (e.g. `current_revision` returns last `env_revision_after` or 0). Run it → PASS.

- [ ] **Step 3 (the split):** Create `run_v3()` by copying `run_v1`'s body and making the graph-scheduler path unconditional (delete the `else: planner.decide()` branch and the v1-only recipe path; keep the task/done/giveup branches; dep_emit + runtime_feedback always on; use the `TerminationReason` from Task 3). Then reduce `run_v1()` to the legacy path (remove the `enable_graph_scheduler` branches, `next_decision` import use, `_handed`/`_sched_*`/`_repair_turns` v3-only state; keep planner.decide + recipe execution + maintainer). Each function calls the shared helpers from `_loop_common.py`. Do NOT leave large duplicated blocks — only the small shared helpers are shared; the divergent loop bodies are expected to differ.

- [ ] **Step 4 (dispatch):** In `agent.py` (~:1330) change the single call site to:
```python
if self.enable_graph_scheduler:
    final_map, stop_reason = run_v3(build_agent, maintainer, initial_map, ledger, sandbox_execute, max_cycles=max_steps, on_cycle=_on_cycle, probe=..., manifest=..., exec_readonly=..., graph_scheduler_attempt_cap=...)
else:
    final_map, stop_reason = run_v1(planner, build_agent, maintainer, initial_map, ledger, sandbox_execute, max_cycles=max_steps, local_budget=..., on_cycle=_on_cycle, probe=..., manifest=..., exec_readonly=..., enable_dep_emit=..., enable_runtime_feedback=...)
```
(Preserve the existing argument values; only the function selection + dropped/added kwargs change. Add `from src.envstate.orchestrator import run_v3`.)

- [ ] **Step 5 (test moves):** Move `test_graph_scheduler_flag.py`'s assertion that `enable_graph_scheduler` is a `run_v1` parameter → assert it on `run_v3` instead (and that `run_v1` no longer has it). For the 3 wiring tests that monkeypatch `"src.envstate.orchestrator.run_v1"` by string: update each to patch `run_v3` (for the v3-arm cases) or assert the dispatch picks the right function. Do not weaken — they must still verify the agent calls the loop.

- [ ] **Step 6 (characterization regression):** Run the FULL orchestrator + agent-glue + scheduler suites:
Run: `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_orchestrator_recipe.py tests/test_orchestrator_recipe_no_graph.py tests/test_graph_scheduler_flag.py tests/test_agent_v1_glue.py tests/test_run_v1_integration.py tests/test_residual_handler_wiring.py -q`
Expected: PASS. Any failure that is NOT one of the named signature/wiring moves means the split changed behavior — STOP, report BLOCKED.

- [ ] **Step 7: Commit**
```bash
git add src/envstate/_loop_common.py src/envstate/orchestrator.py agent.py tests/test_loop_common.py tests/test_graph_scheduler_flag.py <the 3 wiring test files>
git commit -m "refactor(orchestrator): split run_v1 into run_v1()+run_v3() sharing _loop_common"
```

---

## Task 5 (C4): Consolidate v3 refresh points in `run_v3`

**Files:** Modify `src/envstate/orchestrator.py` (`run_v3`). Test: `tests/test_orchestrator_v1.py`.

- [ ] **Step 1:** In `run_v3`, identify the `probe()`+`apply_deterministic(...)` refresh calls and the certify in `_dep_emit_phase`. Reduce to a single refresh after each state-mutating step (after emit-drain, after a task's commands) — remove redundant consecutive re-probes that produce identical state.

- [ ] **Step 2 (regression):** Add/confirm a test that v3 still reaches `planner_done` on a clean graph (reuse the Phase-1 smoke `test_v3_graph_scheduler_reaches_planner_done_on_clean_graph`) and that a v3 run with one residual still certifies + finalizes. Run:
Run: `python3 -m pytest tests/test_orchestrator_v1.py -q` → PASS.

- [ ] **Step 3:** If consolidation risks changing observable behavior (e.g. a test asserts a specific refresh count or ledger length), prefer correctness over fewer calls — keep a refresh that a test depends on, and note it. Behavior of v3's outcomes must not change.

- [ ] **Step 4: Commit**
```bash
git add src/envstate/orchestrator.py tests/test_orchestrator_v1.py
git commit -m "refactor(v3): consolidate dep-graph refresh points to a predictable minimal set"
```

---

## Task 6 (C6): Restore deferred Phase-1 coverage

**Files:** `tests/test_orchestrator_v1.py` (v3 collect-only test), `tests/test_arm_plumbing.py` (tearDown), `conftest.py` (collect_ignore — conditional), `tests/test_orchestrator_per_step_outcome.py` (doc note).

- [ ] **Step 1 (v3 collect-only anti-hollow, RED):** Add:
```python
def test_v3_collect_only_does_not_finalize_as_done():
    """A pytest --collect-only rc=0 must NOT set done_flag / terminate run_v3 as done —
    the v3 done-gate requires a real verified test pass."""
    world = _world_map_with_clean_dep_graph()
    def collect_only_exec(cmd):
        # collect-only 'succeeds' but is not a real pass
        return (True, "collected 5 items") if "collect-only" in cmd else (False, "no tests ran")
    final_map, stop = run_v3(_NoopBuildAgent(), DeterministicMaintainer(v3_only=True),
                             world, ActionLedger(), collect_only_exec, max_cycles=2)
    assert final_map.done_flag is not True   # collect-only must not finalize
```
Adjust fixtures to the file's helpers and to how `_run_tests_verified` distinguishes a real pass from collect-only. Run → it should FAIL if the gate is hollow, PASS if the gate is honest (this characterizes the anti-hollow guarantee for v3). If it passes immediately, confirm by temporarily weakening to prove it can fail, then restore.

- [ ] **Step 2 (tearDown):** Add to `TestApplyArmEnv` in `tests/test_arm_plumbing.py`:
```python
    def tearDown(self):
        for k in [k for k in os.environ if k.startswith("DOCKERAGENT_ENABLE_")]:
            del os.environ[k]
```

- [ ] **Step 3 (collect_ignore):** Check whether `tests/test_run_rat_benchmark.py` now imports cleanly (Phase 1 added stubs): `python3 -m pytest tests/test_run_rat_benchmark.py -q`. If green, remove it from `conftest.py`'s `collect_ignore` so the full suite exercises it; re-run `python3 -m pytest tests/ -q` to confirm no new import-time failures. If it does NOT import cleanly under full collection, leave it and add a one-line comment in conftest explaining why.

- [ ] **Step 4 (doc note):** In `tests/test_orchestrator_per_step_outcome.py`, add a comment that per-step Attempt outcome coverage was contract-graph-specific (removed in Phase 1) and has no v3 equivalent (the scheduler produces no per-step Attempt nodes).

- [ ] **Step 5 (full suite):** `python3 -m pytest tests/ -q` then `python3 -m pytest tests/test_run_rat_benchmark.py tests/test_arm_plumbing.py -q`. Expected: green except the 5 known pre-existing failures (and, if collect_ignore was lifted, test_run_rat_benchmark now in the main run).

- [ ] **Step 6: Commit**
```bash
git add tests/test_orchestrator_v1.py tests/test_arm_plumbing.py tests/test_orchestrator_per_step_outcome.py conftest.py
git commit -m "test(v3): restore deferred coverage — v3 collect-only anti-hollow, arm tearDown, collect_ignore"
```

---

## Notes for the executor

- Re-locate every anchor by `grep -n`; line numbers drift after each task.
- The split (Task 4) is the crux: its correctness is proven by the full existing test suite staying green for both arms. If a non-named test breaks, the split changed behavior — stop and report.
- After all tasks: full-suite regression, then the final whole-branch review. **Re-baseline v3** before any benchmark comparison (operator step — v3 behavior changed).
