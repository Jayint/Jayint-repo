# Gate-Ladder Stage 2 — Binding Dep-Spine Installability via Reset-to-Base — Design

> **Extends:** `docs/superpowers/specs/2026-06-29-gate-ladder-outer-loop-design.md` (two-gate model); builds on the Stage 1 observability scaffold (`src/envstate/gates.py`, landed `ba7f829..d817ab4`).
> **Wires:** `docs/superpowers/HANDOFF-graph-to-build-script-renderer.md` — the `render_build_script` renderer (built, container-validated, deliberately inert). Stage 2 is the "first non-additive step" that handoff names.

**Status:** DESIGN (decided via brainstorm + 3-agent adversarial review, 2026-06-30). Not yet implemented.

**Scope name:** Stage 2 binds **dep-spine** installability (system + pip reciped nodes). The **project's own install** (`pip install -e .`), **`#@need`/`#@block` certification**, and **service/config** are explicitly **deferred to Stage 2.5** (§7 Deferred). Calling the gate "binding dep-spine installability" is deliberate honesty: it does not yet certify that the project itself builds.

---

## 0. Correction to the gate-ladder spec

The gate-ladder spec (§6/§9/§11) says *"Sandbox has no commit/checkpoint support — Stage 2 must add it."* **Wrong.** `src/sandbox.py` already auto-commits after each successful state-changing command (`execute()`→`_should_commit()`→`container.commit()`), keeps a baseline snapshot + `last_success_image`, and rolls back via `docker rm`+`docker run <img>`+ephemeral replay (`_restore_last_success_container`/`rollback()`); the v3 loop uses it (`agent.py:1366` `sandbox_execute=self.sandbox.execute`; `agent.py:1371` `exec_readonly=self.sandbox.exec_readonly`). So Stage 2 is: (a) a loop **control surface** over the container, (b) a **fresh-from-base install + host certify** binding gate, (c) **localized** failures into the existing repair loop. (Patch the gate-ladder spec's §6/§9/§11 to match.)

---

## 1. Goal & scope

**Goal:** turn Stage 1's *provisional* installability gate into a **binding dep-spine `ebsr`** check by compiling the graph into a whole install-only `setup.sh` (`render_build_script`), running it from a clean base container, **and host-certifying every reciped node**, with localized failures feeding the existing typed-patch repair loop.

**In scope:** wire `render_build_script`; the two-phase install→certify binding gate over reciped `#@node`; a container control surface (reset-to-base, run-install-script); reset-to-base execution model (flag-gated, replacing the incremental `block_emit` install when on); error localization on the two failure modes; an enriched repair debug bundle; a pip/apt cache volume.

**Out of scope → Stage 2.5:** project install (`pip install -e .`); certification of `#@need` stubs and `#@block` (LLM-patch) sections; service/config nodes; tier-by-tier snapshot optimization.

**Out of scope → Stage 3:** `done`-condition wiring (`next_decision` reading testability gate state); `pytest --collect-only` probe; `classify_gate_failure`→typed-obligations.

---

## 1.5 Reuse + the binding model (two-phase, necessary AND sufficient conditions)

### Renderer (reuse-only, do NOT modify — handoff invariant)
`render_build_script(graph, manual_blocks) -> str` (`src/python_deps/depgraph/build_script.py`, pure / byte-reproducible / never writes `node.state`) compiles the graph into ONE **install-only** `setup.sh`: hard `Layer`-tier sections, intra-tier `topo_order`, `--no-deps` pinned pip, one hoisted `apt-get update`, preamble `set -Eeuo pipefail`. Annotations: `#@node` (executable install line for reciped PACKAGE-with-version / SYSTEM_LIB|TOOL-with-`apt:`-`chosen_fix`), `#@need` (comment-only stub for CONFIG/SERVICE/DATA_ASSET), `#@block` (governed LLM patch), `#@check` (the node's check, emitted as a **comment, NOT executed**).

### Two-phase install/certify
1. **install** — `bash setup.sh` from clean base; `set -e` aborts at the first failing install line.
2. **certify** — host runs each reciped node's `#@check` read-only (`certify_refresh` → `certify_all`) → flips `State`.

`bash rc=0 ≠ certified`. Proven by the cv2 e2e: install rc 0 but `syslib:libglib2.0-0` certified MISSING (Debian-13 t64 rename). **But the adversarial review showed two-phase is *necessary, not sufficient*** — the gate's honesty also requires *certify coverage* and *check quality*. The binding condition is therefore precise:

> **Binding dep-spine installability** ⇔
> (a) **install rc 0**, AND
> (b) **every `_is_reciped` node that has a `check_command` certifies `State.SATISFIED`**, AND
> (c) **no `_is_reciped` node lacks a `check_command`** (enforced at render time — see below).

Conditions that make (b)/(c) sound (folded in from review):

- **No-check nodes are a build error, caught at render (review C2).** `_is_reciped` requires a version/`chosen_fix` but NOT a `check_command`; `certify_all` leaves a no-check node `UNKNOWN` (`certify.py:68-69`), and `UNKNOWN ≠ SATISFIED`. To avoid both the hollow-pass (treat UNKNOWN as pass) and the livelock (treat as fail with no repair path — `failed_reciped_nodes` skips no-check nodes), **`render_build_script`'s consumer must fail fast**: if `_is_reciped(node) and not node.check_command`, raise before running, surfacing it as a graph defect the LLM must fix (supply a check). The binding certify evaluates only `_is_reciped` nodes (a `certify_reciped_only` filter/wrapper — `certify_refresh` certifies *all* nodes, so `#@need` stubs must be excluded; review I3).
- **Prefer importability checks over metadata checks (review C4).** `python -m pip show X` passes even when a `--no-deps` install left a transitively-broken, unimportable package. Where a module name is known, the pip node's `check_command` should be `python -c "import <module>"`; C-extension syslibs should use a functional import or `ldconfig -p | grep <soname>` rather than `dpkg -s <name>`. This is the builder-side check-quality fix (§7) and the corrected check is promoted to the graph deterministically (not via an LLM patch — see §4/review I2).

This **replaces** the earlier `render_setup_sh` + "rc 0 → SATISFIED" framing.

---

## 2. The reset model (decided: reset-to-base every attempt — R2 dropped)

Every install attempt — first try and every repair retry — does the same thing:

```
reset_to_base()              # docker rm + docker run from base_image (NOT last_success)
rc, fail = run_install_script(script)
graph = certify_reciped_only(graph, exec_readonly, cycle)
```

**Why R2 was dropped (review I1/I2/I4):** the earlier nested "reset-to-last-good (R2) for fast inner search" gave little real speedup on the critical path — `run_install_script` is a *single* `bash` exec, so the sandbox commits at most once per run; on the **first** failing node `last_success_image ≈ the pre-install baseline`, so R2 would re-run the whole script anyway (≈ R3). R2 also could not undo apt **removals** (false negatives) and could inherit a partially-committed install (`_is_informational_exit` commits exit-1 "usage" output as success). Reset-to-base every attempt is simpler, always-honest, and depends on no snapshot hygiene. Cost is mitigated by the cache volume (§9).

**Deferred optimization (Stage 2.5):** if measured too slow on large closures, reintroduce **tier-by-tier commits** inside `run_install_script` so a true reset-to-last-tier becomes available — accepting the resume-point complexity then, with evidence.

---

## 3. The loop (data flow)

```
outer iteration (flag enable_binding_install on):
  script = render_build_script(graph, manual_blocks)   # raises if a reciped node lacks a check (C2)
  reset_to_base()

  ── install phase ──
  result = run_install_script(script)                  # bash setup.sh; set -e + ERR trap
    └─ result.rc != 0 → localize mode A: result.failing_command → preceding #@node + result.stderr

  ── certify phase ──  (only if install rc 0)
  graph = certify_reciped_only(graph, exec_readonly, cycle)   # reciped #@node only
    └─ any reciped node not SATISFIED → localize mode B: that #@node (installed-but-not-certified)

  if result.rc == 0 AND all reciped-with-check nodes SATISFIED:
      binding dep-spine installability SATISFIED
      run pytest on the certified container → testability   (done-path unchanged; Stage 3 re-wires it)
  else:
      assemble debug bundle (§5) for the localized node →
      repair (run_structured_repair): LLM → PatchProposal → PatchGate → re-render
      continue            # next outer iteration: reset_to_base + install + certify is the binding re-verify
```

- **Every binding check is a from-base install+certify.** There is no incremental "candidate search" tier; the repair produces a graph patch and the next iteration re-verifies from clean.
- **Two failure modes:** **A** install line rc≠0 (`set -e` abort); **B** install rc 0 but a reciped node not `SATISFIED`. Both localize to a `#@node`.
- **Per-issue verification** = the next from-base run installs+certifies past the previously-failing node. **Loop-level exit** = a from-base run where install rc 0 AND all reciped-with-check nodes `SATISFIED`.
- Stage-2 repairs admit **typed node/edge patches** (add_requirements / add_edges / version changes → certified `#@node`); `#@block`/script-patches and `#@need` satisfaction are Stage 2.5 (so nothing uncertified can satisfy the Stage-2 gate). Testability runs on the certified container; its termination logic is unchanged.

---

## 4. Error localization (two modes, `#@node` mapping) + check-quality guard

`render_build_script` annotates each executable install line with its `#@node <id>` (and `#@block <id>`). Localization maps *failing line → node*.

- **Mode A — install line rc≠0.** `run_install_script` injects an ERR trap (`trap 'echo "FAIL:$BASH_COMMAND:$LINENO" >&2' ERR`, prepended to the script string before `bash -c` — the renderer is unmodified, so the consumer adds the trap). The trap yields the failing command + line; the preceding `#@node` annotation gives the node. Hand the LLM the failing command **highlighted in its annotated block** + raw stderr + a bounded window.
- **Mode B — reciped node not SATISFIED after install rc 0.** Two distinct causes, and the bundle must NOT prejudge (review C1): (i) the **check is too strict** (cv2/t64 — install fine, `dpkg -s` wrong), or (ii) the **install fetched the wrong thing** (a transitional/alias package installed rc 0 but the needed lib is absent). The bundle presents **both** hypotheses and includes `dpkg -l | grep <name>` / `pip show <name>` evidence so the LLM can distinguish them. **Anti-weakening guard:** `PatchGate` must reject a proposed `check_command` that cannot fail on a container where the node's install line is omitted (i.e. a check structurally incapable of detecting absence) — otherwise mode-B "fix the check" becomes a new hollow-success path.
- **Builder-side, deterministic (review I2):** correcting a brittle SystemLib check (`dpkg -s` → `ldconfig`/import) does **not** go through the LLM `PatchProposal` route (the schema has no `update_node`/override). A deterministic pre-repair heuristic rewrites such checks and promotes them to the graph.

Bound the window to the failing node's block + a few neighbors (or the `#@node` outline), not the whole file.

---

## 5. Repair debug bundle (input enrichment; output unchanged)

| Part | Source | Tells the LLM | Status |
|---|---|---|---|
| Localized failure | container (this run) | mode A: failing cmd + block + stderr · mode B: node + `#@check` + `dpkg -l`/`pip show` evidence + **both** hypotheses | NEW |
| Scoped `RepairScope` slice | the graph | providers, tried_failed, dep states, unblocks, cohort, gate, platform, evidence | exists |
| Bounded script window | rendered `setup.sh` | ordering / neighbors around the failure | NEW |

Graph slice stays **scoped** (no whole-graph summary) and **self-updating** (`tried_failed`/`runtime_classify`/certify-MISSING all recorded → anti-repeat). **Output unchanged:** LLM → typed `PatchProposal` → `PatchGate` → re-render. Host certifies; LLM cannot declare success.

---

## 6. The seam (optional callables)

`run_v3` gains optional kwargs (default `None` ⇒ Stage-1 behavior; matches the Stage-1 `enable_gate_observability` pattern):

- `reset_to_base: Callable[[], None] | None` — **new** `Sandbox` method: `docker rm` + run from `base_image`, **always** (distinct from `rollback()`/`_restore_last_success_container`, which use `last_success_image` and only fall back to base when no snapshot exists — review I4).
- `run_install_script: Callable[[str], InstallResult] | None` — bash the rendered script with the prepended ERR trap. **Must bypass `Sandbox.execute()`** (its `_get_invalid_compound_setup_prefix` preflight rejects multi-step scripts) and call `container.exec_run` directly, like `exec_readonly` does (review/grounding). Because it bypasses `execute()` it never commits a snapshot, and `reset_to_base()` always precedes it — so the gate never depends on `last_success_image` (snapshot-state invariant; needs a code comment).
- a flag `enable_binding_install: bool = False`.

```python
@dataclass(frozen=True)
class InstallResult:
    rc: int
    failing_command: str | None   # $BASH_COMMAND from the ERR trap; None on success
    lineno: int | None
    stderr: str
```

The **certify phase reuses the existing `exec_readonly` callable + `certify_refresh`** (called with its required `cycle` arg — `certify_refresh(graph, exec_readonly, cycle)`; the 2-arg form raises `TypeError`), wrapped by a `certify_reciped_only` filter. Drivers (`agent.py`, `scripts/l2_repair_loop_smoke.py`) bind the new `Sandbox` methods.

---

## 7. Components, task order & deferred

**Task order** (dependencies, corrected per blast-radius review): **(T1 ∥ T2) → T3 → (T4 ∥ T5); T6 independent.** The edges `T2→T3` (T3 calls the localizer + `certify_reciped_only`) and `T3→T5` (T5 binds callables into `run_v3`'s new kwargs that T3 adds) are real — do NOT start T5 on T1 alone.

| Task | Files | Change |
|---|---|---|
| T1 | `src/sandbox.py` | `reset_to_base()` (always `base_image`); `run_install_script(script)->InstallResult` (ERR-trap prepend, `container.exec_run`, mode-A localization); document the snapshot-state invariant (bypasses `execute()` so never `commit()`s; `reset_to_base()` always precedes it); mount persistent pip/apt **cache volume** |
| T2 | `src/envstate/` (new) | localizer + debug-bundle assembly (mode A/B, `#@node` mapping, bounded window, both-hypotheses mode-B); `certify_reciped_only` wrapper |
| T3 | `src/envstate/orchestrator.py` (`run_v3`+`_dep_emit_phase`) | optional callables + `enable_binding_install`; when on, replace incremental `block_emit` with render→`reset_to_base`→`run_install_script`→`certify_reciped_only`; binding from (rc0 ∧ all reciped-with-check SATISFIED); **render fail-fast if a reciped node lacks a check** |
| T4 | `src/envstate/repair_loop.py` | reset-to-base per attempt; attach debug bundle; cap inner-loop `failed_id` to the original via a NEW `cap_failed_id: bool = False` param (Stage 2 passes `True`; existing call sites stay `False`, so the shared `block_emit` repair path is unchanged — blast-radius review) |
| T5 | `agent.py`, `l2` smoke | bind new `Sandbox` callables |
| T6 | builder/check + `PatchGate` | deterministic SystemLib check rewrite (`dpkg -s`→`ldconfig`/import) promoted to graph; prefer `import` checks for pip; PatchGate anti-weakening guard (reject a check that can't detect absence) |

`render_build_script` is **reused unmodified**. Execution layer reuses the wired `Sandbox` (not the scratchpad `DockerExecutor`); `docker commit` checkpoints a *certified* container. **Most important regression test:** flag-OFF ⇒ byte-identical to Stage 1 (in T3).

**Deferred → Stage 2.5:** project install (`pip install -e . --no-deps`) + its certification (import the project); `#@need`/`#@block` certify coverage (review C3 — `#@block` installs are not graph nodes, so `certify_all` never checks them; admit them to the gate only once certified); service/config; tier-by-tier commit (R2 revival) if perf demands.

---

## 8. Safety & byte-identical

`enable_binding_install` defaults **False** ⇒ byte-identical to Stage 1 (incremental path untouched; new callables `None`). `run_v1` and the B3 ablation untouched. Anti-hollow strengthened: installability state written only by the host (install rc + per-node `#@check`), checks are render-time-validated (no-check = error) and quality-guarded (import-pref + anti-weakening). `render_build_script` never writes `node.state`.

---

## 9. Risks & open questions

- **Reset-to-base performance (now the primary risk, R2 dropped).** Every attempt re-runs the full script. Mitigation: pip/apt cache volume → cached no-ops up to the failing line. **Measure as the Stage-2 benchmark arm** (turns/wall-clock vs incremental); revive tier-commit R2 (Stage 2.5) only if measured prohibitive.
- **Cache keying (review M2):** default = **index-level only** (apt package index + pip wheel cache, not keyed per repo). Safe for pip (wheel filenames encode name+ver+py+abi+platform). **apt `.deb` filenames do NOT encode distro release** → a cache shared across **different base images** can serve the wrong binary; therefore key the apt cache by base image (or disable apt caching across differing base images).
- **`_is_informational_exit` pre-existing bug:** `execute()` commits exit-1 "usage" output as success. Mostly moot now (we reset to base, not last_success), but `run_install_script` must NOT route through `execute()` (it bypasses preflight anyway), so it won't inherit this.
- **Mode-B residual:** even with both-hypotheses framing + the anti-weakening guard, a sufficiently adversarial check could pass; the guard (reject checks that can't fail without the install) is the backstop. Verify the guard is implementable (run the proposed check on a container with the install line omitted).
- **Stage boundary:** testability runs on the certified container, but its `done`-wiring stays Stage 3; the binding-install change must not touch `next_decision`.

---

## 10. Research framing

Stage 2's contribution, sharpened by the adversarial review: **binding installability is two-phase (install + per-node host certify) AND requires certify-coverage + check-quality** — a stronger anti-hollow claim than "the script exits 0," and stronger than HerAgent's existential certification (arXiv 2602.07871: a level passes if *any one* command rc 0, no pinning/closure). Ours: graph = source of truth, `render_build_script` = deterministic pinned projection, LLM = governed proposer, certification = **per-reciped-node, host-owned, check-validated**, fixes promoted to the graph. Stage 2 binds the **dep-spine** half honestly; the project-install half is the explicit Stage-2.5 next rung.

---

## 11. Summary

Make **dep-spine** installability **binding** by compiling the graph with `render_build_script` into a whole install-only `setup.sh`, running it from a clean base, **and host-certifying every reciped node** — binding = **install rc 0 AND every reciped-with-check `#@node` certifies `SATISFIED`**, with **no reciped node lacking a check** (render fail-fast) and checks favoring **importability over metadata** plus a **PatchGate anti-weakening guard** (the review's necessary-not-sufficient hardening). **Reset to base on every attempt** (R2 dropped — its speedup was illusory on the critical path and it had removal/contamination gaps); a pip/apt **cache volume** keeps it affordable, **measured** against the incremental arm. Localize the **two failure modes** (install rc≠0 → preceding `#@node`; certify-not-SATISFIED → that `#@node`, with both repair hypotheses) and feed them — with the scoped `RepairScope` slice and a bounded window — into the unchanged typed-patch repair loop. Expose container controls as **optional `run_v3` callables** (default off ⇒ byte-identical), reusing the wired `Sandbox` and the unmodified `render_build_script`. **Project install, `#@need`/`#@block` certification, and tier-commit R2 are Stage 2.5; `done`-wiring/collect-probe/classifier remain Stage 3.**

---

## 12. Promotion & cleanup path (making this "the" code; retiring the old loop)

A read-only inventory confirms: **nothing in the current install/loop hot paths is safe to delete now** — every module serves a live research ablation (B2 `run_v1`, B3 `enable_script_materialization=False`, B5 `block_emit`) or the v1 baseline. So "make this the code and remove old loop code to avoid confusion" is a **sequenced** outcome, not an immediate edit; the flag-gated, default-off, byte-identical design (§8) is exactly the safeguard that prevents premature promotion (and collapsing the B5 comparator).

**Phases:**
1. **Now — land Stage 2 flag-gated (`enable_binding_install=False`).** Add the new path; delete nothing. (Optional cosmetic only: `block_emit.py`'s `parse_setup_sh(render_setup_sh(compose_replay_script(...)))` triple round-trip is identity-equivalent to `compose_replay_script(...)` per `test_gsm_invariants_phase1.py` — simplifiable with a comment, but it's documentation, not dead code.)
2. **After benchmarks** (B5 `block_emit` vs B5-binding `render_build_script`) justify it — flip the default to `enable_binding_install=True`, gating the old incremental B5 path behind its own explicit flag.
3. **After the ablations are done/published** — retire the old incremental path: `block_emit.py`, `script_runner.py` (`run_blocks`), `script.py` (`render_setup_sh`/`parse_setup_sh`), the `enable_script_materialization=True` branch + the B3 `else` branch in `_dep_emit_phase`, and `repair_failed_nodes` (B3-only). `emit_drain` only once `run_v1` is also retired (it's shared by B3 **and** `run_v1`). Plus companion tests (`test_block_emit.py`, `test_script_runner.py`, `test_script_render.py`, `test_v3_block_emit_wiring.py`, parts of `test_sliceA_seam_integration.py`).

**Never removable (permanently load-bearing):** `render_build_script`, `run_structured_repair`, `compose_replay_script`, `certify_*`, `admit_proposal`/`validate_proposal`, `GateResult`/`evaluate_gates`, the `run_v3` body, `_build_v1_ledger_appender` (test-covered — deleting it broke ~20 tests), and `run_v1` until the ablations are published.

**Gotchas:** `render_build_script` has zero `src/` callers today — **inert by design** (HANDOFF §1), not dead; Stage 2 T3 adds its first live caller. `run_install_script` (whole-script + ERR trap + `certify_reciped_only`) is a genuinely different execution model from `run_blocks` (block-by-block) — a parallel implementation, not a rename.
