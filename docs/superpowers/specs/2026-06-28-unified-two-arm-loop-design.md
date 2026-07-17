# Unified Two-Arm Agent Loop — Design

**Date:** 2026-06-28
**Branch:** `john-planner-v3`
**Status:** Approved (brainstorming) → ready for implementation planning (Phase 1 first)

## Goal

Collapse the v3-branch agent loop from **9 flag-gated runtime arms** down to **two clean
agents** — `v1` (the three-role baseline) and `v3` (the graph-scheduled agent, today's
`v1gsps`) — and make the surviving v3 path a single-source-of-truth, host-certified loop
with one well-defined termination story. The work is sequenced into two phases: a
behavior-preserving collapse (Phase 1) and a behavior-simplifying cleanup (Phase 2).

## Background

### Two unrelated naming schemes both say "v1/v2/v3"

1. **Git branches** (`john-planner-v1` frozen Jun 23, `john-planner-v2` frozen Jun 22,
   `john-planner-v3` live) are snapshots of the *whole repo* over time. Only the v3
   branch runs; the launch script provisions it with `git reset --hard
   origin/john-planner-v3`. v1/v2 are frozen history.

2. **Runtime "arms"** (`arm0 < v1 < v1g < v1gd < v1gde < v1gder < v1gs < v1gsp <
   v1gsps`) are *flag presets defined inside the v3-branch code*
   (`run_rat_benchmark.py:767-779`). `--arm X` flips a set of `DOCKERAGENT_ENABLE_*`
   env vars. The benchmark runs the v3 branch with `--arm v1gsps`.

The collision: branch `john-planner-v1` (an old whole-repo copy) is **not** the same as
arm `v1` (the three-role loop in *today's* v3 code with all graph features off). The arm
`v1` is the foundation every higher arm stacks onto (`ENABLE_V1=1` is set for every arm
from v1 up).

### Why the code is messy

The arm ladder is **additive**: every feature (dep-graph, emit, runtime-feedback,
scheduler, runtime-pin, service-provision, and the earlier contract-graph) was added
behind its own flag under a "the off-state must stay byte-identical to the prior arm"
rule, so **nothing ever replaced anything**. The single v3-branch codebase therefore
contains all 9 arms as flag-gated paths. Concretely, in `src/envstate/orchestrator.py`:

- **Dual world-model.** Env state is represented twice: the legacy `WorldModelMap`
  (`installed`/`required`/`open_problems`/`notes`) *and* the certified `DepGraph`. In v3
  the DepGraph is the host-owned source of truth, yet a maintainer still rebuilds the
  legacy map every cycle. **v3 uses `DeterministicMaintainer` (no LLM)** — selected
  because the graph-scheduler forces `enable_deterministic_maintainer` on
  (`agent.py:345`, swapped in at `agent.py:1206`). The **LLM** maintainer
  (`maintainer.py:736`) runs only in **v1**. In v3 the deterministic maintainer's only
  load-bearing output is the done-gate (`_verified_test_run_passed(report)` →
  `done_flag`); its blocker writes into `contract_graph` are **vestigial** — v3's
  scheduler reads `dep_graph`, never `contract_graph`/`open_problems`.
- **Dead-in-both-arms machinery.** `CONTRACT_GRAPH=0` in both surviving arms, so the
  `apply_recipe_patch` contract-graph *bookkeeping* (attempt commit/validate/apply,
  `_derive_outcome`), the advisory-done path (`orchestrator.py:339-365`), `_graph_ready`,
  and the **host-graph projection** are unreachable. The latter is total: `_host_refresh()`
  early-returns when `enable_contract_graph` is False (`orchestrator.py:112`), so all 8
  calls + `refresh_host_graph`/`_refresh_graph` are no-ops in v1 and v3. NOTE: the
  `contracts/` package itself is **not** dead — `ContractGraph` + blocker extraction are
  used by both maintainers (`maintain()` calls `build_blocker_patch`; the LLM maintainer
  serializes the graph). Only the Attempt/outcome/projection/advisory-done layer is dead.
- **Flag-layering.** 21 `enable_graph_scheduler` / `enable_contract_graph` branches in one
  function.
- **Scattered termination.** Stop logic spread across `_budget_exhausted`,
  `_residual_giveup`, `_repair_turns<=0`, `_sched_stuck>=2`, maintainer `done_flag`,
  `next_decision`→done, and the build-agent stuck-guard.

### Architecture-critical fact (verified)

The final Dockerfile is built by **replaying the `ActionLedger`**
(`agent.py:_synthesize_final_build_recipe` / `_backfill_successful_actions_from_ledger`),
**not** from the `WorldModelMap`. The legacy map's only load-bearing outputs in v3 are
`done_flag` (final-verification labeling, `agent.py:1360/1365`) and `installed`
(telemetry, `agent.py:1732/3202`) — both cleanly re-sourceable from the certified
dep-graph. **Phase 2 therefore does not touch artifact emission.**

## Target architecture

The v3 loop, stripped of accretion, is one small thing:

```
v3 cycle:
  certify        # HOST runs check_commands → flips dep-graph state   (WHETHER)
  emit-drain     # install the deterministic closure, no LLM          (cheap WHAT)
  pick 1 residual obligation from the frontier                        (WHAT/WHEN)
  bounded LLM works it, host-checked, cannot self-finalize            (HOW)
  ingest new test failures as graph nodes
until frontier clean AND tests pass   (or one bounded give-up)
```

**Structure: A then C.**

- **Phase 1 (Approach A):** keep a single loop function with one `is_v3` fork. Minimal
  churn; each surviving arm's behavior is byte-for-byte preserved.
- **Phase 2 (Approach C):** split into `run_v1()` and `run_v3()` sharing a small
  host-certify / sandbox / ledger helper module. No `enable_*` flags. No strategy-object
  abstraction (only two arms — YAGNI).

## Phase 1 — Safe collapse (zero behavior change)

Each surviving arm produces identical decisions, ledger, and artifact before vs. after.

1. **Reduce arm presets 9 → 2.** In `run_rat_benchmark.py`, `--arm` choices become
   `{v1, v3}`. Because the 7 intermediate arms are removed, the six graph flags can only
   occur all-on (v3) or all-off (v1); collapse them to a single internal `is_v3` switch.
   Keep the `--arm` vocabulary (the harness, launch scripts, and attribution tooling all
   speak "arm").
2. **Delete the dead contract-graph machinery** — unreachable at `CONTRACT_GRAPH=0` in both
   arms (do NOT delete the `contracts/` package; both maintainers use `ContractGraph` +
   blocker extraction):
   - the Attempt/outcome bookkeeping in the recipe branch (`orchestrator.py:392-459`:
     attempt commit/validate/apply, `_derive_outcome`) — but keep recipe *execution*
     (v1's path);
   - the advisory-done path (`orchestrator.py:339-365`) and `_graph_ready`;
   - the **host-graph projection**: `_host_refresh()` early-returns at
     `orchestrator.py:112` (no-op in both arms), so remove the function, its 8 call-sites,
     and the `refresh_host_graph`/`_refresh_graph` import.
   Confirm the now-unused imports (`_attempts`, `_apply_patch`, `_graph_ready`,
   `_validate_patch`, `_derive_outcome`, `_refresh_graph`, `GraphPatch`) are removed from
   `orchestrator.py` and that no remaining v1/v3 path references them.
4. **Rename `v1gsps` → `v3`** everywhere: arm name and `--arm` choices/help, run-names,
   the `V1GSPS_FLAGS` block in launch scripts, code comments (e.g. the "arm v1gsps"
   comments in `agent.py`), and doc references. Update any harness tooling that enumerates
   arm names (e.g. attribution / ESSR scripts) to the `{v1, v3}` set; past result
   directories keep their original arm labels and are untouched.
5. **Verification:** the existing test suite (508+) is the regression net — deletion must
   not break a single passing test. Add a characterization smoke test: v1 and v3 produce
   identical scheduler decisions and ledger commands on a fixture repo before vs. after
   the collapse. Benchmark comparisons remain valid → no re-baseline.

## Phase 2 — Behavior simplification (re-baseline after)

1. **Split** the loop into `run_v1()` and `run_v3()` sharing extracted helpers
   (host-certify/refresh, sandbox execute, ledger setup, probe/`apply_deterministic`).
   Each arm reads top-to-bottom as a focused function.
2. **v3 single source of truth.** v3 has **no maintainer LLM call** — it already uses
   `DeterministicMaintainer`. The cleanup is structural: reduce v3's maintainer to its only
   load-bearing output (the done-gate, `_verified_test_run_passed(report)` → `done_flag`,
   already host-evidence-based) and **stop writing the vestigial `contract_graph` blockers**
   that v3's scheduler never reads — making `dep_graph` the sole world-model in v3.
   Re-source the remaining legacy outputs from the certified graph: `done_flag` stays from
   the host-evidence gate (or folds into the loop's "frontier clean AND tests pass"
   decision); `installed` (telemetry, `agent.py:1732/3202`) ← certified PACKAGE nodes.
   **v1 keeps its LLM maintainer, `WorldModelMap`, and `contract_graph` serialization
   untouched** — they are v1's core.
3. **Unify stop-signals** in v3 into a single `TerminationReason` resolved in one place,
   replacing `_budget_exhausted` / `_residual_giveup` / `_repair_turns` / `_sched_stuck`
   as independent scattered checks. "Why did the run stop?" has one answer.
4. **Consolidate the real dep-graph refresh points.** The dead `_host_refresh()` is already
   gone (Phase 1). What remains in v3 is the genuine state refresh — `probe()` +
   `apply_deterministic(...)` and the certify in `_dep_emit_phase`. Consolidate these into a
   minimal, predictable set (e.g. once after each state-mutating step) so it is obvious when
   graph state is fresh.
5. **Re-baseline.** v3 behavior has changed (no maintainer call, etc.), so run a fresh
   benchmark to establish the new v3 baseline before any comparison is trusted.

## Out of scope (YAGNI)

- Free-text ReAct parser hardening in `build_agent` (empty-response re-prompts,
  composition self-check, heredoc reconstruction). Localized and somewhat inherent to
  ReAct; leave unless it becomes a problem.
- Strategy-object pattern (Approach B): only two arms.
- Old `john-planner-v1` / `john-planner-v2` git branches: frozen history, untouched. The
  v1 *behavior* lives in the v3 code as the graph-off arm.
- `radical` / `repo2run` / `ccdf` baselines: different agents/models, not arms of
  john-planner. Unaffected.

## Testing strategy

TDD throughout (RED → GREEN → REFACTOR).

- **Phase 1:** the full existing suite is the regression gate; deletion-only changes must
  keep every passing test green. Add a before/after characterization test asserting v1 and
  v3 emit identical scheduler decisions and ledger commands on a fixture repo.
- **Phase 2:** new tests for `done_flag`-from-graph, `installed`-from-graph, the unified
  `TerminationReason`, and an explicit assertion that **v3 makes zero maintainer LLM
  calls**. v1's maintainer path keeps its existing tests.

## Risks & sequencing

- One spec, two phases. Phase 1 is behavior-preserving and independently shippable; land
  it (benchmark stays valid) before planning Phase 2.
- Phase 1's main risk is deleting something still reachable in v1; mitigated by the
  regression suite + characterization smoke and by keeping recipe *execution* intact.
- Phase 2 changes v3 behavior; mitigated by re-baselining and by re-sourcing `done_flag`
  / `installed` from the host-certified graph (verified consumers above).
