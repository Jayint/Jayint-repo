# Design Spec — Deterministic Maintainer + Generalized Certification

**Status:** Draft v2 (revised after spec review + effectiveness analysis, 2026-06-24)
**Date:** 2026-06-24
**Branch:** john-planner-v3
**Depends on:** the DepGraph → Contract Graph seed adapter (commits `7b72500..282b7d9`), which puts a runnable `check` command on every depgraph-seeded atomic contract.

> **v2 changes:** corrected the as-is description (3 certification paths, the existing auto-resolve machinery, the real call sites); reprioritized **Change B (deterministic Maintainer) as the primary, standalone change** because the LLM Maintainer's paraphrasing is a *confirmed active bug* that suppresses the existing auto-resolve on ~20-30% of repos; demoted **Change A (generalized certify) to a narrow soname follow-up**; reversed the claimed B-needs-A dependency; pinned the `exec_readonly` interface; fixed the "reuse `promote_atomic_contracts` for blockers" error (it emits Contracts, not Blockers).

---

## 1. Motivation

The v1g loop runs three LLM roles — Planner, BuildAgent, Maintainer — over a `WorldModelMap` carrying a `ContractGraph`. The driving observation, sharpened by analysis:

**The LLM Maintainer is not just a mechanical bookkeeper — it is actively breaking deterministic recovery.** The host already has two deterministic auto-resolve paths that retire a blocker once the host observes the missing thing is present:
- `_auto_resolve_system_problems` (`world_model.py:363-390`) — fires only when a problem's `layer == "system"`.
- `_auto_resolve_blockers` (`projection.py:71-112`) — matches a violated Contract's `subject` against the `present` set (installed ∪ system_installed ∪ import_ok).

Both are defeated by the LLM Maintainer:
- It labels native/system blockers `layer="deps"` (default) instead of `"system"` → `_auto_resolve_system_problems` never engages.
- It **paraphrases** the subject (`"postgres-config"` for `pg_config`) → `_auto_resolve_blockers`' name-normalized match fails.

Result: the agent correctly runs `apt-get install libpq-dev`, but the blocker never retires, and the Planner burns its remaining cycles on redundant/confused follow-ups. This is documented in `DESIGN-grounded-repair-memory.md §1.4` and confirmed in the code. It affects the **~20-30% of repos with a native/system dependency** (`command-not-found` appears in 24% of benchmark result files, `libXX.so` in 14%).

A **deterministic Maintainer** that emits the *raw* failure signature and the *correct* layer (from `extract.py`'s `{binary→system, system_library→system}` map) makes both auto-resolve paths fire — closing a loop that is currently open. Secondarily, it removes a source of nondeterminism (hallucinated blocker ids / mismatched `evidence_refs` that get the whole `graph_patch` rejected by `validate_patch`).

The **Planner and BuildAgent keep their LLMs** — execution and strategy are where genuine in-container judgment lives.

## 2. Goals / Non-Goals

**Goals**
- **(Change B, primary)** Replace the LLM Maintainer with a deterministic host module that emits raw-signature blockers with correct layers, records attempts, and classifies outcomes — so the existing auto-resolve machinery fires.
- **(Change A, follow-up)** Generalize host certification so a depgraph-seeded atomic contract is certified by running its own `check` in the live container — closing the *soname* `system_library` gap that even auto-resolve can't reach.
- Preserve every certification invariant: only a live check (or host-observed presence) flips a contract to satisfied; host owns truth; state is revocable.
- Keep each change behind its own flag for independent A/B.

**Non-Goals (deferred)**
- Removing the LLM from the **BuildAgent** or **Planner**.
- Mutating the **depgraph** at runtime (it stays a frozen, consume-once prediction; runtime facts land on the contract graph; `source_refs` preserves the link). Optional `origin_ref` write-back is the separate live-graph path.
- **Selective / touch-based invalidation.** We recompute `host_satisfied` wholesale each cycle (the code already does this).
- **Proactive blockers** from depgraph `MISSING` predictions.
- Changing the **done-gate** (`_verified_test_run_passed`) — already real-run, not collect-only.

## 3. Current Architecture (as-is) — CORRECTED

```
Planner(LLM) ─Task─► BuildAgent(LLM) ─TaskReport─► Maintainer.update()[LLM] ─► new WorldModelMap
                          │                              │
                   runs commands in              complete_with_retry → parse_v1_maintainer_reply
                   the live container            → graph_patch (add_blockers/contracts/edges,
                                                   diagnostic_notes) + done-gate
```

**Certification is split across THREE host paths (all in `refresh_host_graph`, `projection.py`):**
1. `host_satisfied_set` (`validators.py:113-127`) — certifies **only `python_import`** contracts (in-container import sweep).
2. `projection.py:175-181` — certifies **`repo_tests_pass`** (+ its `depends_on` closure) when `done_flag` and a verified test command exist.
3. `projection.py:168-174` — certifies **`repo_tests_collect`** (+ closure) when `--collect-only` rc=0. *(collect-only still certifies the collect contract; it no longer gates DONE.)*

**Existing host determinism (the machinery Change B unlocks):**
- `_auto_resolve_blockers` (`projection.py:71-112`) — retires a Blocker when its Contract `subject` is in `present`. Feeds `evidence_satisfied` → `host_satisfied`.
- `_auto_resolve_system_problems` (`world_model.py:363-390`) — layer-gated (`=="system"`).
- `commit_attempt` (`contracts/attempts.py:28`, host scope) — records an Attempt with `created_from_target_node_ids`.
- `derive_attempt_outcome` (`validators.py:130-148`) — classifies ok / ok_but_still_blocked / failed from (rc, target `project_status`). **Note: returns `"ok"` when a step has no `target_node_ids` (line 148).**
- `extract.promote_atomic_contracts` (`extract.py:37-52`) — regex signature → **Contract** node (type `"Contract"`, `check=""`). **Does NOT emit Blockers or edges.**
- `extract.extract_blocker_subject` (`extract.py:19-26`) — signature → `(subject, kind)` via 4 rules (module-not-found, command-not-found, missing-`.so`, missing-`.h`); layer map at `extract.py:48` `{python_import→deps, binary→system, system_library→system}`.
- `_verified_test_run_passed` (`maintainer.py:192-241`) — the real-run done-gate (6 conditions, N≥1 passed).
- `derive_open_problems` (`world_model.py:557-571`) — materializes `OpenProblem`s **exclusively from active Blockers**; the Planner reads `open_problems`.

**The LLM call to replace:** `Maintainer.update()` (`maintainer.py:665-764`) — it calls `complete_with_retry`, then `parse_v1_maintainer_reply` (`maintainer.py:576-638`, a pure parser). Change B replaces `update()`, not the parser.

## 4. Target Architecture (to-be)

### 4.1 Role split

| role | LLM? | responsibility | change |
|---|---|---|---|
| Planner | **LLM** | choose next target from the frontier | none |
| BuildAgent | **LLM** | execute the recipe, adapt commands, fix local issues | none |
| Maintainer | **deterministic** | emit raw-signature blockers (correct layer), record attempt, classify outcome, preserve `learning` | **Change B (primary)** |
| Host certify | deterministic | run depgraph-seeded contracts' own `check` (soname gap) | **Change A (follow-up)** |

### 4.2 Change B (PRIMARY) — Deterministic Maintainer

Replace `Maintainer.update()` with a host function `maintain(current_map, report) -> WorldModelMap` that builds one `GraphPatch` (scope `"host"`) and applies it. **It must emit Blockers** — without them `derive_open_problems` returns empty and the Planner goes dark.

Per BuildAgent cycle:

1. **Extract blockers (raw signature + correct layer) — NEW code, not pure reuse.** For each `CommandRecord.output` in the report:
   - `subject, kind = extract_blocker_subject(line)` for matching lines (the 4 rules).
   - Ensure the Contract exists: `promote_atomic_contracts` (gives the `Contract` with `layer` from the `extract.py:48` map — `binary`/`system_library` → `"system"`).
   - **Build a `Blocker` node** (this is the new part — `promote_atomic_contracts` does NOT do it): `signature` = the **verbatim** failure line (so `_auto_resolve_blockers`' subject match works), `kind`, `layer` (= the contract's layer), `subject`, `root_or_downstream="root"` (default; refine later), `evidence_refs=[]`, `active=True`.
   - Emit a `violates` Edge: Blocker → Contract.
   - Use `GraphPatch(add_contracts=, add_blockers=, add_edges=)` with **scope `"host"`** (bypasses the maintainer-scope rule that `evidence_refs` be non-empty Attempt ids). Do NOT use scope `"maintainer"`.
2. **Record the attempt** — `commit_attempt(graph, step, proposed_by="planner")` (existing).
3. **Classify the outcome** — `derive_attempt_outcome(rc, target project_status)`:
   - rc==0 & target satisfied → `ok`
   - rc==0 & target still unsatisfied → `ok_but_still_blocked` (loop-breaker; carried on the Attempt, read by the Planner via `attempts_for_contract`, `graph.py:187-199`)
   - rc!=0 → `failed`
   - **Required fix:** a step with no `target_node_ids` currently defaults to `"ok"` (`validators.py:148`). Either default conservatively to `failed`, or guarantee every recipe step carries a target. Decide before rollout (Open Q1).
4. **Preserve diagnosis** — store `TaskReport.learning` as a `diagnostic_note` (Planner reads `## Recent Diagnoses` from `graph.diagnostic_notes`, `render.py:113`).
5. **Done-gate** — call `_verified_test_run_passed(report)` directly (already deterministic). Unchanged.

No LLM call. The Maintainer remains the map's single writer.

**Why this is standalone (does NOT need Change A):** for `binary` and most `system_library` blockers, emitting the raw subject + `layer="system"` lets the *existing* `_auto_resolve_blockers` / `_auto_resolve_system_problems` retire the blocker once the install lands in `present` (e.g. `pg_config` is already probed by `SYSTEM_TOOL_PROBES`, `extractor.py:8-12`). No new certification path is required for these.

### 4.3 Change A (FOLLOW-UP) — Generalized certification for the soname gap

Some `system_library` contracts cannot be retired by auto-resolve because the **soname doesn't map to the dpkg name** (`libGL.so.1` is not `libgl1`; the `present`-set fuzzy match at `projection.py:102-104` fails). The *only* authoritative signal is the contract's own check: `ldconfig -p | grep libGL.so.1`. Change A runs it.

**Algorithm (in `refresh_host_graph`, after the existing certification):**
1. **Interface:** `exec_readonly` is `(str) -> (bool, str)` (`Executor`, success **bool** + output) — **not** rc. Test the bool: `ok, out = exec_readonly(check)`. *(My v1 spec said "rc==0"; `True == 0` is `False` in Python — do not write that.)*
2. Guard `if exec_readonly is None: skip` (it is `None` when the contract-graph feature is off, and is currently received-but-ignored at `projection.py:119`).
3. Collect atomic contracts that are (a) not already in `host_satisfied`/`evidence_satisfied` and (b) have a **non-empty `data["check"]`**. Only depgraph-seeded contracts qualify — reactively-promoted contracts have `check=""` (`extract.py:50`). So Change A is gated on `--enable-dep-graph`.
4. Run each check in the live container, **with a per-check timeout** (an adversarial/broken check must not hang the cycle). `ok==True → add to host_satisfied` AND feed `evidence_satisfied` so `_auto_resolve_blockers` retires the matching Blocker in the same pass.
5. Must coexist with the 3 existing certification paths (§3); do not clobber the `repo_tests_collect` path.

**Read-only requirement:** seeded checks come from depgraph node `check_command` (`depgraph_seed.py:60`). These are read probes (`ldconfig -p | grep`, `command -v`, `python -c "import X"`). Keep it so; never run a state-mutating check.

### 4.4 Data flow (post-BuildAgent step)

```
TaskReport{commands:[{cmd, rc, output}], learning}
   │
   ▼ §4.3 CERTIFY (host, Change A)   run depgraph-seeded contracts' check → host_satisfied' + evidence_satisfied'
   │ §3   AUTO-RESOLVE (host, existing) retire Blockers whose subject ∈ present (now fires, thanks to Change B's raw subjects)
   ▼
§4.2 MAINTAIN (host, deterministic, Change B)
   1 extract Blockers (raw signature + system layer) + violates edges  [NEW]
   2 record Attempt (commit_attempt)
   3 classify outcome (derive_attempt_outcome)  → ok | ok_but_still_blocked | failed
   4 learning → diagnostic_note
   5 single apply_patch (scope="host")
   6 done = _verified_test_run_passed(report)
   │
   ▼ new WorldModelMap → Planner(LLM) next cycle
```

## 5. Invariants Preserved

- **Certification truth:** a contract reaches satisfied only via a host-run check exit-0 **or** host-observed presence (`_auto_resolve_blockers`). Never the LLM, never `apt/pip` exit-0 alone.
- **Revocability:** `host_satisfied` recomputed wholesale each cycle.
- **Bottom-up:** a child satisfying does not auto-satisfy the parent; the parent satisfies only on its own evidence.
- **Immutability:** `merge_map` / `apply_patch` return new objects.
- **Host owns truth; depgraph frozen:** runtime facts land on the contract graph; `source_refs=["depgraph:<id>"]` preserves the link.

## 6. Component Change Summary (corrected)

| component | disposition |
|---|---|
| `commit_attempt` (`contracts/attempts.py`) | **reuse** |
| `derive_attempt_outcome` (`validators.py`) | **reuse** + fix the no-target default (Open Q1) |
| `extract_blocker_subject` / `promote_atomic_contracts` (`extract.py`) | **reuse for (subject, kind, Contract)** |
| **build Blocker node + `violates` edge from `(subject,kind)`** | **NEW** (promote does NOT emit blockers) |
| import-sweep certification (`host_satisfied_set`) | **reuse** |
| `_auto_resolve_blockers` / `_auto_resolve_system_problems` | **reuse** — *unlocked* by Change B's raw subjects + system layer |
| `_verified_test_run_passed` (done-gate) | **reuse**, unchanged |
| `Maintainer.update()` (LLM) | **replace** with deterministic `maintain(...)` (NOT just the parser) |
| `refresh_host_graph` — run depgraph-seeded contracts' `check` via `exec_readonly` (bool) | **extend (Change A)** with `None`-guard + timeout |
| Planner, BuildAgent | **unchanged** |

## 7. Sequencing — B first, standalone; A is the soname residual

The v1 spec claimed "B needs A." **Corrected: B stands alone.** Emitting raw subjects + `layer="system"` lets the *existing* auto-resolve retire binary/system blockers without any new certification. Change A only adds the narrow soname case (`libGL.so.1`) that auto-resolve structurally cannot match. Therefore:

1. **Ship Change B first** (bigger slice, confirmed bug, no dependency).
2. **Then Change A** to close the soname residual (opencv → `libGL.so.1` and friends).

## 8. Expected Effectiveness on in-docker ESSR (prediction, not yet measured)

Grounded in code + a scan of 420 benchmark result files. These are predictions; the real numbers need the A/Bs in §9. No `v1g+change` run artifacts exist yet.

| change | mechanism | addressable slice | expected Δ ESSR÷all | confidence |
|---|---|---|---|---|
| **B (deterministic Maintainer)** | raw signature + system layer → existing auto-resolve fires; blocker retires after the apt fix instead of the Planner spinning; also removes patch-rejection nondeterminism | ~20-30% of repos (native/system dep failures: 24% command-not-found, 14% libXX.so) | net **positive** | **moderate-high** |
| **A (generalized certify)** | certifies soname `system_library` contracts auto-resolve can't match (`ldconfig -p \| grep libGL.so.1`) | ~4-10 repos (depgraph ON + soname predicted; mostly opencv) | **+0.04 to +0.12** | low-medium |

Honest caveats: much of A's effect is cycle-efficiency, not binary success (the Planner often recovers via the `python_import:cv2` sweep the next cycle anyway). B's magnitude depends on how often the Planner actually spins on a mis-labeled blocker vs. re-issuing the right apt command from its own reasoning. The **in-docker / in-sandbox** metric is the right target here — the synthesizer "drops installs" bottleneck affects only eval-replay ESSR, not the live container, so it does not confound these changes (but A2 from `honest-success-and-metrics-plan.md` — emit a real in-sandbox pass signal — should be on to separate in-sandbox from eval-replay).

## 9. Rollout & Measurement

- **Change B** — flag `DOCKERAGENT_DETERMINISTIC_MAINTAINER` (default off). Off → `Maintainer.update()` LLM runs as today.
  - A/B: `v1g` vs `v1g + B`, scored with `compute_essr` (`score_agent`). **Fast-signal subset:** the ~12-15 repos that hit a `command-not-found`/`libXX.so` failure *and* issued an `apt-get install` but didn't converge — where the paraphrasing loop burns cycles. **Negative control:** pure `module_not_found` (pip-only) repos, where B should be neutral. Secondary metric: cycles-per-success.
- **Change A** — flag `DOCKERAGENT_GENERALIZED_CERTIFY` (default off). Off → certification byte-identical to today. Requires `--enable-dep-graph` on both arms (else no seeded checks). A/B on the opencv/soname subset.
- **Pre-measurement static check (no run):** count `ok_but_still_blocked` outcomes on `contract:system_library:*` / `contract:binary:*` targets in existing cycle logs — that directly counts the waste B (and A, for sonames) eliminates.

## 10. Open Questions — adjudicated

1. **No-target attempt outcome (REAL, must fix).** `derive_attempt_outcome` returns `"ok"` for a step with no `target_node_ids` (`validators.py:148`); the default `RecipeStep.target_node_ids` is `()`. A clean command on a target-less step is falsely `ok`. Fix: default conservatively or guarantee targets. **Resolve in Change B.**
2. **Ineffective-fix memory (SOLVED, not open).** Carried as the Attempt `outcome="ok_but_still_blocked"` and read by the Planner via `attempts_for_contract` (`graph.py:187-199`); preserved across `merge_map`. No new storage.
3. **Done-gate tightening (keep as-is).** Requiring the `repo_tests_pass` *contract* certified is circular (it certifies iff `done_flag` + verified test event). `_verified_test_run_passed(report)` alone is correct. Optional pass-rate (`>=0.8`) tightening is a separate follow-up.
4. **Regex long-tail miss rate (genuinely empirical).** The 4 rules miss CMake (~5%), version conflicts, generic build errors. Measure how often a real blocker falls outside them (the LLM would have caught it); the BuildAgent `learning` note partially mitigates. Decide later whether a *constrained* LLM blocker-classifier is worth re-adding.

## 11. Out of Scope (pointers)

- Live depgraph / persistent container (`origin_ref` write-back) — the live-graph path, deferred.
- Pass-rate (`>=0.8`) done-gate tightening — separate follow-up.
- Atomic→atomic `depends_on` chaining from the depgraph `requires` chain — frontier already orders by layer.
- Re-adding a constrained LLM blocker-classifier for the regex long-tail — pending Open Q4 measurement.
