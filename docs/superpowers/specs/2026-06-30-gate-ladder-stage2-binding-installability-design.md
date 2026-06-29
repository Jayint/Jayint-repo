# Gate-Ladder Stage 2 — Binding Installability via Reset-to-Base — Design

> **Extends:** `docs/superpowers/specs/2026-06-29-gate-ladder-outer-loop-design.md` (the two-gate model); builds on the Stage 1 observability scaffold (`src/envstate/gates.py`, landed `ba7f829..d817ab4`).
> **Wires:** `docs/superpowers/HANDOFF-graph-to-build-script-renderer.md` — the `render_build_script` renderer (built, container-validated, deliberately left inert). Stage 2 is the "first non-additive step" that handoff names.

**Status:** DESIGN (decided via brainstorm, 2026-06-30). Not yet implemented.

**Decided by:** user, through a Q1–Q5 brainstorm of the in-loop container mechanics + the decision to wire the graph→build-script renderer.

---

## 0. Correction to the gate-ladder spec (important)

The gate-ladder spec (§6, §9, §11) repeatedly states *"Sandbox today has no commit/checkpoint support — Stage 2 must add it."* **That is wrong.** `src/sandbox.py` already:

- **auto-commits a snapshot after every successful state-changing command** (`execute()` → `_should_commit()` → `self.container.commit()`),
- keeps a **baseline snapshot** at init and tracks `last_success_image`,
- **rolls back** via `docker rm` + `docker run <last-good-image>` + ephemeral-service replay (`_restore_last_success_container` / `rollback()`),
- and the v3 loop **uses** this Sandbox (`agent.py:1366` binds `sandbox_execute=self.sandbox.execute`, `exec_readonly=self.sandbox.exec_readonly`).

So `docker commit` checkpoint/reset is **already built**. Stage 2 is therefore: (a) give the loop a *control surface* over the container (reset-to-base, reset-to-last-good, run-install-script — today the loop sees only two thin callables), (b) a **fresh-from-base install + host certify** as the binding `ebsr` gate, and (c) **localized** failures feeding the existing repair loop. (The gate-ladder spec's §6/§9/§11 wording should be corrected to match.)

---

## 1. Goal & scope

**Goal:** turn Stage 1's *provisional* installability gate into a **binding `ebsr`** check by compiling the graph into a whole `setup.sh` (`render_build_script`), running it from a clean base container, **and host-certifying every node** — then feeding precise, *localized* failures into the existing structured repair loop.

**In scope (Stage 2):**
- Wire `render_build_script` as the install-subroutine renderer.
- The **two-phase install/certify** model (§1.5) as the binding installability gate.
- A loop-controlled container surface: reset-to-base, reset-to-last-good, run-install-script.
- The reset-to-base execution model (replacing the incremental `block_emit` install when the flag is on).
- **Error localization** on two failure modes (install line rc≠0; install-rc-0-but-node-certifies-MISSING), mapped via `#@node` annotations.
- An enriched repair input (debug bundle) adding the localized runtime failure + bounded script window to the existing graph slice.
- A **pip/apt cache volume** so reset-to-base re-runs stay affordable.

**Out of scope (deferred to Stage 3):** the `done`-condition wiring (`next_decision` reading testability gate state), the `pytest --collect-only` probe, and `classify_gate_failure`→typed-obligations. Stage 2 changes the *install mechanism + repair input*, not termination logic.

---

## 1.5 Reuse: `render_build_script` + the two-phase install/certify model

A concurrent agent built **exactly the renderer Stage 2 needs** and validated its execution model in real containers (handoff: `docs/superpowers/HANDOFF-graph-to-build-script-renderer.md`). Stage 2 **wires it**; it does not reinvent it.

### The renderer
`render_build_script(graph, manual_blocks) -> str` (`src/python_deps/depgraph/build_script.py`, pure / byte-reproducible / never writes `node.state`) compiles the certified graph into ONE **install-only** `setup.sh`: hard `Layer`-tier sections, intra-tier `topo_order`, `--no-deps` pinned pip (full pinned closure → no resolver drift), one hoisted `apt-get update`. Per-line authority annotations:

- `#@node` — host-compiled, **has a real executable install line** (reciped `PACKAGE` w/ version, or `SYSTEM_LIB`/`TOOL` w/ `apt:` `chosen_fix`).
- `#@need` — **comment-only** stub (no executable line) for `CONFIG`/`SERVICE`/`DATA_ASSET`; the LLM satisfies these via a governed `#@block`.
- `#@block` — governed LLM patch (from `manual_blocks`), wave-grouped; commands ARE executable.
- `#@check` — the node's check command, emitted as a **comment, NOT executed** (the script is install-only; certification is a separate pass).

It **coexists with** `render_setup_sh`/`parse_setup_sh` (untouched) and reuses `emit`/`advise`/`schema`/`block` by import only.

### The two-phase install/certify model (the anti-hollow heart)
Because `#@check` is *not* executed by the script, the binding gate is **two phases**:

1. **install** — `bash setup.sh` from a clean base; `set -Eeuo pipefail` aborts at the first failing install line.
2. **certify** — host runs each `#@node`'s `#@check` read-only (`certify_refresh` / `exec_readonly`) → flips `State` PRESENT/MISSING.

**`bash rc=0 ≠ certified`.** This separation is *proven necessary*: in the cv2 e2e, install returned **rc 0 but `syslib:libglib2.0-0` certified MISSING** — a real **hollow success caught** (Debian-13 t64 rename: `apt-get install libglib2.0-0` installs `libglib2.0-0t64`, so `dpkg -s libglib2.0-0` returns rc 1). Therefore:

> **Binding installability = install rc 0 AND every reciped `#@node` certifies PRESENT** — not `bash rc 0` alone.

This **replaces** the earlier draft's `render_setup_sh` + "full script rc 0 → SATISFIED": Stage 2's render is `render_build_script`, and the gate is the two-phase install+certify.

---

## 2. The reset model (decided: nested R2/R3, "bash the whole script")

Three reset granularities; the Sandbox already gives two for free:

| Option | What | Cost | Honesty |
|---|---|---|---|
| R1 no reset | repair attempts run on the live, possibly-dirty container | cheapest | a failed attempt's junk can fool the next attempt |
| **R2 reset to last-good snapshot** | `docker rm` + run `last_success_image`, then bash the whole script | cheap (present lines no-op) | clean relative to prior good state |
| **R3 reset to base** | bash the whole script from `base_image` | full re-run | every run is a binding `ebsr` |

Because the model is `bash setup.sh` (not block-stepped `run_blocks`), R2 and R3 are the **same action from a different snapshot** — and both are followed by the certify pass:

- **Inner repair loop → R2 (fast candidate search).** Reset to the last-good snapshot, then **bash the whole re-rendered script**: present lines are no-ops, and any patch-inserted *earlier* line or version change simply applies (pip/apt install what's missing/changed). This **dissolves the resume-point problem** — bashing the full script naturally handles inserts and modifications — so there is no fiddly "resume from block K-1" rule. Then certify. Fast because most is pre-installed.
- **Outer loop → R3 (binding verify).** Once the inner loop has a candidate fix, reset to base, bash the whole re-rendered script, and certify. That two-phase result is the **binding installability gate**. R2 only proposes; R3 certifies.

Why not R3 per repair attempt: it is redundant with the outer binding run (the honesty arbiter) and is the slowest path × the most frequent event. R2 gives contamination-freedom cheaply; the outer R3 run is the backstop that catches any case where R2's idempotent re-run diverged from a true from-clean build.

---

## 3. The loop (data flow)

```
outer iteration (flag enable_binding_install on):
  script = render_build_script(graph, manual_blocks)
  reset_to_base()                       # fresh container from base_image

  ── install phase ──
  rc, fail = run_install_script(script) # bash setup.sh; set -e aborts at first failing line
    └─ rc != 0 → localize (mode A): failing line → preceding #@node + stderr

  ── certify phase ──  (only if install rc 0)
  graph = certify_refresh(graph, exec_readonly)   # run each #@node's #@check read-only
    └─ any reciped node MISSING → localize (mode B): that #@node (hollow-success, e.g. cv2 t64)

  if install rc 0 AND all reciped nodes PRESENT:
      installability BINDING gate SATISFIED
      run pytest on the certified container → testability   (done-path unchanged; Stage 3 re-wires it)
  else:
      assemble debug bundle (§5) for the localized node →
      INNER repair loop (run_structured_repair, R2):
        LLM → PatchProposal → PatchGate → re-render
        reset_to_last_good(); bash whole script; certify; retry the failing node  (bounded)
      once the node installs AND certifies → candidate fix → outer R3 re-verify
```

- **Two-tier honesty:** the inner R2 loop is a *fast candidate search*; the **binding** installability is **always a from-base (R3) install + certify**. R2 proposes, R3 certifies — so R2 imperfection is safe.
- **Two failure modes** drive repair: **(A)** an install line exits non-zero (`set -e` abort), and **(B)** install rc 0 but a node's `#@check` certifies MISSING (the hollow-success case). Both localize to a `#@node`.
- **Per-issue verification** = the next R3 run installs+certifies past the previously-failing node. **Loop-level exit** = an R3 from-base run where install rc 0 AND all reciped nodes certify PRESENT.
- Testability runs on the certified container; its termination logic is unchanged in Stage 2.

---

## 4. Error localization (decided: `#@node` mapping, two failure modes)

`render_build_script` annotates each executable install line with its source `#@node <id>` (and `#@block <id>` for governed patches). Localization maps *failing line → node*:

- **Mode A — install line rc≠0.** `set -Eeuo pipefail` aborts the script at the first failing command. An `ERR` trap (printing `$BASH_COMMAND` / `$LINENO`) or line capture identifies the failing line; the immediately-preceding `#@node`/`#@block` annotation gives the node. Hand the LLM the failing command **highlighted within its annotated block** + raw stderr + a bounded script window:

```
#@node syslib:libgl1  provider=apt:libgl1  unblocks=pkg:opencv-python==4.13.0.92
apt-get install -y --no-install-recommends libgl1      ← FAILED (rc=100)
--- stderr ---
E: Unable to locate package libgl1 ...
```

- **Mode B — certify MISSING after install rc 0.** The certify pass runs each `#@node`'s `#@check`; a node that returns non-PRESENT despite a clean install is the localized failure. Hand the LLM that node, its `#@check`, and the check's output (the cv2/`libglib2.0-0` t64 case). Mode B has no stderr from a *failed* command — the evidence is "installed but check says absent," which often means the **check command is wrong** (see §9 capability-check lesson), so the fix may target the node's `check_command` rather than its install.

**Bound the context:** failing node's block + a small window of neighbors (or the script's `#@node` outline) — not the whole file — so context stays bounded on large repos.

---

## 5. The repair debug bundle (input enrichment; output unchanged)

Each repair attempt the LLM sees a **three-part bundle**:

| Part | Source | Tells the LLM | Status |
|---|---|---|---|
| Localized runtime failure | container (this run) | mode A: failing cmd + block + stderr · mode B: node + `#@check` + "installed-but-absent" | **NEW** |
| Scoped `RepairScope` slice | the graph | providers, tried_failed, dep states, unblocks, cohort, gate, platform, evidence | exists |
| Bounded script window | rendered `setup.sh` | ordering / neighbors around the failure | **NEW** |

- The graph slice stays **scoped to the failing node's neighborhood** (current Slice B behavior — no whole-graph summary).
- It is **self-updating**: each failed attempt records to the graph (ledger attempts → `tried_failed`; `runtime_classify` → evidence; a certify-MISSING result is itself recorded), so the next slice shows "you already tried X and it failed/stayed-absent" — the anti-repeat signal.
- **Output unchanged:** LLM → typed `PatchProposal` → deterministic `PatchGate` → host re-renders via `render_build_script`. Stage 2 only enriches the *input*. Single authority preserved: the host certifies (install rc + per-node `#@check`); the LLM cannot declare success.

---

## 6. The seam (decided: extra optional callables, Q5=A)

`run_v3` today sees only `sandbox_execute(cmd)->(bool,str)` and `exec_readonly(cmd)->(int,str)`. Stage 2 adds **optional keyword callables** (default `None` ⇒ Stage-1 behavior), matching the existing thin-callable idiom (trivially faked in tests, backward-compatible, flag-gated):

- `reset_to_base: Callable[[], None] | None`
- `reset_to_last_good: Callable[[], None] | None`
- `run_install_script: Callable[[str], InstallResult] | None` — bash the rendered script in the current container; return `(rc, failing_line/command, stderr)` for mode-A localization.
- a flag `enable_binding_install: bool = False`.

The **certify phase reuses the existing `exec_readonly` callable + `certify_refresh`** — no new certify seam needed. Drivers (`agent.py`, `scripts/l2_repair_loop_smoke.py`) bind the new `Sandbox` methods. Rejected alternatives: a single `ContainerController` protocol object (churns the two-callable seam) and passing the concrete `Sandbox` (breaks the fake-callable test seam).

---

## 7. Components & files

| Component | Change | Effort |
|---|---|---|
| `src/python_deps/depgraph/build_script.py` | **reuse as-is** (`render_build_script`) — do NOT modify (handoff invariant: it stays pure/envstate-free) | — |
| `src/sandbox.py` | `reset_to_base()` (`docker rm` + run from `base_image`); `run_install_script(script)` (bash + mode-A localization → `InstallResult`); expose `reset_to_last_good()` (≈ existing `rollback()`); mount a persistent **pip/apt cache volume** | M |
| `src/envstate/` (new small module) | localization/debug-bundle assembly (mode A + mode B; `#@node` mapping; bounded window) | S |
| `src/envstate/repair_loop.py` (`run_structured_repair`) | reset-to-last-good + re-bash + re-certify between attempts; attach the localized failure to the obligation packet | M |
| `src/envstate/orchestrator.py` (`run_v3` + `_dep_emit_phase`) | new optional callables + `enable_binding_install` flag; when on, replace incremental `block_emit` install with the two-phase render→reset-to-base→install→certify; installability binds from (install rc 0 ∧ all reciped PRESENT) | M–L |
| drivers (`agent.py`, `l2` smoke) | bind the new `Sandbox` methods into `run_v3` | S |
| builder-side fix (graph/check) | `SystemLib` `#@check` prefers capability checks (`ldconfig -p \| grep <soname>`, `command -v`) over exact `dpkg -s <name>`; certify-MISSING feeds this fix back into the graph | S |
| test suite | two-phase path, localization (A+B), reset-to-base, byte-identical-off | L |

Execution-layer note: the handoff's prototype `DockerExecutor` (docker run -d + exec + cleanup, two-phase install/certify) was scratchpad-only. We **reuse the existing wired `Sandbox`** (it already has commit/rollback) rather than introduce `DockerExecutor`; the prototype is a reference for the two-phase logic. `docker commit` checkpoints a *certified* container.

---

## 8. Safety & byte-identical guarantee

- `enable_binding_install` defaults **False** ⇒ behavior byte-identical to Stage 1 (incremental `block_emit` path untouched; new callables `None` on the off-path).
- `run_v1` and the B3 ablation (`enable_script_materialization=False`) untouched.
- Anti-hollow preserved and strengthened: installability state is written only by the host (install rc + per-node `#@check`); the LLM still only proposes typed patches. `render_build_script` never writes `node.state` (handoff invariant).

---

## 9. Risks & open questions

- **Reset-to-base performance** — the full re-run per outer iteration is the main perf risk. Mitigation: the pip/apt cache volume turns re-runs into mostly cached no-ops up to the failing line. **Measure this as the Stage 2 benchmark arm** (turns/wall-clock vs the incremental arm); fall back to a hybrid (R2 search + a single final R3 binding run) only if measured cost is prohibitive.
- **`SystemLib` check-command robustness (the cv2 lesson)** — exact `dpkg -s <name>` is brittle under Debian renames (t64). Prefer capability checks; treat a certify-MISSING-after-clean-install as a strong signal that the *check* (not the install) is wrong, and promote the corrected check to the graph. Without this, mode-B failures will mis-localize as install problems.
- **Mode-A line→node precision** — block-level node attribution is usually exact (a block ≈ one node's install); the `ERR`-trap `$BASH_COMMAND`/`$LINENO` capture pinpoints the line in multi-line `#@block` patches. Confirm the trap reliably reports the failing line through `set -e`.
- **Container abstraction reconcile** — we use `Sandbox`, not the prototype `DockerExecutor`. Confirm `Sandbox` can (a) reset to `base_image` (not just `last_success`) and (b) run a multi-line script as one `bash` invocation with a trap. Both look feasible from `_restore_last_success_container` + `execute`.
- **Cache volume isolation** — a shared cache across repos must not serve a wrong wheel/version; key the cache appropriately (or accept index-level caching only).
- **Stage boundary** — testability runs on the certified container in Stage 2, but its `done`-wiring stays Stage 3; the binding-install change must not alter `next_decision`.

---

## 10. Research framing

Stage 2 delivers the **honest half of the installability gate**: `ebsr` certified by reproducing the env from a clean base via the compiled artifact **and per-node host certification**, not by a proxy on a dirty container or a bare `bash rc 0`. The cv2 hollow-success catch is concrete evidence the two-phase model works.

Positioning vs **HerAgent / "Prometheus"** (arXiv 2602.07871) sharpens here: HerAgent *generates* the whole `setup.sh` with the LLM and certifies **existentially** (a level passes if *any one* command rc 0), with no pinning / no `--no-deps` / no closure. Ours is the inverse and stronger: the **graph is the source of truth**, `render_build_script` is a **deterministic pinned projection**, the LLM is only a governed `#@block` proposer, and certification is **per-node and host-owned** — with fixes promoted to the graph, not patched into the container. The two gates are now both host-certified from clean; the reset-to-base loop is the *mechanism* by which localized failures become typed graph obligations.

---

## 11. Summary

Make installability **binding** by compiling the graph with `render_build_script` into a whole install-only `setup.sh`, running it from a clean base, **and host-certifying every node** — binding = **install rc 0 AND all reciped nodes certify PRESENT** (the two-phase model, proven by the cv2 t64 hollow-success catch). Localize the **two failure modes** (install line rc≠0 → preceding `#@node`; certify-MISSING → that `#@node`) and feed them — with the existing scoped `RepairScope` slice and a bounded script window — into the unchanged typed-patch repair loop. Use the **nested reset model**: inner repair = reset to last-good + bash whole script + certify (R2, fast candidate search; the "bash whole script" form dissolves the resume-point problem); outer = reset to base + bash + certify (R3, binding). Expose container controls as **optional `run_v3` callables** (default off ⇒ byte-identical), reusing the existing `Sandbox`'s commit/rollback and the `render_build_script` renderer (do not modify it). Keep a pip/apt **cache volume** to make reset-to-base affordable, and **measure** it against the incremental arm. The `done`-wiring, collect-only probe, and failure classifier remain Stage 3.
