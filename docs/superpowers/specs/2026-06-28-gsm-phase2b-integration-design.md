# GSM Phase 2b — `run_v3` Integration (Design)

**Status:** approved 2026-06-28 (brainstorming). Sub-spec of the master design
`docs/superpowers/specs/2026-06-28-graph-governed-script-materialized-agent-design.md`
(§9 Build/Lab Agent, §10 PatchGate, §15 Roadmap, §16 Invariants, §18 Decisions) and the
companion `docs/superpowers/specs/2026-06-28-gsm-phase2a-patchgate-design.md`.

**One line.** Make `run_v3` *be* the graph-governed, script-materialized, typed-patch agent:
the deterministic emittable wave runs through `compile_blocks → run_blocks → certify_refresh`,
the BuildAgent emits typed `PatchProposal`s (not free-text shell), PatchGate admits them, the
compiled `setup.sh` becomes the canonical Dockerfile spine, and a config/service classifier
adds soft hints — while v1 is untouched.

## 1. Context and why this exists

Phase 1 + 2a built the machinery as standalone, unit-green modules; **nothing is wired into a
run loop.** The live `run_v3` today is "graph-scheduled but free-text ReAct": the dep-graph
schedules (`graph_scheduler.next_decision`, `orchestrator.py:574`) and the host certifies
(`certify_refresh`), but the BuildAgent's action is still a raw shell command parsed from
free text (`build_agent.py:97-103,162,207`). That **violates invariant #6** (LLM output
accepted only as a structured `PatchProposal`). The PatchGate (`patch_gate.py`) exists but has
zero non-test importers. 2b closes that gap.

2b realizes the §17 contribution and satisfies 8 of the 11 §16 invariants (the table in §4);
maturity gates (#5, Phase 3), lab containers (#7, Phase 5) and the fresh-replay *runner* (#9,
Phase 6) remain later phases.

### Decomposition (this spec → three plans)

Per the 2026-06-28 decision, 2b is **A → B → C, in place, L1-validate A first**. Each slice is
its own implementation plan and produces working, testable software:

- **Slice A — deterministic engine swap + artifact** (no LLM): `run_v3` drives blocks instead
  of `emit_drain`; the compiled `setup.sh` becomes the Dockerfile spine. This *is* the §14 B3/B5
  toggle state. Validated by an L1 real-container run before being relied on.
- **Slice B — structured repair loop**: BuildAgent emits typed `PatchProposal` via ReAct-diagnose
  → fenced JSON; the bounded repair loop drives `validate → apply → compose_script → re-run`.
- **Slice C — config/service classifier + soft edges**: an LLM classifier turns static hits into
  soft-hint proposals; the `_is_actionable` carve-out is generalized into the soft/hard rule.

## 2. Scope

**In scope (2b):** the three slices above + the carried 2a-review hardening MUSTs (§8) + a v3
re-baseline after each slice.

**Out of scope (later phases):** maturity-gate model (Phase 3); platform profiles + Platform
node (Phase 4); causal overlay (`caused_by`/`invalidated_by` edges) + lab containers (Phase 5);
fresh-replay runner + baseline-arm harness + metrics (Phase 6). 2b's reasoning enrichment is the
curated RepairScope packet + "known-invalid providers" memory — **not** causal graph edges.

**v1 is invariant.** Every change is behind the v3 arm. `emit_drain` (`depgraph_live.py:89`),
`synthesis.build_commands_from_ledger` (`synthesis.py:149`), and `BuildAgent.run`
(`build_agent.py:551`) stay byte-identical for v1.

## 3. Resolved decisions

1. **Sequencing:** A → B → C, in place, L1-validate A first. (user)
2. **Artifact strategy:** the compiled `setup.sh` replaces the *install-replay spine*
   (`build_commands_from_ledger`) for v3; the three state-captures (pinned pip closure
   `build_pin_instructions`, config-env bake, file-content capture) are **retained and appended**
   as trailing Dockerfile layers — they read achieved container state, not ledger command-replay.
   (user)
3. **Evidence histories:** the block-emit phase **dual-writes a minimal `ActionLedger`** alongside
   the typed `EvidenceBundle`, so the retained captures keep working pre-classifier. The ledger is
   **demoted** from "install-replay source" to "state-capture feed"; `EvidenceBundle` is the typed
   graph truth. (user, follows from #2)
4. **Structured output:** ReAct loop bounded to **read-only** diagnostics, terminating in a single
   fenced `PatchProposal` JSON consumed by `parse_patch_proposal` (try/except → one structured
   retry → reject). Preserves §9 exploration, model-agnostic, reuses the ReAct executor. (user)
5. **`emit_drain` fork (design recommendation):** there is no fork — v3's `_dep_emit_phase`
   (`orchestrator.py:397`) simply calls the new block-emit phase instead of `emit_drain`; `emit_drain`
   stays untouched for v1.
6. **Toggle (design recommendation):** `enable_script_materialization` (default **on** = B5).
   Off = B3 ablation → v3 reverts to `emit_drain` + ledger-replay (the pre-2b v3 path). One flag,
   clean A/B arm; default flips the product to the new path (§18 #1/#2).

## 4. Invariants 2b satisfies (§16)

| Invariant | After 2b |
|---|---|
| #1 graph authority / #2 script is a projection | ✅ Slice A artifact switch + recompile-after-mutation |
| #3/#4 only host checks write SATISFIED | ✅ kept (`certify_refresh`) |
| #6 LLM output only as typed PatchProposal | ✅ **newly satisfied** (Slice B) — the currently-violated one |
| #8 evidence-cited / #10 soft edges / #11 state enum | ✅ 2a + Slice C |
| #5 maturity gate as behavioral evidence | ⏳ Phase 3 (2b keeps `_verified_test_run_passed` as the only binding gate) |
| #7 lab success never mutates canonical | ⏳ Phase 5 |
| #9 fresh-replay proof | ◑ artifact is replayable (A); the replay *runner* is Phase 6 |

## 5. Slice A — deterministic engine swap + artifact

The risky in-place cut, but **fully deterministic — no LLM**. After A, v3 provisions the
emittable wave from the graph and emits a graph-compiled Dockerfile.

### 5.1 The block-emit phase (replaces `emit_drain` on v3)

A new orchestrator helper `_block_emit_phase(graph, sandbox_execute, exec_readonly, cycle)`
replaces the `emit_drain(...)` call inside v3's `_dep_emit_phase` (`orchestrator.py:397`):

```text
blocks = compose_script(graph, manual_blocks)        # manual_blocks = () in Slice A
graph, bundle, failed = run_blocks(blocks, wrapped_sandbox, exec_readonly, graph, cycle)
return graph, bundle, failed
```

- `run_blocks` (Phase 1) executes each block, stops on the first failure, logs one `Evidence`
  per command, and certifies via `certify_refresh` (block rc=0 never certifies — #3/#4).
- **`run_blocks` stays pure/unchanged.** The dual-write lives in `wrapped_sandbox`: a thin
  orchestrator wrapper around the existing `sandbox_execute` that, on **each** block command
  (success *or* failure), appends a minimal `ActionEvent` to the live `ActionLedger` (the
  state-capture feed, decision #3). Failures (`rc != 0`) are mirrored too because
  `_runtime_ingest_phase` (`orchestrator.py:441`) reads `ledger.events()` filtered to `rc != 0`
  to discover new nodes — the pre-2b `emit_drain` fed it the same way, so the block path must
  preserve that to keep the discovery loop alive.
- With no LLM in A, a `failed` block ends the wave; the loop proceeds to the existing
  certify/done-gate logic. (Repair on failure arrives in Slice B.)

### 5.2 Artifact switch (the v3 finalizer branch)

`_finalize_supervisor_artifacts` (`agent.py:1638`) is arm-agnostic today. Add a v3 branch:

```text
if v3 and enable_script_materialization:
    blocks = compile_replay_blocks(final_graph)              # STATE-INDEPENDENT (see note below)
    build_commands = [c for b in blocks for c in b.commands] # per-block apt + pinned-direct pip
    persist render_setup_sh(blocks) as setup.sh             # audit/replay artifact
    apply_build_recipe({build_commands, source="compiled_setup_sh"}); return True
    # THEN _finalize_supervisor_artifacts appends the retained captures (unchanged), AFTER this returns:
    #   _emit_closure_recipe()                # build_pin_instructions — closure pin
    #   _bake_test_env_vars()                 # config ENV bake
    #   _emit_interleaved_state_recipe()      # file-content capture
else:
    <existing build_commands_from_ledger path>               # v1 + B3 ablation
```

**Why `compile_replay_blocks`, not `compose_script`:** `compose_script`/`compile_blocks` emit
ONLY the emittable wave (`partition().emittable` = `State.MISSING` nodes). At finalize time every
node is `SATISFIED`, so they yield an empty spine. The artifact needs a *replay* projection — one
block per installable (`_is_reciped`) node in topo order, **regardless of state** — which
`compile_replay_blocks` (a small state-independent sibling of `compile_blocks` reusing
`_is_reciped`/`topo_order`/`_block_for`) provides. The rendered `setup.sh` is persisted as the
audit/replay artifact; the Dockerfile RUN spine is the per-block command list (functionally a
`RUN bash setup.sh`, but kept as commands so it composes with the retained-capture command layers).

`synthesis.build_commands_from_ledger` is bypassed for v3; everything else in the finalizer
(`build_pin_instructions` `synthesis.py:224`, config-bake, file-capture
`agent.py:1656-1659`) is reused. The Dockerfile layer order is: base → `setup.sh` (system+pip
spine) → pinned closure → config ENV → captured files → project install.

### 5.3 Toggle + dead-branch removal

- `enable_script_materialization` added to the agent constructor (default `True`); set in the
  flag cascade near `enable_graph_scheduler` (`agent.py:333-345`). Off → `_dep_emit_phase` keeps
  calling `emit_drain` and the finalizer keeps the ledger-replay path (exact pre-2b v3 = B3 arm).
- Delete the dead `apply_recipe_patch` branch in v3 (`orchestrator.py:603-639`) — unreachable
  (`next_decision` only returns `task`/`done`) and its only remaining purpose was `RecipePatch`,
  which v3 no longer produces. v1's recipe branch (`orchestrator.py:243-277`) stays.

### 5.4 L1 validation gate (before relying on A)

A standalone driver (`scripts/` or a committed integration test) runs, against a **real
container** with no LLM: `build_dep_graph(repo) → compose_script → run_blocks → certify_refresh`
on 1–2 known repos; assert the `setup.sh` actually provisions and the target nodes certify.
The existing Docker exec adapter already matches the `run_blocks` callable contract
(`sandbox_execute: (cmd)->(ok,out)`, `exec_readonly: (cmd)->(rc,out)`), so this reuses exec
plumbing. Promote the driver to a committed seam integration test.

## 6. Slice B — structured repair loop

Where the LLM re-enters — now as a typed patch proposer (invariant #6).

### 6.1 RepairScope packet builder (new module)

`build_repair_scope(graph, failed_block, bundle, known_invalid, constraints) -> dict` — the §9
prompt context: the failed command + `Evidence` excerpt; the relevant graph slice
(satisfied/missing/unknown around the failure + frontier); known-invalid providers (cross-attempt
memory); platform/package-manager constraints; the allowed patch schema. **Curated, not raw
history** (no full logs, no full chat).

### 6.2 BuildAgent v3 mode (structured output)

A new BuildAgent path for v3 (v1's free-text `BuildAgent.run` `build_agent.py:551` untouched):
- ReAct loop allowing **read-only** diagnostic commands (poke `pkg-config`, `ldconfig`, `pip show`);
- terminates in a single fenced `PatchProposal` JSON → `parse_patch_proposal`, wrapped in
  try/except: malformed → one structured retry → reject;
- returns the typed `PatchProposal` (replacing the `Action:`/`Final Answer:` free-text parse).

### 6.3 The repair loop in `run_v3`

On `_block_emit_phase` returning `failed != None`, enter the bounded loop
(`max_repairs_per_failed_command ≈ 5`):

```text
while cycles_remain and original block still failing:
    scope    = build_repair_scope(graph, failed_block, bundle, known_invalid, constraints)
    proposal = build_agent_v3_propose(scope)                      # ReAct read-only -> fenced JSON
    errs     = validate_proposal(graph, proposal, known_evidence_ids=bundle_ids)
    if errs:  re-prompt once with errs; else:
        result = apply_proposal(graph, proposal)                  # graph canonical; never SATISFIED
        graph, manual = result.graph, manual + result.blocks
        blocks = compose_script(graph, manual)                    # recompile-after-mutation
        graph, bundle, failed = run_blocks(blocks, ...)           # re-run (installs idempotent); originally-failed block now passes => advance
    record rejected/failed providers into known_invalid          # cross-attempt memory
exhaustion -> structured partial result (satisfied nodes, remaining failing gate, evidence, attempts)
```

The scheduler `task` branch (`build_agent.run` at `orchestrator.py:647`) routes through this
structured path. `_verified_test_run_passed` stays the binding done-gate (§18 #3). Re-running
all blocks each cycle is correct because installs are idempotent; resume-from-failed-block is an
optional optimization a plan may add, not a correctness requirement.

### 6.4 Carried 2a-review MUSTs land here

These were latent in 2a (nothing executed); Slice B makes them load-bearing because
`check_commands` now run via `certify_refresh`:
- **Harden the `_MUTATING` read-only guard** (`patch_gate.py`) → denylist-to-allowlist (or widen
  + add `>/dev/null` redirect nuance) **before** check_commands are host-executed.
- **Provider/promotion upgrade semantics:** the loop re-proposes, so `apply_proposal`'s
  first-writer-wins `chosen_fix` + re-proposal no-op must allow an explicit **upgrade/override**
  (else a corrected provider is silently dropped).
- **Require non-empty `ScriptPatch.target_node_ids`**; **validate `ProviderSpec.provides`** ids
  resolve; **widen `ACTION_CLASSES`** (`pip3`, `apt` frontend).
- **`parse_patch_proposal` robustness:** wrap missing-key KeyErrors into a structured rejection.

## 7. Slice C — config/service classifier + soft edges

- **LLM classifier (new module):** consumes `static_collect`'s compact bundle
  (`static_collect.py`, Phase 1) → soft-hint `PatchProposal`s (Config/Service nodes,
  `promotion="hint"/"candidate"`, soft edges `hard=False`). This is the §5.2.1 detector reframe:
  the pure detectors stay; their graph-mutating wrappers route signal through deterministic
  evidence → classifier → soft hints, promoted to hard only on runtime/gate failure.
- **Generalize `_is_actionable`** (`schedule.py:28-53`): retire the hard-coded CONFIG/SERVICE
  carve-out into the single soft/hard rule built on the 2a `Edge.data["hard"]` seam.
- Fold in the Phase-1-review §5.2-bundle fidelity fixes (CI `source` key → exact workflow
  filename; `scan_env_reads` snippet → `snippet` field, clean path → `file`; add the untested
  `env_read` collector-branch test).

## 8. Cross-cutting

- **Done-gate:** `_verified_test_run_passed` (`maintainer.py:192`) stays binding; the v3 done
  path (`deterministic_maintainer._v3_done_gate`, `orchestrator.py:676-677`) is unchanged.
- **Re-baseline v3** after each slice; report honestly (`ebsr AND pass_rate ≥ 0.8`); never trust
  lenient runner status.
- **Error handling:** stop-on-first-failed-block → repair (B) or honest giveup (A); bounded loop
  → structured partial; gate rejection → re-prompt once → skip; malformed JSON → one retry → reject;
  no block exit-code ever certifies.

## 9. Testing

- **Unit (TDD):** the RepairScope builder, the v3 block-emit phase + ledger dual-write, the v3
  finalizer branch, the BuildAgent v3 propose path (with a fake LLM returning canned JSON), the
  classifier (fake LLM), and each carried-MUST hardening.
- **L1 driver:** real-container deterministic e2e (Slice A gate).
- **L2 driver:** real-container + real-LLM repair-loop e2e on 1–2 repos (Slice B gate).
- **Regression:** full suite green except the 4 known pre-existing failures; **v1 path proven
  unchanged** (`emit_drain`/`synthesis`/`BuildAgent.run` untouched for v1); v3 re-baseline per slice.

## 10. File structure / integration points (file:line)

```text
src/envstate/orchestrator.py        run_v3 (:317): _dep_emit_phase (:397) -> _block_emit_phase;
                                    add the repair loop around the task branch (:647);
                                    delete dead apply_recipe_patch branch (:603-639)
src/envstate/<new> block_emit.py    _block_emit_phase + the ledger-dual-write sandbox wrapper
src/envstate/<new> repair_scope.py  build_repair_scope (the §9 packet)
src/envstate/build_agent.py         add v3 structured-propose path (v1 run/run_recipe untouched)
src/envstate/agent.py               finalizer v3 branch (:1638); enable_script_materialization flag (:333-345)
src/python_deps/depgraph/patch_gate.py   carried MUSTs (read-only allowlist, upgrade semantics,
                                          provides validation, non-empty targets)
src/python_deps/depgraph/action_class.py widen ACTION_CLASSES (pip3/apt)
src/python_deps/depgraph/patch.py        parse robustness (KeyError -> structured reject)
src/python_deps/depgraph/<new> classify_config_service.py   the Slice-C classifier
src/python_deps/depgraph/schedule.py     generalize _is_actionable (:28-53)
src/python_deps/depgraph/static_collect.py  §5.2-bundle fidelity fixes
scripts/ or tests/  L1 + L2 drivers
```

Reuse (do not reimplement): `compile_blocks`/`render_setup_sh`/`parse_setup_sh`/`run_blocks`/
`certify_refresh`/`compose_script`/`validate_proposal`/`apply_proposal`/`parse_patch_proposal`/
`matches_action_class`/`static_collect`/`build_pin_instructions`/`bakeable_config_env`/the existing
Docker exec adapter.

## 11. Risks

1. **Two evidence stores coexist (ActionLedger + EvidenceBundle).** Mitigated by clear roles:
   ledger = state-capture feed; EvidenceBundle = typed truth; only `setup.sh` replays installs.
   Slice C migrates config off ledger scraping onto graph Config nodes.
2. **The in-place run_v3 churn breaks the v3 baseline.** Mitigated by the toggle (B3 = old path),
   per-slice re-baseline, and the L1 gate before relying on A.
3. **Structured output reliability** (LLM emits malformed JSON). Mitigated by the try/except +
   one retry + reject, and the bounded loop.
4. **Read-only guard leakiness becomes load-bearing in B.** Mitigated by the §8 hardening MUST
   landing in Slice B before checks execute.
5. **Provider upgrade semantics:** first-writer-wins could pin a wrong provider. Mitigated by the
   explicit upgrade/override decision in §6.4.

## 12. Decisions log

- 2b = A → B → C, in place, L1-validate A first (user, 2026-06-28).
- Artifact = install-spine + retained captures; evidence = dual-write demoted ledger + EvidenceBundle (user).
- Structured output = ReAct-diagnose → fenced PatchProposal JSON (user).
- `emit_drain` not forked — v3 calls the block-emit phase; `emit_drain` stays for v1 (recommendation).
- Toggle = `enable_script_materialization` (default on; off = B3 ablation) (recommendation).
- Done-gate stays `_verified_test_run_passed`; gates advisory until Phase 3 (§18 #3).
- Causal overlay + lab + maturity-gate model + platform + eval-harness are Phases 3–6, NOT 2b.
