# Unified Two-Arm Loop — Phase 2 (Split + Simplify) Design

**Date:** 2026-06-28
**Branch:** `john-planner-v3`  •  **Base:** `039aefd` (Phase 1 complete)
**Status:** Autonomous design (research-grounded). Behavior-CHANGING for v3 → re-baseline after.

## Goal

Finish the two-arm unification started in Phase 1. Make the v3 (graph-scheduler) arm a clean, single-source-of-truth loop and physically separate it from the v1 (three-role) arm, so each reads top-to-bottom as one coherent agent. This phase **changes v3 behavior** (it stops doing vestigial work), so a fresh v3 baseline is required before any A/B comparison is trusted. v1 behavior is preserved exactly.

This design is grounded in four research passes (see `.superpowers/sdd/phase2-research-{A,B,C,D}-*.md`).

## Components

### C1 — v3 single source of truth (the dep-graph)

**Finding (research B, exhaustive grep):** in the v3 path, **nothing reads `contract_graph`** — the scheduler (`graph_scheduler.next_decision`/`scheduler_frontier`) consumes only `dep_graph`; the LLM planner is bypassed; `refresh_host_graph` is never called live; the `contract_graph.jsonl` log is gated on `enable_contract_graph` (always False in v3). v3 uses `DeterministicMaintainer`, whose `maintain()` writes `done_flag`, `progress`, **and** a `contract_graph` blocker patch (`deterministic_maintainer.py:78-93`). Only `done_flag` (+`progress`) is load-bearing; the blocker write is vestigial.

**Design:** Reduce v3's maintainer to its done-gate. Add `v3_only: bool = False` to `DeterministicMaintainer.__init__`; when set, `update()` calls a new module-level `_v3_done_gate(current_map, report)` that returns `merge_map(current_map, done_flag=..., progress=...)` using the existing `_verified_test_run_passed` + `_progress_synced_with_done`, with **zero `contracts` imports / no `contract_graph` write**. Wire `v3_only=self.enable_graph_scheduler` where the maintainer is constructed (`agent.py:~1207`). v1's `DeterministicMaintainer`/LLM `Maintainer` paths and `contract_graph` serialization are untouched.

**`installed`** is already sourced from certified `NodeType.PACKAGE`/`State.SATISFIED` dep-graph nodes in `_dep_emit_phase` (`orchestrator.py:141-144`) — no change needed. **`done_flag`** stays a host-evidence gate; `agent.py` already treats it as a hint and scans the ledger first (`_resolve_v1_verified_test_run`).

**Anti-hollow invariant preserved:** the done-gate still requires `_verified_test_run_passed(report)` (a real rc-0 test pass), never an LLM/action-implied finalize.

### C2 — Unified termination for v3

**Finding (research C):** v3 termination is spread across `_budget_exhausted`, `_residual_giveup`, `_repair_turns<=0`, `_sched_stuck>=2`, `done_flag`, and `next_decision`→done/giveup, yielding stop-reason strings `planner_done` / `done_flag` / `planner_giveup` / `max_cycles`. `planner_giveup` is overloaded across three distinct causes; **no test asserts a sub-case of it**.

**Design:** Introduce an internal `TerminationReason` enum for `run_v3` with six members — `DONE`, `DONE_FLAG`, `GIVEUP_RESIDUAL`, `GIVEUP_BUDGET`, `GIVEUP_STUCK`, `MAX_CYCLES`. Resolve termination in ONE place. **Map back to the existing stop-reason strings at the return boundary** (`DONE→"planner_done"`, `DONE_FLAG→"done_flag"`, `GIVEUP_*→"planner_giveup"`, `MAX_CYCLES→"max_cycles"`) so `agent.py` and the 12 stop-reason-asserting tests are unaffected. The finer reason is available internally (and can be logged) but does not change the external contract. This is a *clarity* change, not a contract change.

### C3 — Split `run_v1` into `run_v1()` + `run_v3()`

**Finding (research A):** after C1/C2 the arms diverge enough that a clean split is natural. The single production dispatch is `agent.py:1330` (inside `DockerAgent._run_v1`). `run_v1`'s nested closures share `nonlocal` state (`current_map`, `global_step`, `_handed`, `_repair_turns`, …).

**Design (Approach C):** Two top-level functions in `orchestrator.py`:
- `run_v1(planner, build_agent, maintainer, ...)` — the three-role/legacy loop. Drops `enable_graph_scheduler` / `graph_scheduler_attempt_cap`; the decision step is always `planner.decide()`; the V3-only gates in the ingest/emit phases are removed.
- `run_v3(build_agent, maintainer, ...)` — the graph-scheduler loop. Drops `planner` / `local_budget`; decision is always `next_decision(dep_graph, ...)`; ingest/emit V3 logic is unconditional; the task branch always uses `budget=5`, `check=task.done_when`.

Extract the **genuinely-shared, low-state** helpers (`_current_revision`, the `probe()`+`apply_deterministic` refresh, ledger/step bookkeeping) into a small `src/envstate/_loop_common.py`. Do **not** force the large arm-specific loop bodies through a shared abstraction and do **not** duplicate large blocks verbatim — each arm owns its loop body; only the small shared helpers move. Carry mutable loop state explicitly (a small `LoopState` dataclass or per-function `nonlocal`, implementer's choice — whichever keeps each function readable without verbatim duplication).

Dispatch becomes `if self.enable_graph_scheduler: run_v3(...) else: run_v1(...)` at `agent.py:1330`. **Test risk (research A):** `test_graph_scheduler_flag.py` asserts `enable_graph_scheduler` in `run_v1`'s signature (move the assertion to `run_v3`); three wiring tests monkeypatch `"src.envstate.orchestrator.run_v1"` by string (they must also patch `run_v3`, or assert dispatch selection).

### C4 — Consolidate v3 refresh points

The dead `_host_refresh` was removed in Phase 1. The genuine v3 state refresh is `probe()`+`apply_deterministic(...)` and the certify inside `_dep_emit_phase`. Consolidate these in `run_v3` to a predictable minimal set (once after each state-mutating step) so it is obvious when graph state is fresh. No behavior change beyond removing redundant re-probes.

### C5 — Collapse `run_repo2run_benchmark.py` arms (the Phase-1 gap)

**Finding (research D):** `run_repo2run_benchmark.py` carries its own 9-key `_ARM_PRESETS` (`:3177-3246`), 9-way `--arm` choices (`:3406`), and `v1gsps` refs — never touched by Phase 1.

**Design:** Mirror Phase 1's collapse: `_ARM_PRESETS` and `--arm` choices become `{"0", "v1", "v3"}` (drop the 6 intermediates; rename the `v1gsps` preset → `v3`, keep its flag set + a sensible `_label`; keep `"0"` as the legacy default, default stays `None`). **Test changes (only 3 assertions):** delete `test_repo2run_has_v1g_preset` (`test_arm_v1g.py:5-10`); `"v1gs"→"v3"` (`test_graph_scheduler_flag.py:119`); `"v1gsp"→"v3"` (`test_runtime_pin_flag.py:40`). `test_benchmark_arm_v1.py` and `test_deletions_final_verification.py` need no change. **Bug to fix alongside (research D):** the `v1gsps`/v3 preset sets `enable_service_provision` on the namespace, but `build_agent_command` never forwards it (no env var set) — add the forwarding branch so v3 service-provision is not dead under the repo2run harness.

### C6 — Restore deferred Phase-1 test coverage

- Add a v3-appropriate **collect-only anti-hollow test** (replacing the contract-graph one deleted in Phase 1): a `pytest --collect-only` rc-0 must NOT set `done_flag` / must NOT terminate `run_v3` as done — the v3 done-gate requires a real verified pass.
- The BUG-10 **per-step outcome** coverage (removed in Phase 1) is contract-graph-specific and does not apply to v3; document this rather than restore it (no v3 mechanism produces per-step Attempt outcomes).
- Lift `tests/test_run_rat_benchmark.py` from conftest `collect_ignore` **only if** it now imports cleanly (Phase 1 added stubs); otherwise leave it and note why.
- Add `tearDown` to `TestApplyArmEnv` (clears `DOCKERAGENT_ENABLE_*`).

## Out of scope (YAGNI)

- The v0/legacy code (`enable_supervisor`/`enable_fullstate_worker`/`enable_envstate`) and `arm0`'s removal — still deferred (a later phase).
- Strategy-object abstraction for the two arms (only two — research A also recommends against).
- Any change to the dep-graph engine, scheduler logic, or certification semantics.

## Testing strategy

TDD throughout. Per component:
- **C1:** v3 run leaves `contract_graph` empty/unwritten; `done_flag` still set on a verified pass; `installed` reflects certified packages; v1 maintainer path unchanged.
- **C2:** each `TerminationReason` maps to the correct legacy stop-reason string; existing stop-reason tests stay green.
- **C3:** `run_v1` and `run_v3` each produce, for their arm, the SAME decisions/ledger/stop-reason as the pre-split `run_v1` did for that arm (characterization); dispatch at `agent.py:1330` selects correctly; the named wiring tests updated.
- **C4:** v3 still reaches `planner_done` on a clean graph; refresh count reduced without changing outcomes.
- **C5:** `--arm {0,v1,v3}` set the right namespace flags incl. `enable_service_provision` forwarded for v3; the 3 test assertions updated.
- **C6:** the new v3 collect-only test fails-first on a hollow finalize and passes on the real gate.

The full suite is the regression gate; the 5 known pre-existing failures (verified in Phase 1) remain out of scope.

## Risks & sequencing

Recommended task order (small/isolated → big/structural): **C5** (isolated to repo2run + 3 tests + the forward bug) → **C1** (small, v3 world-model) → **C2** (termination, still pre-split) → **C3** (the split — biggest; v3 behavior now finalized) → **C4** (refresh consolidation inside run_v3) → **C6** (coverage). The split (C3) is the highest-risk step; its safety net is the characterization that each arm's decisions/ledger/stop-reason are unchanged, plus the full suite.

**Re-baseline:** after Phase 2, v3 behavior has changed (no contract_graph write; possibly fewer re-probes). Run a fresh v3 benchmark before trusting any comparison. This is an operator step, not a code task.
