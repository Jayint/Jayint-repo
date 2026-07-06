# Graph-Ablation Localization Experiment — design (DRAFT for review)

**Date:** 2026-07-06 · **Branch:** `john-v3-multi-lang` (SHARED — commit local, never push).
**Purpose:** Earn the paper claim *"a certified dependency graph helps an agent localize and
repair build failures better than no graph"* with a controlled, ablatable experiment.
**Status:** DRAFT — 3 open design decisions flagged inline (§9) for the author before we
turn this into an implementation plan.

## 1. The claim under test (narrow, defensible)

> A certified dependency graph converts build-failure repair from unstructured error-text
> interpretation into **structured root-cause localization** — mapping a failure to a typed,
> tiered, provenance-scored node and its dependency neighborhood. This (H1) raises
> localization accuracy, (H2) lowers repair cost, and (H3) cuts wasted (non-causal) actions —
> most sharply on native/system-dependency failures and over-predicted (phantom) needs.

We do NOT claim a universal edge. The scoped-by-failure-class framing is the honest, novel
contribution and is what protects the result from a "here's a case where the graph doesn't
help" reviewer.

## 2. The critical isolation: failures that SURVIVE construction

The graph's already-demonstrated value is at **construction** (first-shot-correct build
script). To measure a *debugging/localization* benefit we must study failures that occur
**despite** the graph, then test whether its structure helps repair them. Protocol:

1. Build the graph + `setup.sh` on the **clean** repo (the system's normal output).
2. **Inject** a known-root-cause failure into the *environment or the rendered script* (never
   by editing the repo's declarations the graph read) so the first build fails.
3. Hand the failed build to the agent to repair.

This guarantees the graph was produced without peeking at the injected fault (no answer-key
leakage), while still legitimately containing the structured knowledge that is the treatment.

## 3. Conditions (arms) — the ablation

Same agent model, temperature, repair-loop mechanics, base image, failure, and budget across
all arms. The ONLY thing that varies is the dependency context the agent is given.

| Arm | Context given to the agent | Isolates |
|---|---|---|
| **C0 — no-graph (baseline)** | repo + failed `setup.sh` + stderr + exit code; free ReAct iteration | the status quo (RAT/repo2run-style) |
| **C0.5 — flat-list (control)** | C0 **+** a flat dependency list (names/versions, à la `pip freeze`) — NO tiers/edges/provenance | rules out "the graph arm just knows the deps" |
| **C1 — graph-augmented** | C0 **+** the dep graph as **read-only query tools** (see §6) | the treatment: *structure*, not just info |

The **C0.5 flat-list control is what makes this paper-grade.** A two-arm (C0 vs C1) result
invites "of course the graph arm wins, it has more information." C0.5 gives the agent the same
dependency *information* with none of the *structure*, so a C1 > C0.5 gap is attributable to
typed/tiered/provenance structure specifically.

Agent substrate = the existing **RAT** build-setup agent (pluggable `BaseEvalModel`), so C0 is
a real, already-tuned ReAct baseline (not a strawman). C0.5/C1 are new *context providers*
layered on the same agent — the agent's reasoning/loop is untouched.

## 4. The failure corpus + injection recipe

~10–14 injections over ~6–8 repos, **≥2 per class** (echoes the corpus "≥2 per stratum"
guardrail), each with a declarative oracle record:

```
{ injection_id, repo, base_image,
  injection_type,            # one of the 5 classes below
  mutation,                  # exactly how the env/script is perturbed
  known_root_cause,          # the true causal need (node id / package / pair)
  correct_action,            # install:<pkg> | drop:<need> | repin:<pkg><spec>
  failure_class }            # MISSING_SONAME | COMPILER_ABSENT | VERSION_CONFLICT | OVERINCLUDE | TOOL_ABSENT
```

Five injection classes (mapped to the claim's failure modes):

| Class | Mutation (survives construction) | Known root cause | Correct action |
|---|---|---|---|
| **(a) SYSLIB_MISSING** | strip a required system lib from the image / drop the apt line for it | the syslib | `install:apt:libX-dev` |
| **(b) COMPILER_ABSENT** | native-build repo on an image with no `build-essential` | compiler toolchain | `install:apt:build-essential` |
| **(c) VERSION_CONFLICT** | inject an incompatible pin into a requirements file the graph didn't gate | the conflicting pair | `repin:<pkg><spec>` |
| **(d) OVERINCLUDE (phantom)** | force the script to include an OPTIONAL dep that fails to build on -slim | "the dep is not required" | `drop:<need>` |
| **(e) TOOL_ABSENT** | GitPython repo with `git` removed from the image | `git` | `install:apt:git` |

Class **(d)** is the provenance test and the sharpest expected win: the *correct* action is to
**drop**, not fix. The graph carries `discovered_by`/`strength`/`data.optional` that flag the
need as droppable; C0 sees only "package X failed to install" and is expected to thrash trying
to satisfy it. (d) uses the real over-prediction failure mode your eval already surfaces.

Base images and repos reuse the build-script-eval corpus (`corpus.py`) where possible; native
repos (pygraphviz/lxml/pyzmq) supply (a)/(b), GitPython-users supply (e).

## 5. Metrics — localization is the headline (operationalized, gradeable)

Localization is scored **separately from final success.** For each (injection, arm, seed):

**Localization (H1):**
- **`localized@k`** — within the first *k* repair actions, did the agent take an action whose
  target matches `known_root_cause`/`correct_action`? Report `localized@1` and `localized@3`.
  - install-class: proposes installing the correct package (match on target, canonicalized).
  - drop-class: **decides to remove/skip** the phantom rather than install it (the behavioral
    discriminator).
  - conflict-class: adjusts the correct pin / names the conflicting pair.
- **`first_correct_rank`** — index of the first correct-target action (∞ if never). Lower = faster.
- **`mislocalized`** — did it commit to a WRONG root cause (fix a symptom, install for a phantom)?

**Repair cost (H2):** repair cycles to green (or budget-exhausted); total commands; total tokens.

**Wasted actions (H3):** `wasted_rate = non-causal actions / total actions` before green.

**Final success (secondary):** `env_works` (reuse the build-script-eval ladder verbatim).

**Grading:** deterministic-primary — parse each agent action (it emits shell commands / edits)
and match its target against the oracle's `correct_action` (canonicalize package names via
`canon_pip`, apt names via the existing maps). LLM-judge as a **secondary cross-check** on a
sample, report grader agreement (mirrors the "cheap LLM-judge + known-answer corpus; judge
diagnostic, never headline" directive). Deterministic matching is feasible because actions in
this domain are structured (`apt install X`, `pip install Y==Z`, remove-from-requirements).

## 6. The graph as read-only query tools (the treatment surface)

C1 exposes the graph to the agent as tools (NOT a pre-computed answer — the agent must do the
localizing *with* the graph, keeping the agent as the localizer in both arms):

- `get_node(id) -> diagnostic_view` (id/type/name/tier/layer/state/chosen_fix/fix_candidates/discovered_by/strength)
- `requires_of(id)` / `required_by(id)` — walk the neighborhood / blame path
- `nodes_by_tier(tier)` / `search(symptom)` — map a `.so`/tool/module symptom to candidate nodes
- a one-screen rendered graph summary in the initial context

Handing C1 a **pre-computed failure→node binding** would be a stronger but less honest
treatment (we'd have localized for it). Keep that as a **sensitivity arm (C1-strong)**, not the
primary, so the headline result reflects the agent using the structure itself.

## 7. Confound controls & validity

- **Stochasticity:** N≥3 seeds per (injection, arm); report mean ± bootstrap CI.
- **Same-everything:** identical model/temp/budget/image/failure across arms; only context differs.
- **No leakage:** clean-build-then-inject (§2); the oracle answer is never in the agent context.
- **Strong baseline:** C0 is the tuned RAT agent, not a hobbled one — validity depends on this.
- **Information vs structure:** C0.5 controls information quantity (§3).
- **Grader validity:** deterministic + LLM cross-check; publish agreement rate.
- **Budget-matched context:** cap C1's rendered-graph tokens so "more context" isn't the driver.

## 8. Analysis / what a win looks like

Primary table: **per failure-class × arm**, `localized@1`, `localized@3`, `first_correct_rank`,
`cycles`, `wasted_rate`, `env_works`. Expected shape: **C1 > C0.5 > C0** on localization and
wasted_rate, with the largest gap on (d) phantom and (a)/(b)/(e) system classes; the smallest
on (c) if resolver stderr is already self-explanatory. The paper's figure is the class-stratified
localization@1 bar chart across the three arms.

Reusable apparatus: construction (`build_dep_graph`), replay ladder (fresh container +
`env_works`), `classify_execution_failures` (symptom parsing), RAT agent (C0 substrate),
case-study consolidator (per-run traces for the grader). New code: injection layer, the
flat-list + graph context providers, the localization grader, the metrics aggregator, the
oracle manifest.

## 9. DECISIONS — LOCKED (2026-07-06)

1. **Arms: 3 (C0/C0.5/C1).** The flat-list control is what makes "structure helps" defensible.
   The harness builds all three arm-providers; the **pilot runs C0 vs C1** first (see #3), and the
   scale-up adds C0.5 + the C1-strong sensitivity arm.
2. **Treatment strength: query-tools (C1) primary.** The pre-computed failure→node binding is a
   later sensitivity arm (C1-strong), not the headline — the agent must localize *with* the graph.
3. **Scope: 5-injection pilot first** (one injection per class, 1 seed, C0 vs C1) to validate the
   grader and confirm the effect exists, THEN scale to the full 3-arm multi-seed. This is what the
   implementation plan `docs/superpowers/plans/2026-07-06-graph-repair-ablation-pilot.md` builds.

## 10. Non-goals

- Not measuring the *construction* benefit (that's the existing build-script eval).
- Not a new agent — RAT is the substrate; we only add context providers + grading.
- Multi-language: injection classes are Python-specific; the mechanism (typed attribution) is
  ecosystem-agnostic — note as future work, don't build it here.
