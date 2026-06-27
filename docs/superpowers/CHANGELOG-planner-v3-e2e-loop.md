# Change Log — planner-v3 e2e autonomous loop

> Per-change record. **Every code change** in this loop gets an entry with:
> **Observation** (what the evidence showed) → **Why** (the root cause / rationale, philosophy-checked) → **What changed** (exact files/behavior) → **Verification** (test + honest-scorer/e2e result).
> Philosophy guardrail on every entry: host owns truth; no LLM-declared / action-implied success; no weakened done-gate/certify.

---

## Iteration 0 — dominant blocker: cycle-1 `planner_giveup`

### Investigation (no code change yet)
- **Observation:** On the `v1gsp` pilot, `NevaMind-AI/memU-server` (`advisory: 16 frontier / 30 satisfied`) and `stlehmann/pyads` (`1 frontier / 5 satisfied`) both `stop_reason='planner_giveup'` on **cycle 1** with `configuration_success=False`, *after* the emit-drain installed the closure and a snapshot was created. `wafw00f` (no bad residuals) succeeded. Runtime-pin certified `satisfied` on both give-up repos → not the pin.
- **Code trace (committed `15799d5`):**
  - `orchestrator.py:290-291` returns `planner_giveup` when `_residual_giveup is not None` — **before** `next_decision` runs.
  - `_residual_giveup` set at `orchestrator.py:257-265`: `(diverged or _out_of_scope) and not partition(new_graph).emittable`. After the drain, `emittable` is empty → collapses to "any divergent/out-of-scope residual exists."
  - `ensure_python_shim` (`orchestrator.py:131` → `depgraph_live.py:50`) runs `ln -sf ... /usr/local/bin/python` through the **mutating** `sandbox_execute`; log shows it is **rejected by sandbox preflight** (`sandbox.py:219-222`).
  - `_runtime_ingest_phase` (`orchestrator.py:200-202`) classifies **every** ledger event since the mark, incl. the rejected shim + initial test failures, via an LLM classifier → `_out_of_scope`.
  - `_is_actionable` (`schedule.py:28`) excludes `CONFIG`/`SERVICE`; memU's 16 "frontier" are Config env-var nodes → NOT in `scheduler_frontier`. The give-up preempts the **discover-task sufficiency loop** (`graph_scheduler.py`).
- **Candidate mechanisms (under adversarial verification by opus research agent):**
  - **A** give-up gate incomplete (fires while discover-task/actionable work un-attempted),
  - **B** harness-shim residual pollution (rejected shim manufactures the `out_of_scope` residual),
  - **C** Config frontier noise (unsatisfiable env-var nodes inflate the partition).
- **Status:** root-cause + minimal-fix design pending the research agent's verified report (`scratchpad/loop/iter0-rootcause.md`). Fix entry will be appended below once landed.

### Change 1 — residual give-up only on host-grounded divergence; don't classify success events  (`54d892a`)
- **Observation:** memU-server & pyads gave up `planner_giveup` on **cycle 1** with the closure installed + a snapshot created. Adversarial root-cause (opus, refuted shim/config alternatives): their preserved `action_ledger` had exactly ONE event — the emit-drain's **successful** `pip install` (`rc=0`). `_runtime_ingest_phase` fed that success to the LLM classifier (prompted to explain a *failure*), which returned a non-env verdict → `note_out_of_scope` → `_out_of_scope` non-empty. With the closure drained (`partition().emittable == ()`), `(diverged or _out_of_scope) and not emittable` was True → `_residual_giveup` set → give-up fired at `orchestrator.py:290` **before `next_decision` / the discover-task ever ran**. `diverged` was empty (`found == []`); the give-up was driven purely by an LLM claim about a success.
- **Why:** Runtime feedback is a **failure** classifier — classifying a *successful* command is the bug. And an LLM-only `_out_of_scope` must never finalize the run (give-up is a *negative* finalization; host-owns-truth says no LLM-declared/action-implied finalization). The give-up must be host-grounded (`diverged` = residual mapping to a host-certified SATISFIED node, spec §8) and must not preempt the discover-task sufficiency loop.
- **What changed (`src/envstate/orchestrator.py`, `_runtime_ingest_phase`):**
  - Edit 2 (root removal, line 202): `obs = [(e.cmd, e.stdout) for e in new_events if e.rc != 0]` — never hand succeeded events to the failure classifier.
  - Edit 1 (defense-in-depth, line ~258): give-up now requires `diverged and not scheduler_frontier(new_graph) and not partition(new_graph).emittable` — dropped LLM-only `_out_of_scope` as a sole trigger; added the empty-`scheduler_frontier` guard so it can't preempt actionable agent work. No `done_flag` write, no gate weakened.
- **Verification:** Two behavioral tests added to `tests/test_residual_handler_wiring.py` (`..._success_event_does_not_give_up_on_cycle1`, `..._out_of_scope_without_divergence_does_not_finalize_giveup`). Confirmed **RED against pre-fix `15799d5`** (`assert 'planner_giveup' != 'planner_giveup'`) → **GREEN post-fix**. Adjacent suites 14 passed (residual/runtime_feedback/graph_scheduler/runtime_divergence); orchestrator sweep 19 passed. **End-to-end impact pending the v1gsp e2e re-run** (this removes the premature give-up; whether memU/pyads then *succeed* or hit the next blocker is the empirical question for Verify).
- **Philosophy check:** ✅ only raises the bar for a negative finalization; give-up now host-grounded (`diverged`) not LLM-claim-driven; separation of powers + Maintainer-sole-writer unchanged; bounded by existing `_sched_stuck>=2` + `max_cycles` (Config nodes excluded from `scheduler_frontier`/`partition`, can't thrash).

<!-- Append one ### entry per landed change: Observation / Why / What changed / Verification -->
