# Gate-Ladder Stage 2 — Binding Installability via Reset-to-Base — Design

> **Extends:** `docs/superpowers/specs/2026-06-29-gate-ladder-outer-loop-design.md` (the two-gate model) and builds on the Stage 1 observability scaffold (`src/envstate/gates.py`, landed `ba7f829..d817ab4`).

**Status:** DESIGN (decided via brainstorm, 2026-06-30). Not yet implemented.

**Decided by:** user, through a Q1–Q5 brainstorm of the in-loop container mechanics.

---

## 0. Correction to the gate-ladder spec (important)

The gate-ladder spec (§6, §9, §11) repeatedly states *"Sandbox today has no commit/checkpoint support — Stage 2 must add it."* **That is wrong.** Reading `src/sandbox.py` shows `Sandbox` already:

- **auto-commits a snapshot after every successful state-changing command** (`execute()` → `_should_commit()` → `self.container.commit()`),
- keeps a **baseline snapshot** at init and tracks `last_success_image`,
- **rolls back** via `docker rm` + `docker run <last-good-image>` + ephemeral-service replay (`_restore_last_success_container` / `rollback()`),
- and the v3 loop **uses** this Sandbox (`agent.py:1366` binds `sandbox_execute=self.sandbox.execute`, `exec_readonly=self.sandbox.exec_readonly`).

So `docker commit` checkpoint/reset is **already built**. Stage 2 is therefore **not** "add docker commit." It is: (a) give the loop a *control surface* over the container (reset-to-base, reset-to-last-good, run-script-fresh — today the loop only sees two thin callables), and (b) add a **fresh-from-base full-script run** as the binding `ebsr` certification, with **localized** failures feeding the existing repair loop. (The gate-ladder spec's §6/§9/§11 wording should be corrected to match.)

---

## 1. Goal & scope

**Goal:** turn Stage 1's *provisional* installability gate into a **binding `ebsr`** check by running the compiled `setup.sh` from a clean base container each outer iteration, and feed precise, *localized* failures into the existing structured repair loop so the LLM reasons about the exact failing command in context.

**In scope (Stage 2):**
- A loop-controlled container surface: reset-to-base, reset-to-last-good, run-script-fresh.
- The reset-to-base **install execution model** (replacing the incremental `block_emit` install when the flag is on).
- **Error localization** of the failing command within its annotated block + stderr.
- An **enriched repair input** (debug bundle) that adds the localized runtime failure and a bounded script window to the existing graph slice.
- A **pip/apt cache volume** so reset-to-base re-runs stay affordable.

**Out of scope (deferred to Stage 3):** the `done`-condition wiring (`next_decision` reading testability gate state), the `pytest --collect-only` probe, and `classify_gate_failure`→typed-obligations. Stage 2 changes the *install mechanism + repair input*, not termination logic.

---

## 2. The reset model (decided: nested R2/R3)

There are three reset granularities; the Sandbox already gives two for free:

| Option | What | Cost | Honesty |
|---|---|---|---|
| R1 no reset | repair attempts run on the live, possibly-dirty container | cheapest | a failed attempt's junk can fool the next attempt |
| **R2 reset to last-good snapshot** | `docker rm` + run `last_success_image` — keeps prior good installs, drops the failed attempt | cheap (no full re-run) | clean relative to prior good state |
| **R3 reset to base** | re-run the whole script from `base_image` | expensive (full re-run) | every run is a binding `ebsr` |

**Decision:**
- **Inner repair loop → R2 (fast candidate search).** Before each repair attempt, restore the last-good snapshot **preceding the earliest block the patch changed** (§3 resume-point rule), then run forward from there to retry the failing block. Fast, self-contamination-free. The per-command auto-commit is what provides the last-good snapshots R2 restores to — so auto-commit is *used*, just not as the binding mechanism.
- **Outer loop → R3 (binding verify).** Once the inner loop has a candidate fix, reset to base and run the *whole* re-rendered script. That run's rc is the **binding installability gate**. R2 only proposes; R3 certifies.

**Why not R3 per repair attempt:** it is redundant with the outer binding run (the honesty arbiter), and it is the slowest path × the most frequent event. Repair attempts only need contamination-freedom, which R2 delivers cheaply. The outer R3 run is the backstop that catches any incremental-only success — same division of labor that justified reset-to-base in the first place, applied at the right granularity.

---

## 3. The loop (data flow)

```
outer iteration (flag enable_binding_install on):
  render setup.sh from graph
  reset_to_base()                       # fresh container from base_image
  run full script, block-by-block       # auto-commit snapshots last-good after each OK block
    └─ first block fails →
         assemble debug bundle (§5):
           · localized runtime failure (failing command highlighted in its #@action block + stderr)
           · scoped RepairScope slice  (providers / tried_failed / dep states / gate / evidence)
           · bounded script window
         INNER repair loop (run_structured_repair, R2) — fast CANDIDATE search:
           LLM → PatchProposal → PatchGate → re-render
           reset to the last-good snapshot PRECEDING the earliest block the patch touched
             (drop the failed attempt; keep only blocks proven good before that point;
              fall back to base if the patch touches the first block)
           re-run from that point; retry the failing block   (bounded by MAX_REPAIRS_PER_BLOCK etc.)
       once the failing block passes → graph has a candidate fix
  reset_to_base(); run the WHOLE re-rendered script   # R3 — the BINDING verify
    └─ fails at a LATER block → localize → repair again
    └─ rc 0 → installability BINDING gate SATISFIED
  run pytest on the just-built clean container → testability   (done-path unchanged; Stage 3 re-wires it)
```

- **Two-tier honesty:** the inner R2 loop is a *fast candidate search* — it finds a patch that makes the current block pass without re-running the whole script. The **binding** installability is **always a from-base (R3) full pass** of the re-rendered script; R2 only proposes, R3 certifies. This is why R2 imperfection is safe (see §9): even if an R2 resume is fooled, the R3 run re-executes the whole script from clean and catches it.
- **R2 resume-point rule:** a patch may insert a block *earlier* in topo order (e.g. a system lib before the pip package). R2 must therefore reset to the last-good snapshot **preceding the earliest block the patch changed**, not blindly to "before block K" — otherwise the resumed run wouldn't include the newly-inserted earlier block. Fall back to base when the patch touches the first block.
- **Per-issue verification** = the R3 run advances past the previously-failing block. **Loop-level exit** = an R3 from-base full pass at rc 0 (binding installability). A fix that resolves error #1 but exposes error #2 keeps the loop going.
- Testability runs on the freshly-built, clean-from-base container; its termination logic is unchanged in Stage 2.

---

## 4. Error localization (decided: command-in-block + bounded window)

The rendered `setup.sh` is already block-structured and node-annotated (`render_setup_sh` emits `#@action id=… wave=…` + `#@targets <node_ids>` + `#@check <cmd>`, preamble `set -Eeuo pipefail`, and round-trips via `parse_setup_sh`). Localization is therefore *line → block → graph node*:

- Run the parsed blocks (block-by-block) against the fresh container; the first block whose command exits non-zero is the failure. Its `target_node_ids` is the offending graph node(s); the command output is the raw error.
- Hand the LLM **both** the exact failing command **and** its enclosing block:

```
#@action id=blk7 wave=pip   #@targets pkg:psycopg2==2.9.9
  pip install --no-deps psycopg2==2.9.9      ← FAILED (rc=1)
--- stderr ---
Error: pg_config executable not found. ...
```

- The failing command comes from the block-runner (it knows which command tripped) or an `ERR` trap's `$BASH_COMMAND`; the block boundary comes from the annotations.
- **Bound the context:** include the failing block + a small window of neighbors (or the script's block outline) — not the entire file — so context stays bounded on large repos. (Command-level precision *inside* block context; no either/or.)

---

## 5. The repair debug bundle (input enrichment; output unchanged)

Each repair attempt the LLM sees a **three-part bundle**:

| Part | Source | Tells the LLM | Status |
|---|---|---|---|
| Localized runtime failure | container (this run) | *what* broke, *how* (exact cmd + block + stderr) | **NEW** |
| Scoped `RepairScope` slice | the graph | *context*: providers, tried_failed, dep states, unblocks, cohort, gate, platform, evidence | exists |
| Bounded script window | rendered setup.sh | ordering / neighbors around the failure | **NEW** |

- The graph slice stays **scoped to the failing node's neighborhood** (current Slice B behavior — no whole-graph summary).
- It is **self-updating**: each failed attempt records to the graph (ledger attempts → `tried_failed`; `runtime_classify` → evidence), so the next slice shows "you already tried X and it failed with Y" — the anti-repeat signal.
- **Output is unchanged:** LLM → typed `PatchProposal` → deterministic `PatchGate` admission → host re-renders `setup.sh`. Stage 2 only enriches the *input*. Single authority preserved: the host certifies via the from-base rc; the LLM cannot declare success.

---

## 6. The seam (decided: extra optional callables, Q5=A)

`run_v3` today sees only `sandbox_execute(cmd)->(bool,str)` and `exec_readonly(cmd)->(int,str)`. Stage 2 adds **optional keyword callables** (default `None` ⇒ Stage-1 behavior), matching the existing thin-callable idiom (trivially faked in tests, backward-compatible, flag-gated):

- `reset_to_base: Callable[[], None] | None`
- `reset_to_last_good: Callable[[], None] | None`
- `run_script_fresh: Callable[[str], BlockRunResult] | None` — runs the rendered script's blocks in the fresh container and returns the first failing block + command + stderr (or success).
- a flag `enable_binding_install: bool = False`.

Drivers (`agent.py`, `scripts/l2_repair_loop_smoke.py`) bind the `Sandbox` methods. Rejected alternatives: a single `ContainerController` protocol object (churns the two-callable seam and every test/driver), and passing the concrete `Sandbox` (couples `run_v3` to the class, breaks the fake-callable test seam).

---

## 7. Components & files

| Component | Change | Effort |
|---|---|---|
| `src/sandbox.py` | `reset_to_base()` (`docker rm` + run from `base_image`); `run_script_fresh`/fresh block-runner returning first-failing-block + command + stderr; `reset_to_last_good()` ≈ existing `rollback()` (expose cleanly) | M |
| `src/sandbox.py` | mount a persistent pip/apt **cache volume** (so reset-to-base re-runs hit cache) | S |
| `src/envstate/` (new small module) | localization/debug-bundle assembly (command-in-block + stderr + bounded window) | S |
| `src/envstate/repair_loop.py` (`run_structured_repair`) | call `reset_to_last_good` between attempts; attach the localized runtime failure to the obligation packet | M |
| `src/envstate/orchestrator.py` (`run_v3` + `_dep_emit_phase`) | new optional callables + `enable_binding_install` flag; when on, replace incremental `block_emit` install with reset-to-base full-script run + localized repair; installability gate binds from that run's rc | M–L |
| drivers (`agent.py`, `l2` smoke) | bind the new `Sandbox` methods into `run_v3` | S |
| test suite | reset-to-base path, localization, debug-bundle, byte-identical-off, R2/R3 nesting | L |

---

## 8. Safety & byte-identical guarantee

- `enable_binding_install` defaults **False** ⇒ behavior byte-identical to Stage 1 (incremental `block_emit` path untouched; the new callables are `None` on the off-path so nothing in the loop changes).
- `run_v1` and the B3 ablation (`enable_script_materialization=False`) untouched.
- Anti-hollow preserved: installability state is written only by the host (the from-base run's rc); the LLM still only proposes typed patches.

---

## 9. Risks & open questions

- **Reset-to-base performance** — the full re-run per outer iteration is the main perf risk. Mitigation: the pip/apt cache volume turns re-runs into mostly cached no-ops up to the failing line. **Measure this as the Stage 2 benchmark arm** (turns/wall-clock vs the incremental arm); fall back to a hybrid (incremental search + a single final from-base binding run) only if measured cost is prohibitive.
- **Block-runner granularity** — block-level node attribution is usually precise (a block ≈ one node's install); command-level highlighting via `ERR`/`$BASH_COMMAND` is included for multi-command blocks. Confirm the block-runner reliably reports *which* command tripped.
- **R2 resume-point correctness** — the inner loop must reset to the last-good snapshot *preceding the earliest block the patch changed* (a patch can insert an earlier block, e.g. a system lib before a pip package), falling back to base when the patch touches the first block. This needs per-block snapshots (the auto-commit cadence roughly provides them — verify it commits after each successful block) and knowing the patch's earliest touched topo position. **Even if R2 resumes from the wrong point, the R3 from-base binding run is the backstop** — so R2 is a heuristic accelerator, not a correctness dependency.
- **Cache volume isolation** — a shared cache across repos must not leak a wrong wheel/version; key the cache appropriately (or accept index-level caching only).
- **Stage boundary** — testability runs on the clean container in Stage 2, but its `done`-wiring stays Stage 3; do not let the binding-install change accidentally alter `next_decision`.

---

## 10. Research framing

Stage 2 delivers the **honest half of the installability gate**: `ebsr` certified by reproducing the env from a clean base via the compiled artifact, not by a proxy on a dirty container. Combined with Stage 1's testability gate, the two gates are now both **host-certified from clean** — strengthening the authority-model claim (hollow success architecturally impossible: the LLM proposes typed patches; the host certifies installability by from-base reproduction and testability by a real anti-hollow pytest run). The reset-to-base loop is the *mechanism* by which localized failures become typed graph obligations.

---

## 11. Summary

Make installability **binding** by resetting to base and running the compiled `setup.sh` from clean each outer iteration; localize the first failing **command within its annotated block** and feed it — alongside the existing scoped `RepairScope` slice and a bounded script window — into the unchanged typed-patch repair loop. Use the **nested reset model**: inner repair attempts restore the **last-good snapshot** (R2, cheap), the outer loop resets to **base** (R3, binding). Expose the container controls as **optional callables** on `run_v3` (default off ⇒ byte-identical), reusing the `Sandbox`'s already-existing commit/rollback machinery. Keep a pip/apt **cache volume** to make reset-to-base affordable, and **measure** it against the incremental arm. The `done`-wiring, collect-only probe, and failure classifier remain Stage 3.
