# Single-Loop (ReAct) Build-Script Repair Agent — Design

**Date:** 2026-07-09 · **Status:** Design approved, ready for implementation plan · **Branch:** john-v3-multi-lang

## Goal

Replace the multi-level repair loops (run_v3's 3-level nesting; arm C's error-loop + per-error session) with **ONE flat ReAct loop**, ported from the `radical` branch's `DockerAgent.run`, adapted so the agent **patches a build script and re-runs it fresh each step** instead of mutating a live container, with **no container versioning**, while keeping the host **graph-certify update** as an observability side-channel.

## Architecture (in three sentences)

A single ReAct loop over `max_steps`. Each step: reset the container to base → run the **whole** build script → `certify_all` the graph (observability) → if the script is green **and** the test suite passes ≥ 80%, return DONE. Otherwise the LLM planner reads the failure (build log or test output) plus its compressed running history and emits **one** action — a read-only exploration command run in the post-run container, or a replacement build script — and the loop repeats.

**Tech stack:** Python; the existing `Sandbox` (Docker) with `reset_to_base` / `run_install_script` / `exec_readonly`; `render_build_script`; `certify_refresh` / `certify_all`; an LLM client via `complete_with_retry`.

---

## 1. Motivation & context

The current repair systems are more structure than an initial system needs. run_v3 nests three loops (cycle → `run_structured_repair` ≤5 → `propose` ≤4 ReAct turns); arm C is two levels (error loop + sustained session). Both are hard to reason about.

The `radical` branch (`agent.py`, `DockerAgent.run`) is a single flat ReAct loop — plan → execute → observe — and is far simpler. We port that shape, but keep the two properties that make our system better than the repo2run-style loop `radical` is based on:

1. **Build-script repair, not live mutation.** The agent edits a build script; the loop re-runs the *whole* script fresh each step. The script *is* the state → reproducible. (repo2run mutates a live container step-by-step → not reproducible.)
2. **Host-certified graph state.** After each run, `certify_all` runs the graph's node checks so the graph reflects host-verified reality.

This is explicitly an **initial** system: **script-primary now** (the agent patches script text directly, no typed-patch gate), **graph-guided repair later** (see §12).

## 2. The core loop

```
script  = render_build_script(graph)          # INITIAL script comes from the graph (once)
history = History()                             # per-run ReAct transcript + observation compression
for step in range(max_steps):
    reset_to_base()                             # fresh base — replaces snapshots/rollback
    result = run_install_script(script)         # run the WHOLE script (the only durable mutation)
    graph  = certify_all(graph, exec_readonly)  # host checks flip node state (observability)

    if result.ok:
        test = run_test_gate()                  # python -m pytest -q, once
        if test.ok:                             # rc-agnostic: passed/executed >= 0.8, executed >= 1
            return DONE, script, graph
        observation = tail(test.output)         # tests ran but < 80% passed
    else:
        observation = tail(result.stderr)       # the build script failed

    thought, action = planner.plan(history, script, observation)   # ONE move
    if action.kind == EXPLORE:
        obs = exec_readonly(action.command)     # read-only poke at the post-run container
        history.record(step, action, obs)
    elif action.kind == PATCH:
        script = action.new_script              # replace the build-script TEXT wholesale
        history.record(step, action, "patched")
    else:                                        # unparseable → re-prompt for a valid action
        history.record(step, action, FORMAT_REMINDER)
return GIVEUP, script, graph
```

That is the entire top level. No inner loop, no sub-loop, no counters beyond `max_steps`.

## 3. Components

Each unit has one responsibility and a clear interface.

- **Container adapter** — the existing `Sandbox`. Uses only `reset_to_base()`, `run_install_script(script) -> InstallResult(rc, failing_command, lineno, stderr)`, and `exec_readonly(cmd) -> (rc, output)`. No snapshots/rollback (see §8).
- **Build script (state)** — a plain string. Seeded once from `render_build_script(graph)`; thereafter the agent's patched text is authoritative. This is the run's source of truth.
- **Planner** — the LLM. Given the compressed history + current script + latest observation, returns `thought` + exactly one action. Owns the ReAct prompt and the parse of the model's reply into an action.
- **Action** — a small typed value: `EXPLORE(command)` or `PATCH(new_script)`. §4 defines the surface syntax and parsing.
- **Done gate** — host-owned. `run_test_gate()` runs the test command once and returns a `TestResult`; `test_verdict` decides pass (§5). The agent never declares success.
- **Graph update** — `certify_all` / `certify_refresh(graph, exec_readonly, cycle)` after each run. Updates node states for observability and the future graph-guided phase. Does **not** gate repair or done in this version.
- **History + observation compression** — per-run step records + the planner's managed message history, with both compression tiers (§6).

The arm's package layout and its import boundary with the shared platform are defined in §14.

## 4. Agent action vocabulary

The planner replies in ReAct form. Its reply carries a `Thought:` and exactly one of:

- **Explore** — `Action: <one read-only shell command>`. Run via `exec_readonly` against the just-run container; the output becomes next step's observation. Read-only investigation only (e.g. `ldconfig -p`, `pip show x`, `apt-cache search`, `cat`, `ls`). The loop rejects a non-read-only command with a reminder (reuse the existing `is_read_only` guard).
- **Patch** — `Script:` followed by exactly one fenced ` ```bash ` block containing the **complete** updated `setup.sh`. The loop replaces the script string wholesale and re-runs it next step.

There is **no** agent "done" action — the host decides done automatically (§5). Full-script emission (rather than a diff) is chosen for robustness (no fragile diff application); a bad regeneration that drops a working line surfaces immediately as `rc != 0` on the next run and the agent fixes it. Diff-based patching is a future optimization (§12).

## 5. Done condition (host-owned)

After a script run:

- If `result.rc != 0` → not done; observation = build failure tail.
- If `result.rc == 0` → run the test command **once** (`python -m pytest -q`), then apply `test_verdict`:

```
passed, failed, errors = parse pytest summary line   # regex, ANSI-stripped
executed = passed + failed + errors                   # skipped excluded
ok = executed >= 1 and passed / executed >= 0.8
```

**DONE iff the script is green AND `test_verdict.ok`.** No agent-claimed success, no verification bundle, no `rc == 0` trust (an all-skipped / zero-collected run returns rc 0 but `executed == 0` → not ok). This is intentionally **more lenient than run_v3** (which requires all-pass): the loop repairs everything below 80% (the agent reads the failure and patches), and accepts ≥ 80% as done. Collection/import errors count in the denominator so they drag the rate down and get repaired.

**Avoid a double test run.** `certify_all` walks the execution layers including `TESTS`, whose node check is also `python -m pytest -q` — so a naive step would run the suite twice. The done gate's single test run is authoritative: run the suite once, feed that result to both `test_verdict` (the gate) and, if needed, the test node's state. Certify should refresh only the install-tier node states each step (or be given the already-captured test result), never re-invoke pytest.

## 6. Observation compression (both tiers, per-run)

Ported from `radical`. Both operate only on the current run's history.

- **Tier 1 — safety truncation** (`safety_compress_observation`): every observation is truncated to a char budget (keeping the tail, where errors live) before it enters the planner history. Deterministic, no LLM, no cost.
- **Tier 2 — reflective compression** (`observation_compressor`): an LLM pass summarizes an *old* step's large observation, applied `compression_delay` steps behind the current one, only when the raw observation exceeds a char threshold and the estimated token benefit clears a threshold. Costs reflection tokens (tracked separately in the token ledger).

Both reset each run. This keeps the growing ReAct prompt inside the context window across a long trajectory.

## 7. Data flow (one step)

1. `reset_to_base()` → clean container.
2. `run_install_script(script)` → `InstallResult`.
3. `certify_all(graph, exec_readonly)` → graph node states refreshed (observability).
4. Done check: green script + `test_verdict.ok` → return DONE.
5. Else build the observation (build-failure tail *or* test-failure tail), truncated (Tier 1) and appended to history (with Tier 2 applied to older steps).
6. `planner.plan(history, script, observation)` → `thought` + action.
7. Dispatch: EXPLORE → `exec_readonly`, record obs; PATCH → replace `script`, record; malformed → format reminder.
8. Next step.

## 8. What's cut / kept / reused

**Cut from `radical`:**
- Snapshot/rollback + `_environment_revision` — dead once every step re-runs the whole script fresh (the previous script *is* the rollback point).
- The `Synthesizer` + Dockerfile generation — `setup.sh` *is* the artifact; no trajectory→recipe synthesis.
- Long-term (cross-run) memory — `_maybe_generate_long_term_memories` / `_retrieve_long_term_memory_observation`.
- Agent-claimed "Final Answer: Success + Verification Bundle" — replaced by the host-owned done gate.

**Kept from `radical`:**
- The flat ReAct `run()` skeleton (plan → act → observe).
- **Both** observation-compression tiers and the `AgentStep` + planner-managed-history structure they ride on.

**Reused from this repo (not rebuilt):**
- `Sandbox` — `reset_to_base` / `run_install_script` / `exec_readonly`.
- `render_build_script(graph)` — seeds the initial script only.
- `certify_all` / `certify_refresh` — graph update.
- `test_verdict` (80% gate) and `is_read_only` (explore guard).
- `complete_with_retry` — LLM calls.

## 9. The graph/script inversion (conscious design note)

arm C is **graph-primary**: the graph is truth, the script is a pure render, and the agent must emit typed graph patches through a gate. This system is **script-primary**: the agent edits `setup.sh` text directly, and `certify_all` runs the node checks as a certified-observability side-channel. That inversion is what buys the simplicity — free-text edits, no typed-patch gate, no rejection friction, and the LLM is strong at editing shell scripts.

Consequences, stated so they don't surprise us:
- `render_build_script(graph)` seeds the **initial** script only; after step 0 the *script text* is authoritative, not the graph.
- The graph and script may **diverge** (the agent adds an `apt-get` the graph has no node for). That's acceptable here — the script builds the env; the graph reports certified state.
- The graph does **not** drive repair or done in this version.

**Future (§12): graph-guided repair** re-inverts this to graph-primary.

## 10. Error handling

- **Build-script failure** (`rc != 0`): observation = `failing_command` + `stderr` tail → agent patches.
- **Test failure** (< 80%): observation = pytest output tail → agent patches.
- **Transient LLM error**: bounded retry via `complete_with_retry`; on exhaustion, end the run as GIVEUP with the last script.
- **Malformed agent reply** (no valid `Action:`/`Script:`): re-prompt with a one-line format reminder (as `radical` does for "No Action"), no container action that step.
- **Non-read-only explore command**: rejected with a reminder; the container is never mutated by an explore.
- **A patch that breaks the script**: surfaces as `rc != 0` on the next run; the agent sees it and fixes. The loop always retains the last script string, so there is always a best-effort artifact.
- **Never silently swallow**: every failure becomes an observation the agent sees, or a logged GIVEUP reason.

## 11. Termination & outputs

- **DONE**: green script + tests ≥ 80% → return `(script, graph, DONE)`.
- **GIVEUP**: `max_steps` hit or unrecoverable → return `(last_script, graph, GIVEUP)`.
- **Artifacts**: the final `setup.sh` (the agent's script) written to disk; the certified graph for reporting.

## 12. Open items / future

- **Graph-guided repair** (the re-inversion): the SAME loop with the planner's `graph_context` slot populated (`--arm react --graph-context`) — feed the certified graph state into the planner prompt as guidance, and/or convert accepted script patches back into typed graph nodes so the graph reflects what the script built (success→node reverse-ingest). A flag on this arm, not a new arm (§14).
- **Diff-based patching** instead of full-script emission (token efficiency), once the loop is proven.
- **Structured localization**: optionally hand the agent the single first-failing step rather than the whole log, if free-form log reading proves noisy.

## 13. Testing strategy

- **Offline mechanics** (Docker-free, mirrors arm C's eval): a `FakeSandbox` returning scripted `(rc, output)` sequences + a scripted planner, exercising: build-fails→patch→green; green-but-tests-fail→patch→pass; unfixable→GIVEUP; both compression tiers firing. Design-point logging so a later reader can verify each guarantee.
- **Unit**: `test_verdict` parsing (passed/failed/errors/skipped; the 0.8 boundary; hollow = `executed 0`); action parsing (explore vs patch; non-read-only rejection); patch application (wholesale replace); Tier-1 truncation boundary; Tier-2 benefit gate.
- **Live smoke**: small repos (`itsdangerous`, `click`) native-arm64, plus one native/native-dep repo, via the existing e2e harness + `V3_LOOP_VERBOSE`.

## 14. Arm structure, packaging & decoupling

**A new, self-contained arm.** This ships as `--arm react` (baseline) — additive, no cutover. It **replaces arm C** (`--arm session` + its `repair_*` modules), which is retired as part of this work (never used in production, superseded by this simpler loop). `run_v3` and `run_v1` are untouched.

**Graph-guidance is a flag, not an arm.** The future graph-guided variant is the SAME loop with the planner's `graph_context` slot populated: `--arm react` (empty = baseline) vs `--arm react --graph-context` (populated = graph-guided). One code path, one toggle — so the baseline↔graph-guided A/B differs *only* in the guidance signal, not the loop or the patch mechanism. Never a separate `react-graph` package.

**Package layout** — the arm owns only its strategy; everything below it is shared platform it imports:

```
src/react_repair/              # THE ARM (strategy layer, self-contained)
  loop.py        # run_react(...) — the flat ReAct loop (§2)
  planner.py     # ReAct prompt + parse; graph_context slot (off=baseline, on=graph-guided)
  history.py     # per-run step records + BOTH observation-compression tiers (§6)
  actions.py     # Action type + parse/apply explore | patch (§4)
  gate.py        # test_verdict (§5)
  entry.py       # run_react wiring + its OWN docker adapters; --arm react dispatch
```

**Decoupling boundary** — share the platform, duplicate the incidental glue:

| react imports (shared platform) | react owns, written fresh | react must NOT import |
|---|---|---|
| `Sandbox` (`reset_to_base`/`run_install_script`/`exec_readonly`), `render_build_script`, `certify_refresh`/`certify_all`, `DepGraph`, `complete_with_retry`, `is_read_only` | `entry.py` docker adapters, `history.py` logger + compression, `actions.py`, `planner.py`, `loop.py`, `gate.py` | anything from `repair_arm*.py`, `repair_fix.py`, `repair_session.py`, `session_agent.py`, `repair_arm_entry.py`, `repair_log.py` (arm C — being deleted) |

Rationale: coupling to the shared **platform** is necessary (v3 uses it too); coupling to a **sibling arm** — especially a retired one — is a landmine (you would have to keep the dead arm alive to import from it). The arm-specific glue is tiny (~30-line adapters, ~40-line logger) and react's versions genuinely differ from arm C's (react's replay wants raw `(rc, output)`, not per-node localization), so rewriting fresh is *smaller* for react, not just cleaner. This is **incidental duplication** (independent glue that never needs to change together), not **knowledge duplication** (reimplementing `certify`/the container — forbidden).

**Retirement + salvage.** Salvage arm C's *patterns* by rewriting them fresh in `react_repair/` (the render→reset→run flow, the design-point logging idea, the `FakeWorld` eval double). Then delete arm C wholesale — `repair_arm.py`, `repair_fix.py`, `repair_session.py`, `session_agent.py`, `repair_arm_entry.py`, `repair_log.py`, the `src/eval/repair_arm_eval/` package, the `--arm session` branch in `run_v3_e2e.py`, and the related tests — after verifying no other module imports them. (`repair_types.py` / `ReplayResult` stays only if something outside arm C still uses it; otherwise it goes too.)

## 15. Observability (verifying an e2e run)

Three layers, all off by default (a quiet run is byte-identical) so a live e2e can be *inspected*, not just trusted. All share one object, `ReactLog`, threaded everywhere as `log`.

1. **Design-point log** (`log.d`, `[DESIGN:*]` tags) — proves control flow (RUN/CERTIFY/PLAN/EXPLORE/PATCH/COMPRESS/TEST_GATE/DONE/GIVEUP fired). Stdout gated by `REACT_VERBOSE=1`. A run-end `log.summary()` prints per-tag counts (`PLAN×4 PATCH×2 COMPRESS×1 …`) as a one-glance health check.
2. **Structured trace** (`log.trace(phase, **fields)`) — one JSONL record per event, written to `--trace-out <path>` and always kept in memory (for tests). Per step: `run` (script len, rc, failing command, output tail), `plan` (observation, the FULL prompt messages, raw reply, parsed action), `compress` (tier, target step, raw vs summary chars, summary text), `test` (passed/executed/ok/output tail), `end` (outcome). One step's records read top-to-bottom as the agent↔system round-trip.
3. **Verbose stdout** (`REACT_VERBOSE=1`) — echoes design tags + prompt/reply/compaction previews live.

What this verifies:

| Check | Captured where | Verify |
|---|---|---|
| Prompts correct | `plan.prompt` | includes current script + observation + history; **no** `GRAPH CONTEXT` block in the baseline (present only in the graph-guided variant) |
| Compaction works | `compress` records + next `plan.prompt` | `raw_chars ≫ summary_chars`; the summarized line replaces the old one in the next prompt's history |
| Agent↔system round-trip | one step's `run`→`plan`→(`explore`/`patch`) | observation matches the run result; parsed action matches the reply; a patch is followed by a fresh run; an explore is a free turn (no new run) |
| Gate math | `test` records | `ok == (passed/executed ≥ 0.8 and executed ≥ 1)` |

The trace is a clean raw artifact for offline/subagent analysis. `ReactLog` is react-owned; `log_llm_exchange` (shared, `diagnostics.py`) may be reused for exchange dumps without an arm-C import.

## Global constraints

- **One clean loop**; minimal machinery; no counters beyond `max_steps`.
- **Host owns done** — the agent never self-certifies.
- **Build-script repair only** — no live mutation of durable container state; explore is read-only; only the whole-script re-run mutates the build.
- **Reproducible** — fresh `reset_to_base` + whole-script run each step; no snapshots.
- **Additive** — a new system; does not modify `run_v3` / `orchestrator` / arm C.
