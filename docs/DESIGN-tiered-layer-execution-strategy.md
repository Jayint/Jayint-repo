# DESIGN: Tiered-Layer Execution Strategy

**Status:** design reference (not yet fully wired)
**Date:** 2026-06-23
**Branch:** john-planner-v3
**Related:** `DESIGN-static-probe-certified-dependency-graph.md` (§5 node/edge model, §6 layering),
`superpowers/specs/2026-06-23-uv-enriched-depgraph.md`, `IMPL-STATUS-depgraph.md`
**Prior art:** HerAgent — "Rethinking Automated Environment Deployment via Hierarchical Test
Pyramid", arXiv:2602.07871 (Feb 2026, UCL/Uppsala/CMU; code github.com/EuniAI/EnvAgent).

---

## 1. Why this doc

Captures how the dependency graph's **layer hierarchy** is used as the *operational spine* of the
build agent — i.e. how `layer` changes what the agent **does** during execution, not just how the
graph reads. It also records the second, orthogonal hierarchy (a success-maturity ladder) we want to
adopt from HerAgent, and how the two combine into an honest done-gate.

There are **two distinct hierarchies plus one orthogonal axis**. Keep them separate:

```
(A) BUILD-STACK LAYERS   vertical composition: base/OS -> ... -> tests   (HOW the env is built)
(B) MATURITY LADDER      validation depth: Installable -> Testable -> Runnable  (HOW DONE the env is)
(C) RUNTIME AXIS         orthogonal: env vars / services / data            (NOT a vertical layer)
```

(A) is ours today (the `Layer` enum). (B) is the HerAgent idea to adopt as the semantics of the
Test/goal node. (C) is the correction from our earlier debate: env/service needs are not a layer.

---

## 2. (A) The build-stack layer lattice

Bottom-up. `layer` is a **node attribute** (a coordinate), never a rigid container; execution order is
*derived* from `layer` + `requires` (a layer-constrained topological sort), never stored as edges.

```
L0  base / OS image      docker base, distro, glibc            (substrate; NOT yet modeled — see §7)
L1  interpreter          python 3.x
L2  system               shared libs: libGL.so.1, libxcb.so.1
L2' toolchain            compilers/headers: gcc, pg_config, Python.h   (build-time deps)
L3  pip / packages       opencv-python, numpy, flask           (the "framework/library" layer)
L4  import / naming       cv2, PIL                              (the interface to the code)
L5  tests / app           repo_tests_pass                       (the goal)

orthogonal:  runtime     DATABASE_URL, postgres service, chrome binary, model weights
```

Notes:
- **"Framework" is not its own layer.** A framework (flask/django) and an ordinary library are the
  same *kind* of need — both are pip distributions resolved/certified identically. Framework-ness is
  at most a node attribute, not a band.
- **Discovery order ≠ execution order** (design §3.3/§10.10): a SystemLib is *discovered* after
  installing the pip package that needs it, but *executed* (apt) before the pip/import certification.

---

## 3. How layers help the agent DURING EXECUTION (the operational payoff)

`layer` is the control variable for five decisions in the loop. Each entry lists the wrong behavior it
prevents.

### 3.1 Install/build order — "the first build lands"
Layer drives a bottom-up topological sort, so the agent emits/runs **base → apt(system) → toolchain →
pip → import**.
- Without: `pip install psycopg2` runs before `apt install libpq-dev` → fails on `pg_config not found`.
- With: toolchain < pip is guaranteed → first real build succeeds. (This is the literal Dockerfile
  line order and the discovery-loop order; deterministic, no LLM.)

### 3.2 Failure → actuator dispatch — "the right repair tool"
The layer of a failed node is the dispatch key for the repair handler:
```
system    -> apt-get install
toolchain -> apt-get install (build dep)
pip       -> resolver / pip
naming    -> fix import->distribution mapping (cv2 -> opencv-python)
runtime   -> set env / start service / STOP
```
- Without: agent sees `ImportError: libGL.so.1` and tries `pip install libGL` (wrong actuator) — the
  classic wasted loop.
- With: `libGL` is `system` → route to apt, never pip.

### 3.3 Fix the lowest broken layer first — "one action clears many reds"
Higher-layer failures are usually *caused* by a lower-layer one; pick the next action by **lowest
unsatisfied layer**.
- Without: `libGL missing` + `cv2 import fails` + `test fails` look like three problems.
- With: all three trace to one `system`-layer root; one `apt install libgl1` turns all three green.
  Layer ordering dedupes a cascade into a single root action.

### 3.4 STOP / done verdict — "kills hollow success" (combine with §4)
```
all build-stack layers (L0–L4) certified  =>  the environment is built.
remaining failure is L5 (app/test logic) or the runtime axis  =>  NOT an env fault  =>  STOP + report.
```
- Without: agent can't tell "env broken" from "code broken" → loops forever or falsely declares
  success on `pytest --collect-only` (the recurring hollow-success defect).
- With: "build stack green, test still red" is a *defined, honest* terminal state.

### 3.5 Cheaper repair via cross-layer alternatives — "the cost lever"
Because layers are ordered, a higher/cheaper fix can **retract** a lower/expensive need:
- `cv2` needs `libGL` (system, apt). Swapping at the **pip layer** to `opencv-python-headless`
  *retracts* the system-layer `libGL` need entirely.
- With: agent compares "apt at system layer" vs "swap at pip layer" and picks the one that deletes the
  most lower-layer work.

### 3.6 Bonus mechanics
- **Docker cache order:** layer order = build-cache order; base/system layers cache across rebuilds,
  pip churns on top → faster iteration.
- **Re-certification scope:** a mutation at layer N invalidates only *higher* layers, not the whole
  graph (numpy downgrade at pip re-certifies imports above; doesn't touch the interpreter).
- **Bounded LLM slice:** the agent can trust certified lower layers and reason only about the current
  one → less wasted reasoning.

### Worked example (cv2 + psycopg2 on fresh python:3.11-slim)
1. Sort by layer → plan system/toolchain first (§3.1).
2. `pg_config` (toolchain) → apt `libpq-dev`; `libGL` (system) → apt `libgl1` — both before pip (§3.2).
3. One apt step turns toolchain/system reds green; pip + import nodes above go green same pass (§3.3).
4. Tests fail on `KeyError: DATABASE_URL` → runtime axis, not a build layer → **env certified, STOP,
   report "needs DATABASE_URL"** (§3.4) instead of looping.
5. Alternatively the agent could swap `opencv-python-headless` and skip the `libgl1` apt (§3.5).

---

## 4. (B) The maturity ladder — Test-node semantics + two-tier STOP

From HerAgent's **Environment Maturity Hierarchy** (partial order, necessity but non-sufficiency):

```
Installability  ⊊  Testability  ⊊  Runnability
deps install        tests run         main entry / suite actually passes
```

HerAgent's empirical finding (RQ2): Install→Test retention is easy (91–94%) but **Test→Run drops
~50%** — *"passing unit tests is an insufficient proxy for end-to-end system usability."* This is
independent confirmation of this project's collect-only / hollow-success scar (honest success =
`ebsr AND pass_rate>=0.8`).

**Adopt as the Test/goal node's maturity sub-state, and split the STOP verdict in two tiers:**

```
Tier-1 STOP  (env built):    L0–L4 all host-certified  =>  Installable + Testable provable
Tier-2 SUCCESS (project runs): the suite PASSES at threshold (pass_rate>=0.8) or the main entry runs
                               =>  Runnable
```

This operationalizes the ladder **without disturbing the build-stack layers (A)**: layers (A) decide
*how to build and when the env is done*; the ladder (B) decides *how thoroughly "done" was proven*.
It directly kills the collect-only false-pass: `pytest --collect-only` rc=0 is at most Testability-ish
and never Runnable.

**Keep our certification stricter than HerAgent's.** Their oracle is lenient — a level passes if *any
one* command returns 0 — and their state transition is LLM-driven. Ours stays: per-node host
`check_command`, deterministic, and Runnable requires a real pass threshold, not one green command.

---

## 5. (C) The runtime axis (orthogonal, not a layer)

`runtime` needs — env vars (`DATABASE_URL`), live services (postgres/redis), browser binaries, data
files/model weights — do **not** belong in the vertical stack: a fully built L0–L4 env still fails on
a missing env var. Folding them into the ladder manufactures false certainty at the env-vs-bug
boundary. Model them as a side axis hanging off the Test goal, with a positive STOP state
("stack certified; remaining failure is downstream / unprovisionable → report, don't loop").

---

## 6. Prior art: HerAgent (arXiv:2602.07871) — what to take, what to keep ours

**Take:**
- The Install/Test/Run maturity ladder as Test-node semantics + two-tier STOP (§4).
- Test-command mining/classification (discover run/test/entry commands from docs/CI/source) to
  populate `check_command`s.
- The emitted artifact as the single monotonic state carrier (replay certified providers, never raw
  trajectory) — counters the lossy-synthesizer scar.
- Keep both repair granularities (per-layer single actuator + full bottom-up rebuild); their ablation
  shows removing either hurts.

**Keep ours (differentiators they lack):**
- Per-node **host-certified** tri-state (`unknown/missing/satisfied`) vs their LLM-driven, lenient
  any-one-command oracle.
- **uv-pinned transitive closure** (uv.lock) vs their `pip install -r requirements.txt` (no solver, no
  lockfile → "runs once" ≠ reproducible).
- A **typed dependency graph** (Package/SystemLib/Tool/Runtime + `requires`/`alternative_to`/
  `conflicts_with`) vs their Tree-sitter/Neo4j graph of *source files for retrieval*.
- **Proactive probing** for system deps + the **orthogonal runtime axis**.

**Be skeptical of:** their existential success oracle (inflates Runnability), uneven Pass@k across
benchmarks, the "holistic not reactive" claim (case studies discover system deps via build failures),
and "reproducible" without pinning.

---

## 7. Implementation status & next steps

**Already in the graph:**
- `layer` attribute on every node (`schema.Layer`: interpreter/system/toolchain/pip/naming/runtime/
  tests).
- Layer-constrained topological execution intent (design §6); host per-node certification
  (`state` flipped only by a `check_command`); uv-pinned transitive edges; probing; per-root
  resilience.

**To wire (this strategy's build step):**
1. **Layer-ordered planner**: choose the next action by lowest unsatisfied layer (§3.1, §3.3).
2. **Layer→actuator dispatch** table (§3.2).
3. **Two-tier STOP verdict** with the maturity sub-state on the Test node (§3.4, §4).
4. **base/OS L0 node** (base image + interpreter) so the bottom of the stack and the certification
   scope are explicit (today `python:3.11-slim` is an unmodeled assumption).
5. **Runtime axis** node kind wired to the Test goal with the downstream-STOP state (§5).
6. (Optional) test-command mining to populate the Test node's run-level `check_command` (§6).
7. (Optional viewer) layer-swimlane layout so the graph reads top-to-bottom as a stack.

**Not in scope here:** the LLM Planner's repair reasoning, Dockerfile finalize/clean-rebuild
promotion (covered in the static-probe design doc), cross-run knowledge base.

---

## 8. One-line summary

Use **build-stack layers (A)** as the operational spine — ordering, dispatch, cascade-collapse, cost —
and the **maturity ladder (B)** as the Test-node's success semantics; together they yield an honest
two-tier STOP verdict (env built vs project runs), with the **runtime axis (C)** kept orthogonal.
Ours stays stronger than HerAgent via host per-node certification, uv pinning, and proactive probing.
