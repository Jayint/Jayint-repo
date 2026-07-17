# Unified Two-Arm Loop — Phase 1 (Safe Collapse) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the v3-branch agent from 9 flag-gated arms toward two — `v1` (three-role) and `v3` (today's `v1gsps`, renamed) — by removing the 6 intermediate arm presets, deleting the dead contract-graph machinery in the orchestrator, and renaming `v1gsps→v3`, all with **zero behavior change** for v1 and v3.

**Architecture:** Phase 1 is Approach A — keep one `run_v1()` loop with its existing `enable_graph_scheduler` fork (that fork *is* the v1-vs-v3 distinction; the function split into `run_v1`/`run_v3` is Phase 2). We only delete code that is unreachable in both surviving arms (`enable_contract_graph` is False in v1 and v3) and collapse the arm-preset table. The v0/`arm0` legacy path is **deferred** (kept reachable, removed in a later phase).

**Tech Stack:** Python 3.10, pytest. Files: `run_rat_benchmark.py`, `src/envstate/orchestrator.py`, `agent.py`, `tests/`.

## Global Constraints

- **Reference spec:** `docs/superpowers/specs/2026-06-28-unified-two-arm-loop-design.md`. Every task implicitly includes this section.
- **Zero behavior change for v1 and v3.** Each surviving arm must produce identical scheduler decisions, ledger commands, and `stop_reason` before vs. after. The full existing test suite is the regression gate — no passing test may break (update only tests that assert the *removed* intermediate-arm / contract-graph behavior).
- **Host owns truth.** Do not alter how state is certified (the dep-graph certify / `apply_deterministic` paths). We are deleting dead code, not changing certification.
- **Do NOT delete the `contracts/` package.** `ContractGraph` + blocker extraction are still used by both maintainers (`deterministic_maintainer.maintain()` calls `build_blocker_patch`; the LLM maintainer serializes the graph). Only the Attempt/outcome/advisory-done/host-projection *layer* is dead.
- **Do NOT touch the v0/legacy code** (`enable_supervisor`, `enable_fullstate_worker`, `enable_envstate`, the `run()` v0 branch at `agent.py:1860`). Deferred to a later phase. `arm0` stays a valid `--arm` choice and the default.
- **`v3` arm flag set** (identical to today's `v1gsps`): `DOCKERAGENT_ENABLE_V1=1`, `DEP_GRAPH=1`, `DEP_EMIT=1`, `RUNTIME_FEEDBACK=1`, `GRAPH_SCHEDULER=1`, `RUNTIME_PIN=1`, `SERVICE_PROVISION=1`, `CONTRACT_GRAPH=0`.
- **Immutability:** preserve the existing frozen-dataclass / `merge_map` patterns; never mutate `WorldModelMap` in place.
- **Commits:** conventional-commit messages; **NO `Co-Authored-By` trailer** (attribution disabled globally). `git add` only the **specific files** listed in each task — **never** `git add -A` / `.` / a directory (the repo has unrelated WIP).
- **Logging:** after the plan lands, append one Observation→Why→What→Verification entry to `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md` (handled in Task 4).

---

## File Structure

- `run_rat_benchmark.py` — owns the `--arm` interface: `_apply_arm_env` (preset→env), the worker child arm re-derivation, and the `--arm` argparse choices/help. **Task 1.**
- `src/envstate/orchestrator.py` — the `run_v1()` loop. Holds the dead `enable_contract_graph` branches + the no-op `_host_refresh`. **Tasks 2 & 3.**
- `agent.py` — one call-site passes `enable_contract_graph=` into `run_v1` (`:1342`); one comment says "arm v1gsps" (`:1766`). **Tasks 3 & 4.**
- `tests/test_arm_plumbing.py`, `tests/test_run_rat_benchmark.py` — arm-preset assertions. **Task 1.**
- `tests/test_contract_graph_v2_integration.py`, `tests/test_orchestrator_v1*.py`, `tests/test_progress_done_consistency.py` — exercise orchestrator paths; some assert contract-graph behavior being removed. **Tasks 2 & 3.**
- Launch scripts referencing `V1GSPS_FLAGS` / run-names, `tests/test_residual_handler_wiring.py`, depgraph comments — `v1gsps` string residuals. **Task 4.**

---

## Task 1: Collapse `--arm` presets to {arm0, v1, v3}

**Files:**
- Modify: `run_rat_benchmark.py` (`_apply_arm_env` ~`:763-781`; worker child arm re-derivation ~`:392-406`; `--arm` argparse `:831-846`)
- Test: `tests/test_arm_plumbing.py`, `tests/test_run_rat_benchmark.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `_apply_arm_env(arm)` accepting only `{"arm0","v1","v3"}`; `--arm` choices `["arm0","v1","v3"]` (default `"arm0"`); the child re-derivation returns one of those three.

- [ ] **Step 1: Rewrite the `_apply_arm_env` tests** in `tests/test_arm_plumbing.py` — replace every `v1gsp`/`v1gsps` case with `v3`, delete the intermediate-arm cases, keep `arm0`.

```python
class TestApplyArmEnv(unittest.TestCase):
    def setUp(self):
        for k in list(os.environ):
            if k.startswith("DOCKERAGENT_ENABLE_"):
                del os.environ[k]

    def test_v3_sets_full_stack(self):
        rrb._apply_arm_env("v3")
        for var in ("V1", "DEP_GRAPH", "DEP_EMIT", "RUNTIME_FEEDBACK",
                    "GRAPH_SCHEDULER", "RUNTIME_PIN", "SERVICE_PROVISION"):
            self.assertEqual(os.environ[f"DOCKERAGENT_ENABLE_{var}"], "1", var)

    def test_v3_clears_contract_graph(self):
        rrb._apply_arm_env("v3")
        self.assertEqual(os.environ["DOCKERAGENT_ENABLE_CONTRACT_GRAPH"], "0")

    def test_v1_sets_only_v1(self):
        rrb._apply_arm_env("v1")
        self.assertEqual(os.environ["DOCKERAGENT_ENABLE_V1"], "1")
        for var in ("DEP_GRAPH", "DEP_EMIT", "RUNTIME_FEEDBACK",
                    "GRAPH_SCHEDULER", "RUNTIME_PIN", "SERVICE_PROVISION", "CONTRACT_GRAPH"):
            self.assertEqual(os.environ[f"DOCKERAGENT_ENABLE_{var}"], "0", var)

    def test_arm0_clears_all_flags(self):
        rrb._apply_arm_env("arm0")
        for var in ("V1", "DEP_GRAPH", "DEP_EMIT", "RUNTIME_FEEDBACK",
                    "GRAPH_SCHEDULER", "RUNTIME_PIN", "SERVICE_PROVISION", "CONTRACT_GRAPH"):
            self.assertEqual(os.environ[f"DOCKERAGENT_ENABLE_{var}"], "0", var)
```

- [ ] **Step 2: Run the tests, watch them fail**

Run: `python -m pytest tests/test_arm_plumbing.py -q`
Expected: FAIL — `test_v3_*` raise `KeyError`/assertion because `_apply_arm_env` does not yet accept `"v3"`.

- [ ] **Step 3: Rewrite `_apply_arm_env`** in `run_rat_benchmark.py` to the two-arm table (arm0 kept as deferred-v0 default):

```python
def _apply_arm_env(arm: str) -> None:
    """Set DOCKERAGENT_ENABLE_* env vars for *arm*.

    Two supported arms plus the deferred legacy default:
      arm0 — legacy v0 ReAct (all flags off; removed when v0 is deleted)
      v1   — three-role Planner/BuildAgent/Maintainer loop
      v3   — graph-scheduled agent (v1 + dep-graph/emit/runtime-feedback/
             scheduler/runtime-pin/service-provision)
    """
    is_v1 = arm in ("v1", "v3")
    is_v3 = arm == "v3"
    os.environ["DOCKERAGENT_ENABLE_V1"] = "1" if is_v1 else "0"
    os.environ["DOCKERAGENT_ENABLE_CONTRACT_GRAPH"] = "0"
    os.environ["DOCKERAGENT_ENABLE_DEP_GRAPH"] = "1" if is_v3 else "0"
    os.environ["DOCKERAGENT_ENABLE_DEP_EMIT"] = "1" if is_v3 else "0"
    os.environ["DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK"] = "1" if is_v3 else "0"
    os.environ["DOCKERAGENT_ENABLE_GRAPH_SCHEDULER"] = "1" if is_v3 else "0"
    os.environ["DOCKERAGENT_ENABLE_RUNTIME_PIN"] = "1" if is_v3 else "0"
    os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] = "1" if is_v3 else "0"
```

- [ ] **Step 4: Run the tests, watch them pass**

Run: `python -m pytest tests/test_arm_plumbing.py -q`
Expected: PASS.

- [ ] **Step 5: Update the child arm re-derivation test** in `tests/test_run_rat_benchmark.py` — keep `test_child_cmd_arm0_when_v1_disabled` (arm0 stays), add a v3 case. The re-derivation maps env→arm string for the worker child.

```python
def test_child_cmd_v3_when_full_stack(monkeypatch):
    for v in ("V1","DEP_GRAPH","DEP_EMIT","RUNTIME_FEEDBACK",
              "GRAPH_SCHEDULER","RUNTIME_PIN","SERVICE_PROVISION"):
        monkeypatch.setenv(f"DOCKERAGENT_ENABLE_{v}", "1")
    cmd = rrb._build_worker_argv(  # use the actual function name/signature in the file
        full_name="o/r", root_path="/tmp/x", llm="deepseek-chat",
        timeout=10, num_turn=5, repos_json="d.json", model="dockeragent",
    )
    assert cmd[cmd.index("--arm") + 1] == "v3"

def test_child_cmd_v1_when_only_v1(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_V1", "1")
    for v in ("DEP_GRAPH","DEP_EMIT","RUNTIME_FEEDBACK",
              "GRAPH_SCHEDULER","RUNTIME_PIN","SERVICE_PROVISION"):
        monkeypatch.setenv(f"DOCKERAGENT_ENABLE_{v}", "0")
    cmd = rrb._build_worker_argv(
        full_name="o/r", root_path="/tmp/x", llm="deepseek-chat",
        timeout=10, num_turn=5, repos_json="d.json", model="dockeragent",
    )
    assert cmd[cmd.index("--arm") + 1] == "v1"
```

Note: confirm the worker-argv builder's real name and parameters in the file before writing (the re-derivation block is the `if os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1": arm = "v1gsps" …` ladder ~`:392-406`).

- [ ] **Step 6: Run, watch fail**

Run: `python -m pytest tests/test_run_rat_benchmark.py -q -k "child_cmd"`
Expected: FAIL — `test_child_cmd_v3_when_full_stack` gets `"v1gsps"`, not `"v3"`.

- [ ] **Step 7: Rewrite the child arm re-derivation** to {arm0, v1, v3}:

```python
    if os.environ.get("DOCKERAGENT_ENABLE_GRAPH_SCHEDULER") == "1":
        arm = "v3"
    elif os.environ.get("DOCKERAGENT_ENABLE_V1") == "1":
        arm = "v1"
    else:
        arm = "arm0"
```

(Replaces the whole `v1gsps/v1gsp/v1gs/v1gder/v1gde/v1gd/v1g/v1/arm0` ladder. `v3` keys off `GRAPH_SCHEDULER` — the lowest flag unique to v3 vs v1.)

- [ ] **Step 8: Collapse the `--arm` argparse choices + help:**

```python
    parser.add_argument("--arm", choices=["arm0", "v1", "v3"], default="arm0",
                        help="DockerAgent variant: 'arm0' = legacy v0 ReAct loop "
                             "(default; deferred for removal); "
                             "'v1' = three-role Planner/BuildAgent/Maintainer loop "
                             "(sets DOCKERAGENT_ENABLE_V1=1); "
                             "'v3' = graph-scheduled agent — graph DECIDEs, agent EXECUTEs, "
                             "host CERTIFIEs; v1 + dep-graph/emit/runtime-feedback/scheduler/"
                             "runtime-pin/service-provision.")
```

- [ ] **Step 9: Run the full arm-test files + grep for stragglers**

Run: `python -m pytest tests/test_arm_plumbing.py tests/test_run_rat_benchmark.py -q`
Expected: PASS.
Run: `grep -n "v1gsps\|v1gsp\|v1gder\|v1gde\|v1gd\|v1g\b\|v1gs\b" run_rat_benchmark.py`
Expected: no matches (every intermediate-arm token gone from this file).

- [ ] **Step 10: Commit**

```bash
git add run_rat_benchmark.py tests/test_arm_plumbing.py tests/test_run_rat_benchmark.py
git commit -m "refactor(arms): collapse --arm presets to {arm0, v1, v3}; rename v1gsps->v3"
```

---

## Task 2: Remove the dead `enable_contract_graph` branches from the orchestrator

`enable_contract_graph` is False in both v1 and v3, so every `if enable_contract_graph:` block (and the `if not enable_contract_graph:` wrapper around the immediate-done return) is dead. Keep recipe *execution*; delete attempt/outcome bookkeeping, the advisory-done path, and the `_graph_ready` gate.

**Files:**
- Modify: `src/envstate/orchestrator.py` (done branch `:338-365`; recipe attempt-commit `:392-404`; recipe outcome write-back `:426-459`)
- Test: `tests/test_orchestrator_v1.py`, `tests/test_orchestrator_v1_snapshot.py`, `tests/test_contract_graph_v2_integration.py`, `tests/test_progress_done_consistency.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `run_v1` whose `done` branch always returns `("…", "planner_done")` immediately and whose `apply_recipe_patch` branch contains no contract-graph bookkeeping. The `enable_contract_graph` *parameter still exists* after this task (removed in Task 3).

- [ ] **Step 1: Add a regression test** asserting the done branch returns `planner_done` immediately (no advisory verification) in `tests/test_orchestrator_v1.py`:

```python
def test_done_branch_returns_planner_done_immediately(monkeypatch):
    """action='done' returns ('…','planner_done') without running VERIFY_TEST_CMD."""
    calls = []
    def fake_exec(cmd):
        calls.append(cmd)
        return (True, "")
    planner = _StubPlanner(decisions=[PlannerDecision(action="done", reason="x")])
    final_map, stop = run_v1(
        planner, _NoopBuildAgent(), _NoopMaintainer(),
        _empty_world_map(), ActionLedger(), fake_exec, max_cycles=2,
    )
    assert stop == "planner_done"
    assert VERIFY_TEST_CMD not in calls   # advisory path is gone
```

Use the test file's existing stub/world-map helpers (mirror an existing `run_v1` test in the same file for fixtures). If a near-identical test already asserts this for the flag-off path, extend it instead of duplicating.

- [ ] **Step 2: Run, watch it pass-or-fail**

Run: `python -m pytest tests/test_orchestrator_v1.py::test_done_branch_returns_planner_done_immediately -v`
Expected: PASS already (flag defaults off today) — this test *pins* the behavior so the deletion can't regress it. If it ERRORs on fixtures, fix the fixtures to match the file's existing pattern, not the behavior.

- [ ] **Step 3: Delete the advisory-done path.** Replace the done branch (`:338-365`) with:

```python
        if decision.action == "done":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, "planner_done"
```

(Removes the `if not enable_contract_graph:` wrapper and the entire advisory block `:344-365`, including `_graph_ready`.)

- [ ] **Step 4: Delete the recipe attempt-commit block.** Remove `:392-404` (the `if enable_contract_graph:` block that commits Attempt nodes and `merge_map(contract_graph=…)`) and the now-orphaned `attempt_ids: list[str] = []` line. The recipe branch goes straight from the empty-recipe guard to `report = build_agent.run_recipe(...)`.

- [ ] **Step 5: Delete the recipe outcome write-back block.** Remove `:426-459` (the `if enable_contract_graph:` block deriving `_derive_outcome` and applying `update_attempts`). After it, the branch is: `run_recipe` → `apply_deterministic` (if probe) → `maintainer.update` → `on_cycle` → `if current_map.done_flag: return`.

- [ ] **Step 6: Run the orchestrator + contract-graph suites; update tests that assert removed behavior**

Run: `python -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_contract_graph_v2_integration.py tests/test_progress_done_consistency.py -q`
Expected: failures only in tests that drove `run_v1` with `enable_contract_graph=True` (the advisory-done / attempt-outcome behavior). For each: if it tests the removed contract-graph layer, delete that test (it covers code that no longer exists); if it tests v1/v3 behavior that *should* survive, the deletion broke something — stop and re-check. Re-run until green.

- [ ] **Step 7: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_contract_graph_v2_integration.py tests/test_progress_done_consistency.py
git commit -m "refactor(orchestrator): delete dead enable_contract_graph branches (advisory-done, attempt outcomes)"
```

---

## Task 3: Remove the no-op `_host_refresh`, the `enable_contract_graph` param, and dead imports

After Task 2, `enable_contract_graph` is referenced only by the no-op `_host_refresh` guard (`:112`), the function signature, and the agent.py call-site. `_host_refresh()` early-returns in both arms, so it and its 8 call-sites are no-ops.

**Files:**
- Modify: `src/envstate/orchestrator.py` (`_host_refresh` def `:110-118`; 8 call-sites `:300,357,385,418,465,505,516` — note `:357` is already gone after Task 2; imports `:18-24`; signature `:66`)
- Modify: `agent.py` (`run_v1` call-site `:1342` drops `enable_contract_graph=`)
- Test: `tests/test_orchestrator_v1.py`, `tests/test_agent_v1_glue.py`

**Interfaces:**
- Consumes: Task 2's de-branched orchestrator.
- Produces: `run_v1(...)` signature with **no** `enable_contract_graph` parameter; no `_host_refresh` symbol.

- [ ] **Step 1: Add a signature-guard test** in `tests/test_orchestrator_v1.py`:

```python
import inspect
from src.envstate.orchestrator import run_v1

def test_run_v1_has_no_contract_graph_param():
    params = inspect.signature(run_v1).parameters
    assert "enable_contract_graph" not in params
```

- [ ] **Step 2: Run, watch it fail**

Run: `python -m pytest tests/test_orchestrator_v1.py::test_run_v1_has_no_contract_graph_param -v`
Expected: FAIL — the param still exists.

- [ ] **Step 3: Delete `_host_refresh`** (the `def _host_refresh(): …` at `:110-118`) and remove all its call-sites: the remaining `_host_refresh()` lines (`:300, :385, :418, :465, :505, :516` — `:357` was removed in Task 2). Each is a bare no-op statement; delete the line.

- [ ] **Step 4: Remove the `enable_contract_graph` parameter** from the `run_v1` signature (`:66`) and its docstring mention (`:87`).

- [ ] **Step 5: Remove the now-dead imports** from `orchestrator.py` top (`:18-24`):
`from src.envstate.contracts import attempts as _attempts`, `apply_patch as _apply_patch`, `goal_ready as _graph_ready`, `GraphPatch`, `validate_patch as _validate_patch`, `derive_attempt_outcome as _derive_outcome`, and `refresh_host_graph as _refresh_graph` (`:22`). **Keep** any `contracts` import still used elsewhere in the file (verify with grep in Step 7).

- [ ] **Step 6: Update the agent.py call-site** (`:1342`) — delete the `enable_contract_graph=getattr(self, "enable_contract_graph", False),` line from the `run_v1(...)` call. Leave the rest of agent.py's `enable_contract_graph` plumbing (`:325/345/349/1291/1315`) untouched (deferred v0 cleanup).

- [ ] **Step 7: Verify no dangling references**

Run: `grep -n "_host_refresh\|enable_contract_graph\|_graph_ready\|_derive_outcome\|_refresh_graph\|_apply_patch\|_validate_patch\|GraphPatch\|_attempts" src/envstate/orchestrator.py`
Expected: no matches (all removed). If `_apply_patch`/`_validate_patch`/`GraphPatch` are still referenced, a Task-2 block was missed — fix before continuing.

- [ ] **Step 8: Run the signature test + orchestrator + agent-glue suites**

Run: `python -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_agent_v1_glue.py -q`
Expected: PASS (update any test that passed `enable_contract_graph=` into `run_v1` to drop the kwarg).

- [ ] **Step 9: Commit**

```bash
git add src/envstate/orchestrator.py agent.py tests/test_orchestrator_v1.py tests/test_agent_v1_glue.py
git commit -m "refactor(orchestrator): drop no-op _host_refresh + enable_contract_graph param + dead imports"
```

---

## Task 4: Rename `v1gsps→v3` residuals + full-suite regression + v3 smoke

**Files:**
- Modify: `agent.py` (comment `:1766`), `tests/test_residual_handler_wiring.py` (`v1gsps` refs), `src/python_deps/depgraph/service_scan.py` / `build.py` / `certify.py` (comment refs), launch scripts referencing `V1GSPS_FLAGS`/run-name (e.g. `/opt/runs/ml15_v3.sh` lives on the VM — out of this repo; note it for the operator)
- Modify: `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md`
- Test: full suite + a v3 integration smoke

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: no functional change — string/comment rename + changelog.

- [ ] **Step 1: Update `tests/test_residual_handler_wiring.py`** — rename the `v1gsps` arm references to `v3` (e.g. the test that sets the full flag stack / names the arm). Keep the assertions; only the label/flag-set naming changes. Run it:

Run: `python -m pytest tests/test_residual_handler_wiring.py -q`
Expected: PASS.

- [ ] **Step 2: Rename code comments** — `agent.py:1766` (`# Binding bake pass (arm v1gsps):` → `# Binding bake pass (arm v3):`) and the `v1gsps` mentions in `src/python_deps/depgraph/service_scan.py`, `build.py`, `certify.py` (comments only — confirm each is a comment, not a code token, before editing).

Run: `grep -rn "v1gsps" agent.py src/`
Expected: no matches.

- [ ] **Step 3: Add a v3 integration smoke test** (characterization — v3 still drives the graph-scheduler loop end-to-end) in `tests/test_orchestrator_v1.py`:

```python
def test_v3_graph_scheduler_reaches_planner_done_on_clean_graph(monkeypatch):
    """With graph-scheduler on and an already-satisfied graph + passing tests,
    run_v1 terminates via the scheduler's done path (no contract-graph needed)."""
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "0")
    # clean dep-graph (empty frontier) + tests pass → next_decision returns 'done'
    world = _world_map_with_clean_dep_graph()      # mirror existing graph-scheduler test fixtures
    def passing_exec(cmd):
        return (True, "1 passed")
    final_map, stop = run_v1(
        _UnusedPlanner(), _NoopBuildAgent(), _NoopMaintainer(),
        world, ActionLedger(), passing_exec, max_cycles=3,
        enable_graph_scheduler=True, enable_dep_emit=True, enable_runtime_feedback=True,
    )
    assert stop == "planner_done"
```

Use the existing graph-scheduler test fixtures in the file (find the test that constructs a dep-graph and `run_v1(..., enable_graph_scheduler=True)`; copy its fixture builders). If such a smoke already exists, skip this step.

- [ ] **Step 4: Run the smoke**

Run: `python -m pytest tests/test_orchestrator_v1.py::test_v3_graph_scheduler_reaches_planner_done_on_clean_graph -v`
Expected: PASS.

- [ ] **Step 5: Run the FULL suite (the Phase-1 regression gate)**

Run: `python -m pytest -q`
Expected: PASS — zero failures. Any failure is either (a) a test asserting removed intermediate-arm/contract-graph behavior (delete/update it), or (b) a real regression (stop and fix). Do not weaken a test to make it pass.

- [ ] **Step 6: Append the changelog entry** to `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md`:

```markdown
## 2026-06-28 — Phase 1: two-arm collapse (v1 + v3)
**Observation:** v3-branch carried 9 flag-gated arms; contract-graph machinery + no-op _host_refresh were dead in both surviving arms; v3 uses DeterministicMaintainer (no LLM), not the LLM maintainer.
**Why:** collapse to {v1, v3} for a clean, unified loop; behavior-preserving first (in-flight benchmark stays valid).
**What:** --arm → {arm0(deferred-v0), v1, v3}; renamed v1gsps→v3; deleted enable_contract_graph branches (advisory-done, attempt outcomes), no-op _host_refresh + host-graph projection, dead imports + the run_v1 enable_contract_graph param. contracts/ package kept (used by both maintainers). v0/supervisor/fullstate code deferred.
**Verification:** full pytest suite green; v1/v3 decisions + stop_reasons unchanged; v3 graph-scheduler smoke reaches planner_done.
```

- [ ] **Step 7: Commit**

```bash
git add agent.py tests/test_residual_handler_wiring.py tests/test_orchestrator_v1.py src/python_deps/depgraph/service_scan.py src/python_deps/depgraph/build.py src/python_deps/depgraph/certify.py docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md
git commit -m "refactor(arms): rename v1gsps->v3 residuals; v3 smoke; changelog"
```

> **Operator note (not a code change):** the VM launch script `/opt/runs/ml15_v3.sh` sets `V1GSPS_FLAGS` and `--run-name v1gsps-...`. After this lands, update it to pass `--arm v3` (or keep the explicit env block) and rename the run-name. This is outside the repo and is done on the box, not in this plan.

---

## Notes for the executor

- **Line numbers drift** as you edit. The anchors above are from HEAD `4ff5de3`; after each task, re-locate by symbol (`grep -n`) rather than trusting absolute line numbers.
- **Confirm the worker-argv builder's real name/signature** in `run_rat_benchmark.py` before writing Task 1 Step 5 (the re-derivation block precedes the `return [PY, __file__, …]` list).
- **The `contracts/` package stays.** If a deletion makes a `contracts/*` import unused *in orchestrator.py*, remove only that import line — do not touch the package or `deterministic_maintainer.py` / `maintainer.py`.
- **Phase 2 is a separate plan** (split `run_v1`/`run_v3`, reduce v3's DeterministicMaintainer to the done-gate, drop the vestigial `contract_graph` writes, unify the termination signals) — do not start it here.
