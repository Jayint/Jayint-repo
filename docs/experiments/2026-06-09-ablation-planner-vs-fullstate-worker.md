# Experiment Design: Isolating the Supervisor/Planner Layer in the EnvState System

**Repository:** `/Users/john/john-planner-v1` (branch `john-planner-v1`)
**Status:** Design / pre-registration draft. Four prerequisite code changes are required before any run: (1) Arm A `_run_fullstate_worker` path; (2) Arm C worker-swap path; (3) benchmark flag plumbing + a mandatory token-bucket split + a shared global action cap; (4) port `response_text()` from `src/envstate/llm_response.py` into the legacy `src/planner.py` so the bare-ReAct Arm 0 shares identical model-compat with the other arms. All specified below.
**Date:** 2026-06-09
**Revision:** v3 — adds **Arm 0 (bare ReAct)**, the pre-EnvState baseline (legacy ReAct planner, no world model / Maintainer / host-probe certification / Supervisor), as the floor the whole research program must clear. v2 had already resolved the single-variable-integrity problem (former 2-arm design conflated three changes) by adding **Arm C (matched-context planner)** so the planner is the genuinely isolated variable. See the "Changes from critique" appendix (§12).

---

## 0. Reading guide / what changed in v3

**v3 (this revision)** adds a **fourth arm, Arm 0 = "bare ReAct"** — the original pre-EnvState agent, the baseline the entire EnvState+Supervisor program exists to beat. **Terminology, made explicit:** the legacy loop *already* uses `src/planner.py` `plan()` — the ReAct thought→action generator invoked at `agent.py:1200,1204`. That is the *ReAct planner*, **not** the EnvState Supervisor. So Arm 0 is defined by what it **lacks**: no EnvState world model, no Maintainer, no host-probe certification, no Supervisor task-decomposition — a ReAct planner emitting thought→action→observation directly. v3 also: sources Arm 0 as the **current branch with all flags off** (single-variable bare baseline; pristine `184a9e3` is only an optional secondary sanity arm — see §2/§8); makes the **`src/planner.py` model-compat port** a listed prerequisite so all four arms share identical `response_text()` plumbing; reframes the comparison as a **four-arm ladder** (§1.5) with **0-vs-B** as the headline ("does the whole stack beat the original agent?"); maps Arm 0's `--steps` budget to the same 180 executed-action ceiling; and adds a **Holm-Bonferroni family-wise correction** over the now-multiple pre-registered McNemar contrasts (§6).

**v2** added a **third arm (Arm C)** that holds the worker context+prompt identical to Arm A but keeps the planner, making **A-vs-C the clean planner ablation** (the v1 2-arm A-vs-B "planner ablation" was confounded across three coupled changes: planner presence, worker context breadth, worker prompt). Other v2 fixes (all retained): a shared global executed-action cap (real budget parity), a **mandatory** `supervisor`/`worker` token-bucket split (so planner cost is measurable), a test-command comparability gate on the primary metric, a shared/parameterized interruption policy, reclassifying behavior-dependent counts from "controls" to "outcomes," and an N recomputed from a real discordance pilot.

---

## 1. Motivation & Hypotheses

### 1.1 The scientific question

The current `--enable-supervisor` system layers a **Supervisor/Planner** (`src/envstate/supervisor.py`) on top of a bounded ReAct **Worker** (`src/envstate/worker.py`). The Planner alone consults the full host-certified world model, decomposes setup into a sequence of narrow `TaskSpec`s, and hands the Worker only a curated `build_task_brief` digest plus its own last 3 observations (`worker.py:62,72`). The Worker **never** sees the `EnvStateSnapshot` — the only snapshot consumers are the Supervisor (`supervisor.py:89` `next_task` / `render_planning_view`) and the Maintainer (`maintainer.py:86`).

We want to know two things, at two altitudes:

1. **Headline (does the program clear the floor?):** does the whole EnvState+Supervisor stack beat the **original, pre-EnvState bare-ReAct agent** at all? This is **Arm 0 vs Arm B**. Arm 0 (§2.0) is the legacy ReAct loop with **no** EnvState world model, **no** Maintainer, **no** host-probe certification, and **no** Supervisor task-decomposition — note it *does* run the `src/planner.py` ReAct planner (`plan()` at `agent.py:1200,1204`), which is the thought/action generator, NOT the EnvState Supervisor. If the full stack cannot beat bare ReAct, none of the finer ablations matter.
2. **Mechanism (where does the value come from?):** **does the Planner layer (task decomposition + ordered phase sequencing) add value, holding the worker's context and prompt fixed?**

The v1 framing ("remove the planner and give the worker the full world model + RCA prompt") bundled three changes. We keep that composite system as one arm but add control arms so each layer is testable in isolation:

- The **value of adding the EnvState layer at all** is isolated against the bare baseline (**Arm 0 vs {A, B, C}**), with **Arm 0 vs Arm B** the headline whole-stack contrast and **Arm 0 vs Arm A** the "host-certified world-model + Maintainer (no planner) vs bare ReAct" contrast.
- The planner's contribution is isolated by comparing two systems that are identical except for the planner (**Arm A vs Arm C**).
- The worker-engineering contribution (full context + RCA prompt vs narrow brief + bounded prompt) is isolated by comparing two systems identical except for that (**Arm B vs Arm C**).

### 1.2 Independent variable(s)

**Primary IV — presence of the Supervisor/Planner layer**, which exclusively owns four things today:

1. **Task selection** — `Supervisor.next_task(snapshot, ledger, budget)` is the only place the full snapshot is consulted to choose the next unit of work (`supervisor.py:89-104`).
2. **Phase sequencing** via the ordered 6-phase `SETUP_PHASES` pipeline (`supervisor.py:8-14`), re-stated in the planner prompt (`supervisor.py:27-29`).
3. **The `RenderedPlanningView`** — the trust-tagged projection of the world model shown ONLY to the Supervisor (`render_planning_view`, `supervisor.py:44-75`).
4. **The `TaskSpec`** contract that scopes the Worker (`SUPERVISOR_SYSTEM_PROMPT` emit-block `supervisor.py:33-37`, `parse_task_spec` `supervisor.py:78-80`).

**Secondary IV (separately tested via B-vs-C) — worker context breadth + prompt:** narrow `build_task_brief` + `WORKER_SYSTEM_PROMPT` (`worker.py:90-119`) vs full `render_fullstate_view` + `FULLSTATE_WORKER_SYSTEM_PROMPT`.

### 1.3 Primary metric

**Clean-room-verified test-pass rate** — operationally, the benchmark's per-instance `environment_build_success` (EBSR), which the harness rebuilds the synthesized Dockerfile from scratch and re-runs the verified test command to compute (`environment_build_success = dockerfile_generation_success AND test_execution AND all_test_commands_effective`; conjunction at `run_repo2run_benchmark.py:3533-3540`, also rendered in the per-instance report at `:657-658,:787-788`). This is the anti-hollow-success outcome (§4).

**Why this metric is what lets Arm 0 join cleanly.** EBSR is computed by the harness **outside and downstream of the agent**: it judges the *output Dockerfile*, not the path that produced it. It is agent-agnostic. The EnvState arms B/C/A additionally run the in-agent `--enable-cleanroom` gate, but that gate is a *mechanism*, not the yardstick — the yardstick is the harness rebuild applied identically to all four arms. Arm 0 has no `--enable-cleanroom` (it predates EnvState entirely), yet it emits a Dockerfile and is scored on the **same external EBSR scale** with **no flag needed**. So the primary outcome is uniform across all four arms by construction.

### 1.4 Hypotheses (PRIMARY metric, EBSR)

- **H0 (whole stack, headline):** The EnvState+Supervisor stack does not change EBSR relative to bare ReAct. In the paired **0-vs-B** McNemar table the discordant cells are symmetric (OR = 1).
- **H1 (whole stack, two-sided):** EBSR differs between Arm 0 and Arm B (OR ≠ 1).
- **H0 (planner):** The planner does not change EBSR holding worker context/prompt fixed. In the paired **A-vs-C** McNemar table the discordant cells are symmetric (OR = 1).
- **H1 (planner, two-sided):** EBSR differs between A and C (OR ≠ 1).
- **H0 (world-model/Maintainer):** Host-certified world model + Maintainer (no planner) does not change EBSR relative to bare ReAct — the **0-vs-A** contrast (OR = 1); two-sided H1.
- **H0' / H1' (worker engineering):** same, for the **B-vs-C** contrast.
- These four pre-registered contrasts (0-vs-B, 0-vs-A, A-vs-C, B-vs-C) form a family; family-wise error is controlled by Holm-Bonferroni (§6.1).
- **Directional expectations** (stated for interpretation, tested two-sided): the full stack should beat bare ReAct (0 < B) — if not, the program has not cleared its floor. Within the stack, the planner helps most on the hard tail (`paper_build_success=False`) where ordered phase sequencing prevents papering over a symptom one layer above its cause; it may cost more tokens than it saves. The competing prior is that a single worker with the full certified world model + layered-RCA prompt matches the planner, so the planner costs Supervisor tokens for no benefit. **Crucially, only A-vs-C can adjudicate the planner**; 0-vs-B and A-vs-B confound it with the world-model/Maintainer layer and/or worker engineering.

### 1.5 The four-arm comparison ladder

The four arms decompose the path from the original agent to the full system as **bare → +world-model/Maintainer (0→A) → +planner (A→C)**, with the worker-engineering (prompt/context) axis as the orthogonal **B↔C** comparison. Each pairwise contrast is a pre-registered, paired McNemar test (§6).

| Contrast | What it isolates | Role |
|---|---|---|
| **0 vs B** | The whole EnvState+Supervisor stack vs the original agent | **HEADLINE** — does the program clear its floor? |
| **0 vs A** | Host-certified world model + Maintainer (no planner) vs bare ReAct | value of the certified world model alone |
| **A vs C** | The Supervisor/planner's isolated marginal value (worker context/prompt held fixed) | the clean planner ablation (existing) |
| **B vs C** | Worker full-state context + RCA-prompt engineering, holding the planner fixed | the worker-engineering ablation (existing) |
| **0 vs {A, B, C}** | Value of adding the EnvState layer at all | program-level framing |

Read as a ladder: **bare ReAct (0)** → add the host-certified world-model + Maintainer but no planner (**A**) → add the planner on top of the matched worker (**C**); **B** is **C** with the worker stripped back to the narrow brief + bounded prompt. So 0→A→C climbs the layer ladder and B↔C moves the prompt axis.

---

## 2. The Four Arms

The switch separating Arm A from Arm B is the `run()` dispatch at `agent.py:990-993`. **Arm 0 is the legacy `run()` body itself** — reached when none of `--enable-supervisor` / `--enable-fullstate-worker` / `--enable-envstate` / `--enable-cleanroom` is passed, so the dispatch falls through to the original ReAct loop driving `src/planner.py` `plan()` (`agent.py:1200,1204`). Arm C is Arm B's `_run_supervisor` with the Worker's planner object swapped. Everything below the dispatch in the EnvState arms — observer, Maintainer, probes, ACL, snapshot threading, clean-room — is shared byte-for-byte across A/B/C; Arm 0 deliberately has none of it.

| | EnvState layer? | Planner present? | Worker context | Worker prompt | Role |
|---|---|---|---|---|---|
| **Arm 0** (no EnvState flags) | **No** (bare ReAct) | ReAct `plan()` only (no Supervisor) | n/a — single ReAct loop | legacy `src/planner.py` prompt | **floor / baseline** |
| **Arm A** (`--enable-fullstate-worker`) | Yes | **No** (no Supervisor) | full `render_fullstate_view` | `FULLSTATE_WORKER_SYSTEM_PROMPT` (layered RCA) | treatment |
| **Arm B** (`--enable-supervisor`) | Yes | Yes | narrow `build_task_brief` | `WORKER_SYSTEM_PROMPT` (bounded task) | control = current system |
| **Arm C** (`--enable-supervisor --fullstate-worker-prompt`) | Yes | Yes | full `render_fullstate_view` | `FULLSTATE_WORKER_SYSTEM_PROMPT` | matched-context planner |

- **0 vs B** = whole-stack headline (does the program beat the original agent?).
- **0 vs A** = value of the host-certified world model + Maintainer without the planner.
- **A vs C** = the clean planner ablation (only the planner differs).
- **B vs C** = the worker-engineering ablation (only context+prompt differ).
- **A vs B** = within-stack whole-system comparison (reported, but NOT a planner verdict).

### 2.0 ARM 0 = BARE ReAct = the pre-EnvState baseline (floor)

**Flag:** *none of the EnvState flags* — run with `--enable-supervisor` / `--enable-fullstate-worker` / `--enable-envstate` / `--enable-cleanroom` **all absent**. The `run()` dispatch (`agent.py:990-993`) checks only `enable_supervisor` (and, with v3, `enable_fullstate_worker`); with both off it falls through to the **legacy `run()` body** — the original ReAct loop calling `self.planner.plan(...)` at `agent.py:1200,1204`.

**Defined by what it LACKS.** Arm 0 *does* use a planner — `src/planner.py` `plan()`, the ReAct thought→action→observation generator. That is **not** the EnvState Supervisor. Arm 0 has **no EnvState world model** (`EnvStateSnapshot` never built — `--enable-envstate` is off, so the `ActionLedger`/snapshot machinery at `agent.py:164-166` never lights up), **no Maintainer**, **no host-probe certification** (probes/ACL never run), and **no Supervisor task-decomposition** (no `TaskSpec`, no phase sequencing). A ReAct planner emits thought→action→observation directly against raw shell output. This is the floor the whole research program must clear.

```
   ┌────────────────────────────────────────────────────────────────────────┐
   │  legacy DockerAgent.run()  (UNCHANGED; reached with all EnvState flags   │
   │  OFF)  — bounded by --steps (ReAct steps); NO orchestrator, NO Supervisor│
   │  NO snapshot/Maintainer/probes/ACL/clean-room                            │
   └───────────────┬────────────────────────────────────────────────────────┘
                   │  each ReAct step:
   raw last observation ───────────►┌──────────────────────────────────┐
   (shell stdout/stderr, no          │  src/planner.py  plan()           │
    certified world model)           │  (agent.py:1200,1204)             │
                                     │  legacy WORKER/ReAct prompt        │
                                     └──────────────┬────────────────────┘
                                                    │ one shell action
                                                    ▼
                                    self.sandbox.execute → raw observation
                                    (NO observer, NO Maintainer, NO probe)
                                                    │
   finalize: legacy synthesis path → Dockerfile emitted (NO _verify_cleanroom_or_fail)
   PRIMARY METRIC: harness rebuilds & scores EBSR externally (§1.3, §4.1) — no flag needed
```

**Sourcing Arm 0 — RECOMMENDED: current branch, FLAGS-OFF (not pristine `184a9e3`).** "The bare ReAct agent at the base of `radical`" is commit **`184a9e3`** (verified: `radical`'s tip is `184a9e3`, and `john-planner-v1` forked from exactly there). Two shared-code robustness fixes sit between that fork and the EnvState work: **`42e7a02`** (tolerate `None` completion content) and **`0ef7e88`** (reject stale test evidence). The **primary** Arm 0 is therefore the **current `john-planner-v1` branch with all EnvState flags off** — i.e. "legacy ReAct + robustness-only fixes (`42e7a02`, `0ef7e88`)." Those two fixes do not change the ReAct *strategy*, so flags-off shares the **same harness, model-compat plumbing, dataset, model, and budget** as the other arms; the only difference is the flags. That makes it the scientifically correct single-variable bare baseline.

- **Why not pristine `184a9e3`?** It introduces a **cross-commit confound** — it carries different code beyond the agent loop and predates the model-compat fixes. Running it would conflate "bare vs EnvState" with "old code vs new code." List it only as an **OPTIONAL secondary "pristine bare" sanity arm** to check whether the `42e7a02`/`0ef7e88` deltas matter on EBSR (§8 Threat #12). It is **not** the primary baseline.

**Model-compat prerequisite (CRITICAL for Arm 0 fairness — §3.0).** The legacy planner `src/planner.py` reads `response.choices[0].message.content` directly (`src/planner.py:202`) and was **NOT** given the `reasoning` fallback: commit `9814edc` wired `response_text()` (from `src/envstate/llm_response.py`) only into the EnvState roles (worker/supervisor/maintainer). So under **MiniMax-via-OpenRouter** Arm 0 would receive empty `content` and collapse for reasons unrelated to "bare vs EnvState." **Prerequisite:** port `response_text()` into `src/planner.py` so all four arms share identical model-compat, OR run the whole experiment on **MiniMax-direct** creds where `content` populates. This is a listed deliverable (§3.0, §11).

**Budget parity (§3.5).** Arm 0 is bounded by `--steps` (ReAct steps); the EnvState arms by the shared 180 executed-action cap. Map Arm 0's `--steps` so its total executed-action budget equals 180 (≈1 step ≈ 1 executed action). Report the **realized** action count (§4.2) so any difference is visible, not assumed.

### 2.1 ARM B = CONTROL = current full system

**Flag:** `--enable-supervisor --enable-cleanroom`
(`--enable-supervisor` auto-enables `--enable-envstate` at `agent.py:159`; `--enable-cleanroom` is independent and OFF by default — must be passed explicitly.)

```
                 ┌─────────────────────────────────────────────────────────┐
                 │  EnvStateOrchestrator.run()  (orchestrator.py:60-83)      │
                 │  loop while tasks_completed < max_tasks (=max_steps=30)   │
                 │  + NEW shared global action cap (§3.5) breaks at 180      │
                 └───────────────┬─────────────────────────────────────────┘
                                 │ budget={steps_remaining}
       render_planning_view      ▼
   (snapshot→trust-tagged) ┌──────────────┐  one TaskSpec JSON
   ───────────────────────►│  SUPERVISOR  │──────────────────┐
   FULL EnvStateSnapshot    │ next_task()  │ (temp=0)          │ task_spec
                            │ supervisor.py│                  ▼
                            └──────────────┘        ┌───────────────────────┐
                            build_task_brief(spec)  │  WORKER (max_actions=6)│
                            = NARROW digest ───────►│  run_task()  worker.py │
                            (+last 3 observations)  │  WORKER_SYSTEM_PROMPT  │
                                                    └──────────┬────────────┘
                                                               │ one shell action
                                                               ▼
   ┌───────────────────────────────────────────────────────────────────────────┐
   │ SHARED HOST PIPELINE (identical in all arms) — observer (agent.py:792-842)  │
   │  executor=sandbox.execute → record ActionEvent → advance_revision on mutate │
   │  → Maintainer.interpret(snapshot, task_spec, event, log) (maintainer.py:75) │
   │  → run_probe + certify_probe_result through ACL (probes.py, acl.py)         │
   │  → returns NEW immutable EnvStateSnapshot, threaded onto self               │
   └───────────────────────────────────────────────────────────────────────────┘
                                                               │
   finalize: _auto_finalize_from_verified_tests → _finalize_supervisor_artifacts
             → _verify_cleanroom_or_fail (HARD GATE, agent.py:917,922-974)
```

**Budgets (Arm B):** orchestrator loop bounds on `tasks_completed >= max_tasks` (= `max_steps` = 30) OR the **NEW shared global executed-action cap of 180** (§3.5), whichever fires first. The legacy `_step` counter never bounded the loop (verified `orchestrator.py:45,54,64-67`); v2 adds the global cap so all arms share a true hard action ceiling. Plus ≤30 Supervisor LLM calls and one Maintainer call per failing/mutating action.

### 2.2 ARM A = TREATMENT = ablated system (planner removed)

**Flag:** `--enable-fullstate-worker --enable-cleanroom`
(auto-enables `--enable-envstate` exactly as supervisor does; mutually exclusive with `--enable-supervisor` — §3.1.)

```
   ┌────────────────────────────────────────────────────────────────────────┐
   │  _run_fullstate_worker()  (NEW, agent.py)                                │
   │  ONE planner-less ReAct loop, total_action_budget = 180 (shared cap)     │
   │  NO EnvStateOrchestrator, NO Supervisor instantiated                     │
   └───────────────┬────────────────────────────────────────────────────────┘
                   │  each step:
   render_fullstate_view(snapshot, recent_obs)   single synthetic FULLSTATE_TASK_SPEC
   = FULL certified world model ──────►┌──────────────────────────────────┐
   (requirements w/ status/source/      │  FullStateWorkerPlanner           │
    specifier/required_by/evidence,      │  (no per-task sub-cap; shared     │
    provider_facts, open_failures,       │  interruption policy §3.5/§I1)    │
    stale_evidence, plan_notes,          │  PROMPT: layered RCA base→sys→    │
    revision) + last 3 observations      │  runtime→deps→build→tests         │
                                        └──────────────┬────────────────────┘
                                                       │ one shell action
                                                       ▼
   ┌───────────────────────────────────────────────────────────────────────────┐
   │ SHARED HOST PIPELINE — BYTE-IDENTICAL to Arms B & C (observer 792-842,      │
   │  Maintainer, probes.py, acl.py, snapshot threading) — UNCHANGED             │
   └───────────────────────────────────────────────────────────────────────────┘
                                                       │
   finalize: SAME tail — _auto_finalize_from_verified_tests
             → _finalize_supervisor_artifacts → _verify_cleanroom_or_fail (SAME gate)
```

### 2.3 ARM C = MATCHED-CONTEXT PLANNER (the control that isolates the planner)

**Flag:** `--enable-supervisor --fullstate-worker-prompt --enable-cleanroom`

Arm C is **exactly Arm B's `_run_supervisor`** — same `Supervisor`, same `EnvStateOrchestrator`, same phase sequencing, same `TaskSpec` flow — with **one** change: the `Worker`'s planner object is `FullStateWorkerPlanner` (the same prompt + full-snapshot rendering Arm A uses) instead of `LlmWorkerPlanner`. The worker still receives a `TaskSpec` from the Supervisor (so the planner's task decomposition is preserved), but it *also* sees the full certified snapshot and runs under the RCA prompt. This makes the **A↔C delta = exactly the planner** (both have fullstate worker + RCA prompt; A lacks the planner, C has it) and the **B↔C delta = exactly the worker context/prompt** (both have the planner).

```
   _run_supervisor(... worker_planner=FullStateWorkerPlanner ...)   # one-line swap, §3.8
   Supervisor → TaskSpec → Worker(planner=FullStateWorkerPlanner) → SHARED HOST PIPELINE
   (orchestrator, observer, Maintainer, probes, ACL, clean-room: all byte-identical to Arm B)
```

Arm C is cheap to build: it reuses the orchestrator and `_run_supervisor` untouched except for which planner object the `Worker` is constructed with. See §3.8.

### 2.4 What is held constant

Arms A/B/C share the entire EnvState host pipeline byte-for-byte (the columns below). **Arm 0 deliberately lacks that whole pipeline** — it is the floor, so the EnvState components are ABSENT by design, not merely off. What Arm 0 *does* share with the others is the harness, dataset, model, model-compat plumbing (after the §3.0 port), the 180-action budget (mapped via `--steps`), and the external EBSR yardstick.

| Component | Arm 0 | Arm A | Arm B | Arm C | Identical? |
|---|---|---|---|---|---|
| Maintainer (`maintainer.py`) | **ABSENT** | per-action interpret | same | same | A/B/C **byte-identical**; Arm 0 lacks it (floor) |
| Host probes (`probes.py`) | **ABSENT** | via observer | via observer | via observer | A/B/C **Yes**; Arm 0 lacks them |
| ACL trust boundary (`acl.py`) | **ABSENT** | certify/advance_revision | same | same | A/B/C **Yes**; Arm 0 lacks it |
| Observer (`agent.py:_build_observer`) | **ABSENT** | shared closure | shared closure | shared closure | A/B/C **Yes** |
| EnvStateSnapshot world model (`types.py`) | **ABSENT** | threaded per action | same | same | A/B/C **Yes**; Arm 0 has no world model |
| Clean-room (`_verify_cleanroom_or_fail`, `cleanroom.py`) | **ABSENT** (external EBSR instead) | hard gate | hard gate | hard gate | A/B/C **Yes**; Arm 0 judged by harness EBSR (§1.3) |
| Finalization tail (`_finalize_supervisor_artifacts`) | legacy synthesis | shared | shared | shared | A/B/C **Yes** |
| Shared global executed-action cap (180) | via `--steps` (§3.5) | yes | yes (NEW §3.5) | yes (NEW §3.5) | **Matched & enforced** |
| Interruption policy (shared parameterized fn §3.5/§I1) | legacy guard (own) | yes | yes | yes | A/B/C **Yes**; Arm 0 keeps its legacy guard |
| Model / temperature | minimax-m2.7, temp=0 | same | same | same | **Yes** |
| Model-compat (`response_text` reasoning fallback) | **after §3.0 port** | yes (`9814edc`) | yes | yes | **Yes — once §3.0 lands** |
| Harness / dataset / EBSR yardstick | same | same | same | same | **Yes** |
| EnvState layer (world-model/Maintainer/probes/Supervisor) | **ABSENT** | present | present | present | 0-vs-{A,B,C} IV |
| **Supervisor/Planner** | absent | **ABSENT** | present | present | A-vs-C IV |
| Worker context + prompt | legacy ReAct | full + RCA | narrow + bounded | full + RCA | B-vs-C IV |

The within-stack pairwise contrasts (A-vs-C, B-vs-C) each move exactly one of the two within-stack IVs. The 0-vs-* contrasts move the EnvState layer as a whole; **0-vs-B** is the program-level headline, not a single-component verdict.

---

## 3. Implementation Spec (Arm 0 model-compat port + Arms A & C + shared plumbing)

Minimal-diff, default-OFF. `worker.py`/`supervisor.py`/`orchestrator.py`/`maintainer.py`/`probes.py`/`acl.py`/`types.py`/`serde.py`/`cleanroom.py` are **imported, never behaviorally changed**, with three surgical, behavior-preserving exceptions noted explicitly: (a) the orchestrator gains an optional global-action-cap break (§3.5); (b) the interruption guard is refactored into a shared parameterized function (§3.5/§I1); (c) `_run_supervisor` gains a `worker_planner` selector (§3.8). Arm B's behavior is unchanged when the new flags are OFF. Arm 0 requires **no new flags** (it is flags-off legacy `run()`), but does require the one-time model-compat port in §3.0.

### 3.0 Arm 0 prerequisite: port `response_text()` into `src/planner.py` (model-compat uniformity)

This is the single most important Arm 0 fairness change. The legacy ReAct planner reads completion text directly at `src/planner.py:202` (`content = response.choices[0].message.content or ""`) and has **no reasoning fallback**. Commit `9814edc` added `response_text()` (`src/envstate/llm_response.py:5`, which prefers `choices[0].message.content` and falls back to a `reasoning` attribute / `model_extra["reasoning"]`) but wired it only into the EnvState worker/supervisor/maintainer. Under MiniMax-via-OpenRouter the assistant text arrives in `reasoning` with empty `content`, so an un-ported Arm 0 would parse no Action and collapse — a failure mode caused by model-compat, not by "bare vs EnvState," fatally confounding 0-vs-B.

**Either** (preferred, single-knob) port `response_text()` into `src/planner.py` so the legacy planner uses the same content-extraction as all EnvState roles:
```python
from src.envstate.llm_response import response_text
# at src/planner.py:202, replace:
#   content = response.choices[0].message.content or ""
content = response_text(response)
```
**Or** run the entire 4-arm experiment on **MiniMax-direct** credentials where `content` populates natively (then no port is needed, but the port is still cheaper and avoids a creds dependency). Pre-register which path is used; if the port is taken, it is a behavior-preserving change when `content` is already non-empty (identical to the EnvState roles). This is a listed deliverable (§11).

### 3.1 New CLI flags + wiring

**Flag 1 — `--enable-fullstate-worker`** (Arm A; additive/opt-in; avoid a negation like `--disable-planner`).
**Flag 2 — `--fullstate-worker-prompt`** (Arm C; only meaningful with `--enable-supervisor`).

- **argparse** — add adjacent to the EnvState flags:
  ```python
  parser.add_argument("--enable-fullstate-worker", action="store_true",
      help="ARM A: single planner-less ReAct worker ingesting the full certified "
           "EnvState snapshot each step, layered root-cause analysis. Shared global "
           "action cap = --steps * 6. Mutually exclusive with --enable-supervisor.")
  parser.add_argument("--fullstate-worker-prompt", action="store_true",
      help="ARM C: with --enable-supervisor, swap the Worker to the fullstate "
           "RCA prompt + full-snapshot context (isolates the planner vs Arm A).")
  ```
- **constructor kwargs** — add `enable_fullstate_worker=False, fullstate_worker_prompt=False,` next to `enable_cleanroom=False` in `DockerAgent.__init__` (near `agent.py:126-128`).
- **flag attributes** — extend `agent.py:158-159` (today: `self.enable_supervisor = enable_supervisor` then `self.enable_envstate = enable_envstate or enable_supervisor`):
  ```python
  self.enable_fullstate_worker = enable_fullstate_worker
  self.fullstate_worker_prompt = fullstate_worker_prompt
  self.enable_envstate = enable_envstate or enable_supervisor or enable_fullstate_worker
  ```
  This lights up the `ActionLedger` build (`agent.py:164-166`) and `env_container_id` automatically.
- **constructor call** — pass `enable_fullstate_worker=args.enable_fullstate_worker, fullstate_worker_prompt=args.fullstate_worker_prompt,` at the `DockerAgent(...)` construction site.
- **`run()` dispatch** — at `agent.py:990-993`, after the supervisor check (supervisor must stay first so `--enable-supervisor --fullstate-worker-prompt` routes to `_run_supervisor` = Arm C):
  ```python
  if getattr(self, "enable_supervisor", False):
      return self._run_supervisor(max_steps=max_steps, keep_container=keep_container)
  if getattr(self, "enable_fullstate_worker", False):
      return self._run_fullstate_worker(max_steps=max_steps, keep_container=keep_container)
  ```
- **Mutual-exclusion guard** — right after `args = parser.parse_args()`:
  ```python
  if args.enable_supervisor and args.enable_fullstate_worker:
      parser.error("--enable-supervisor and --enable-fullstate-worker are different arms; pick one.")
  if args.fullstate_worker_prompt and not args.enable_supervisor:
      parser.error("--fullstate-worker-prompt requires --enable-supervisor (Arm C).")
  ```

Routing: Arm B = `--enable-supervisor` (no prompt flag); Arm C = `--enable-supervisor --fullstate-worker-prompt`; Arm A = `--enable-fullstate-worker`. `--enable-envstate` alone keeps legacy `run()` in shadow mode, unchanged.

### 3.2 Arm A: bypass the Orchestrator; drive one Worker loop over the full snapshot

`EnvStateOrchestrator.run()` is hardwired to call `supervisor.next_task` (`orchestrator.py:69`), so Arm A does NOT use it. `_run_fullstate_worker` drives the loop directly, reusing the same `executor` + `observer` step closure.

**Single synthetic TaskSpec.** The observer (`agent.py:801`) and `Maintainer.interpret` (`maintainer.py:75-87`, embedding `task_spec` via `build_maintainer_input`) both require a `task_spec` dict. Arm A passes ONE fixed synthetic spec for the whole run:
```python
FULLSTATE_TASK_SPEC = {
    "task_id": "fullstate",
    "phase": "Whole-Environment Root-Cause Configuration",
    "goal": "Configure the repository environment end-to-end: install all dependencies "
            "and make the test suite runnable, diagnosing the root cause of each failure "
            "across the full stack.",
    "relevant_state": [], "constraints": [], "allowed_actions": [],
    "success_criteria": ["The project's test command runs and the suite is runnable."],
    "stop_conditions": [],
}
```
**Confound flagged (§M4):** Arm A feeds the Maintainer this constant spec while Arms B/C feed varying narrow specs. The Maintainer *code path* is byte-identical, but its *input* differs, so its interpretations may diverge for reasons orthogonal to the planner. We do not assert "Maintainer held identical"; we treat Maintainer behavior as an outcome to report (§4.2, §I2) and verify in the dry-run that probe-request behavior is comparable.

### 3.3 Full-state snapshot renderer

`render_planning_view` (`supervisor.py:44-75`) is insufficient — it drops `provider_facts` and omits `specifier`/`evidence`/`required_by` detail. New module `src/envstate/fullstate_worker.py`, function `render_fullstate_view(snapshot, recent_observations) -> str`, renders all `EnvStateSnapshot` fields (`types.py:79-88`): `revision`, `container_id`, `base` (image/distro/arch/python), each `requirement` tagged **CERTIFIED-PROBE** (when `source == Source.PROBE` at current revision, `types.py:10`) vs `hypothesis(<source>)` with status/specifier/required_by/evidence, `provider_facts`, `open_failures` (signature, revision span, hypothesis, already_tried), `stale_evidence` ("re-verify if needed"), `plan_notes`, and the last 3 observations. Rebuilt fresh each step from the latest threaded snapshot. **Used identically by Arm A and Arm C** (this is what makes B-vs-C clean).

### 3.4 Systematic layered root-cause worker prompt

New constant `FULLSTATE_WORKER_SYSTEM_PROMPT` in `fullstate_worker.py` (do NOT mutate `worker.WORKER_SYSTEM_PROMPT`, which Arm B uses). Differences vs `worker.py:103-119`: (a) no "ONE bounded task / never change scope" framing; (b) explicit whole-stack layered RCA — `base image → system packages → language runtime → project deps → build → tests` — naming the candidate root-cause layer and justifying from the certified state before each single action; (c) explicit trust rules ("a requirement is TRUE only when CERTIFIED-PROBE at current revision; do NOT re-install/re-check [PRESENT] CERTIFIED-PROBE; focus on REQUIRED/MISSING/UNKNOWN and open failures"); (d) "do not paper over a symptom one layer above its cause." The completion contract is **identical** (`Thought:` / `Action:` / `Final Answer: Success`) so `_extract_worker_action` (`worker.py:125-132`) and `_is_worker_finished` (`worker.py:135-136`) are reused verbatim. **Used identically by Arm A and Arm C.**

### 3.5 Loop termination, budget fairness, and the shared interruption policy

- **Completion:** worker emits `Final Answer: Success` → `_is_worker_finished` → break → finalize.
- **Shared global executed-action cap (C2 fix).** v1's "180 ceiling" was a near-unreachable *task-count* ceiling for Arm B (30 tasks × 6 actions only if the Supervisor emits 30 tasks AND every worker burns all 6). Verified: the orchestrator loop bounds on `tasks_completed >= max_tasks`; the `_step` counter is incremented but never bounds the loop (`orchestrator.py:45,54,64-67`). v2 makes the budget a **real shared knob**: define `GLOBAL_ACTION_BUDGET = max_steps * DEFAULT_MAX_ACTIONS = 30 * 6 = 180` and enforce a 180-action ceiling in **all four arms** (the shared global cap in the EnvState arms A/B/C; Arm 0's `--steps`-mapped ceiling, v3):
  - **Arm 0:** bounded by `--steps` (legacy ReAct step ceiling), with **no** new code. Map `--steps` so Arm 0's total executed-action budget equals 180 — in the legacy loop ≈1 ReAct step ≈ 1 executed action, so set `--steps 180` for Arm 0 (vs `--steps 30` for the EnvState arms, whose 30 tasks × 6 actions = 180). Report Arm 0's **realized** executed-action count (§4.2) so any divergence from 180 is visible, not assumed. (Arm 0 has no orchestrator/global-counter machinery; the `--steps` ceiling is its only budget knob, which is exactly why realized counts are reported.)
  - Arm A: the `run_fullstate_loop` increments a global counter and stops at 180.
  - Arms B & C: thread a global executed-action counter through the orchestrator's `_make_step_fn` and break `EnvStateOrchestrator.run()` when it reaches 180 (a single additive guard inside the existing `while True`, default `None` = disabled so legacy behavior is unchanged). Implementation: add an optional `global_action_budget=None` ctor arg to `EnvStateOrchestrator`; the step closure increments a shared counter; after `worker.run_task` returns, break if the counter ≥ budget. Behavior-preserving when `None`.
- **Realized-count reporting + sensitivity (C2).** Both arms also **report the realized executed-action count** (§4.2). Because Arm B/C can stop early via `no_more_tasks` (Supervisor emits no TaskSpec, `orchestrator.py:72-74`) — a termination mode with no analog in Arm A — we additionally run a **sensitivity analysis** capping Arm A at the *realized median* action count of its paired comparator if A systematically consumes more budget. The `no_more_tasks` early-stop rate is reported per arm as an asymmetry diagnostic.
- **Shared, identically-parameterized interruption policy (I1 fix).** v1 said Arm A "keeps Arm B's safety guards," but `should_interrupt`'s repeated-identical-failure guard (`worker.py:42-44`) inspects only the *current task's* `observations`; over Arm A's single contiguous 180-action history it would fire across what would have been task boundaries in Arm B (or, if naively reset, never fire). To keep firing semantics identical, refactor the guard into a shared function `interruption_decision(recent_observations_window, action)` parameterized by a fixed **rolling window of the last N=3 observations** in *all* arms (matching the per-task `observations[-3:]` slice the worker already passes, `worker.py:72`). Arm B/C call it with the same window; Arm A calls it with a rolling last-3 window rather than whole-history. The pin-edit guard (`_looks_like_pin_edit`, `worker.py:22-26`) is unchanged and shared. Interruption firing-rate is added to the control-check report (§7) and asserted comparable in the dry-run.

### 3.6 Keeping Maintainer / probes / EnvState / clean-room identical

- **Observer:** `self._build_observer(maintainer)` exactly as `_run_supervisor` (`agent.py:792-842, 873`) — no edits, all arms.
- **Maintainer:** `Maintainer(client=self.client, model=self.model)` identically (`agent.py:852`); Arm A passes `FULLSTATE_TASK_SPEC`, Arms B/C pass per-task specs — same prompt, same code path (input difference flagged §3.2/§I2/§M4).
- **Step closure (Arm A):** inline the identical closure body from `EnvStateOrchestrator._make_step_fn` (`orchestrator.py:47-58`) — increment global `_step`/action counter, raw-execute via `self.sandbox.execute`, run observer, thread new snapshot onto `self`. Inline (not reuse) to avoid coupling Arm A to the orchestrator's supervisor-calling `run()`.
- **Clean-room:** finalize via the SAME `_finalize_supervisor_artifacts` → `_verify_cleanroom_or_fail` (`agent.py:909-974`), gated on `self.enable_cleanroom` exactly as Arm B. Verified: legacy `run()` does NOT call `_verify_cleanroom_or_fail`/`_finalize_supervisor_artifacts` (they appear only inside `_run_supervisor`), so building Arm A on the supervisor finalization tail — NOT legacy `run()` — is required to keep the gate symmetric (Threat #2).
- **Usage accounting:** see §3.7 for the **mandatory** bucket split.

### 3.7 Mandatory token-bucket split (C3 fix — now required, not optional)

Verified: `_record_supervisor_path_usage` maps **Supervisor → `planner` AND Worker → `planner`** (same bucket; `_run_supervisor` passes `"planner"` for both the worker `on_usage` and the orchestrator `on_usage`, `agent.py:864,874`), Maintainer → `reflection` (observer, `agent.py:821`); the docstring admits the collision (`agent.py:979-985`). With the buckets conflated, **`total` cannot isolate the planner's cost** (Arm A's fullstate worker re-sends the whole snapshot every step → more *worker* prompt tokens, while Arms B/C add Supervisor calls). A `total`-only comparison conflates planner overhead with fullstate-context overhead — exactly the C1 confound on the cost axis.

**Fix (REQUIRED before the sweep):** split the `planner` bucket into `supervisor` + `worker` in `RunTokenLedger` (the bucket set lives in the run token ledger; locate via `grep -n "planner" src/observation_compressor.py` or wherever `RunTokenLedger` defines its buckets) and route:
- `Supervisor.next_task` usage → `supervisor` bucket (`_record_supervisor_path_usage("supervisor", usage)` in the orchestrator `on_usage` lambda).
- `LlmWorkerPlanner` / `FullStateWorkerPlanner` usage → `worker` bucket.
- Maintainer → `reflection` (unchanged).

Consequences that make the experiment answerable:
- Arm A's `supervisor` bucket = 0 by construction.
- **Arm C's `supervisor` bucket = the planner's marginal cost** (C's worker = A's worker, so C−A on `supervisor` is pure planner overhead).
- Per-step worker prompt inflation is visible in the `worker` bucket (B vs C / A vs C).

This is a ~10-line change; without it the cost half of every decision rule is uninterpretable.

### 3.8 Arm C: one-line worker-planner swap in `_run_supervisor`

`_run_supervisor` (`agent.py:844-906`) constructs `Worker(planner=LlmWorkerPlanner(...), max_actions=6)`. Add a selector so Arm C swaps in the fullstate planner while leaving the orchestrator, observer, Maintainer, finalization, and clean-room untouched:
```python
if getattr(self, "fullstate_worker_prompt", False):
    from src.envstate.fullstate_worker import FullStateWorkerPlanner
    worker_planner = FullStateWorkerPlanner(
        self.client, self.model,
        get_snapshot=lambda: self.env_snapshot,   # full-snapshot context each step (Arm C)
        on_usage=lambda usage: self._record_supervisor_path_usage("worker", usage),
    )
else:
    from src.envstate.worker import LlmWorkerPlanner
    worker_planner = LlmWorkerPlanner(
        self.client, self.model,
        on_usage=lambda usage: self._record_supervisor_path_usage("worker", usage),
    )
worker = Worker(planner=worker_planner, max_actions=DEFAULT_MAX_ACTIONS)
```
For Arm C, `FullStateWorkerPlanner.next_action(task_brief, recent_observations)` must accept the same signature the `Worker` calls (`worker.py:72`) but build its user message from `render_fullstate_view(self.get_snapshot(), recent_observations)` **plus** the per-task `task_brief` (so the planner's TaskSpec scoping is preserved alongside the full snapshot). This is the only difference between Arm C and Arm B. `env_snapshot` is threaded onto `self` by the observer each step (`agent.py:872-879` path), so `get_snapshot` always returns the latest certified snapshot. Also route the orchestrator's Supervisor `on_usage` → `"supervisor"` (§3.7).

### 3.9 Functions to add / modify

**New file `src/envstate/fullstate_worker.py`:** `FULLSTATE_WORKER_SYSTEM_PROMPT`; `render_fullstate_view(snapshot, recent_observations)`; `class FullStateWorkerPlanner` (mirrors `LlmWorkerPlanner` `worker.py:139-171`: maintains its own ReAct history, reuses `_extract_worker_action`/`_is_worker_finished`, emits usage via `on_usage`; takes an optional `get_snapshot` callable so it can render the full snapshot every `next_action` for both Arm A's direct loop and Arm C's worker); `interruption_decision(recent_window, action)` shared guard (§3.5/I1); `run_fullstate_loop(planner, get_snapshot, step_fn, global_action_budget, interruption_decision)` returning a reused `WorkerReport` (`worker.py:13-19`).

**Modify `agent.py` (additions behind flags):** `_run_fullstate_worker(self, max_steps=30, keep_container=False)` modeled on `_run_supervisor` (`agent.py:844-906`) — same initial snapshot build, same Maintainer, same observer, inlined step closure, `FullStateWorkerPlanner` with `on_usage` → `_record_supervisor_path_usage("worker", ...)`, `run_fullstate_loop(...)` with `global_action_budget = max_steps * DEFAULT_MAX_ACTIONS`, then the SAME finalization tail; the §3.8 Arm-C selector inside `_run_supervisor`; the §3.7 bucket-split routing; the §3.1 flag wiring; the §3.5 dispatch ordering.

**Modify `src/envstate/orchestrator.py` (behavior-preserving):** optional `global_action_budget=None` ctor arg + a shared action counter incremented in `_make_step_fn` + a break in `run()` when the counter ≥ budget. No change when `None`.

**Modify `src/envstate/worker.py` (behavior-preserving refactor):** extract the repeated-failure window logic from `should_interrupt` into the shared `interruption_decision`; `should_interrupt` keeps calling it with the existing per-task slice so Arm B's firing is bit-identical.

**Modify `src/planner.py` (Arm 0 model-compat, behavior-preserving — §3.0):** replace the bare `content = response.choices[0].message.content or ""` at `src/planner.py:202` with `content = response_text(response)` (import from `src/envstate/llm_response.py`) so the legacy ReAct planner shares the reasoning-content fallback all EnvState roles already use. Skip only if the experiment runs on MiniMax-direct creds (§3.0). Identical output when `content` is non-empty.

**Untouched:** `supervisor.py`, `maintainer.py`, `probes.py`, `acl.py`, `types.py`, `serde.py`, `cleanroom.py`, `_build_observer`, `_verify_cleanroom_or_fail`, and the legacy `run()` loop body itself (Arm 0 uses it as-is; only `src/planner.py`'s content extraction is touched, per §3.0).

---

## 4. Metrics

All fields are keys in `<workplace>/agent_run_summary.json` (written by `_write_run_summary` / `_build_run_summary`) unless noted; all arms write the identical schema. The benchmark also writes `results/<id>.json` and `summary.json` (with `metrics.EBSR`).

### 4.1 PRIMARY: clean-room-verified test-pass rate

Operationalized as the harness-level `environment_build_success` (EBSR), computed uniformly OUTSIDE the agent for all **four** arms (conjunction `environment_build_success = dockerfile_generation_success AND test_execution AND all_test_commands_effective` at `run_repo2run_benchmark.py:3533-3540`; rendered in the per-instance report at `:657-658,:787-788`). **This external verification is the key enabler for Arm 0.** Arm 0 has no `--enable-cleanroom`, but the harness already rebuilds each produced Dockerfile and computes EBSR regardless of which agent path emitted it — the verification judges the *output Dockerfile*, not the construction path, so it is agent-agnostic. Arm 0 just emits a Dockerfile and is scored on the same scale, no flag needed. Cross-check (EnvState arms only) against in-agent `cleanroom.passed`:
```
success(repo) = environment_build_success == true     # all four arms, identical external scale
              (robustness check, EnvState arms A/B/C only:
               agent_run_summary.cleanroom.passed == true; Arm 0 has no cleanroom block)
```

**Test-command comparability gate (C4 fix).** `environment_build_success` requires `all_test_commands_effective`, which depends on *which* test command the arm surfaced — an arm verifying a trivial command (`python -c "import pkg"`) can pass EBSR hollowly. v1 only *reported* test-command divergence (Threat #7); v2 makes it a **gate**: capture `verified_test_commands` per arm/repo; for any discordant pair where two arms verified **materially different** test commands, either (a) **exclude the pair from the primary McNemar table** and report it separately, or (b) re-run both arms' synthesized Dockerfiles against a **single canonical test command** (the repo's own, extracted independently) so EBSR targets the same suite. Pre-register option (a) as primary, (b) as a robustness pass. "Materially different" = different test runner/target after trivial-normalization (whitespace, `-q`/`-v` flags); pre-register the normalizer.

**Why not raw build-success.** `configuration_success` / `dockerfile_generation_success` only prove a Dockerfile was synthesized and the agent declared done — a hollow success (the synthesizer is known to drop editable/test installs, so the Dockerfile *builds* but does not reproduce a working test environment). Clean-room (`cleanroom.py:45-94`, `_verify_cleanroom_or_fail` `agent.py:922-974`) is the anti-hollow mechanism: it injects `COPY . <workdir>` (`cleanroom.py:21-42`), rebuilds from scratch (`cleanroom.py:65-67`), re-runs every certified-PRESENT probe (`req.source==PROBE and status=="PRESENT"`, `agent.py:944`), and re-runs `verified_test_commands` requiring `rc==0` (`cleanroom.py:85-92`). A hollow success fails at build (`cleanroom.py:69`) or test (`cleanroom.py:91`). **Note (gaming vector, confirmed `cleanroom.py:73-74`):** `verify_cleanroom` returns `passed=False` when there are NO probes AND NO test commands ("nothing to verify") — so an arm that certifies nothing fails the in-agent gate. Good for the gate, but the **primary outcome is the harness EBSR**, which is why the §4.1 test-command comparability gate is the real protection.

**ESSR convention (paper-faithful, ÷ ALL assigned):**
```
ESSR_primary = (# repos with environment_build_success) / (# repos ASSIGNED)
```
Missing/unparseable `agent_run_summary.json`, or `error != null` with no success → counted as **0**, kept in the denominator. Additionally report `coverage = produced/assigned` and a secondary `ESSR_÷executed`. **Never report ÷executed alone** (the "RATBench ESSR ÷exec deviation" inflation risk).

### 4.2 Secondary metrics

| Metric | JSON field(s) / derivation | Expectation |
|---|---|---|
| Build-success (anti-confounder) | `configuration_success`; harness `dockerfile_generation_success` | open; all 4 arms |
| **Realized executed-action count (budget parity, C2)** | `len(successful_actions)+len(failed_actions)`, or `len(action_ledger)`; Arm 0: legacy ReAct step/action log | report per arm (incl. Arm 0); gate sensitivity |
| `no_more_tasks` early-stop rate (B/C only, C2) | orchestrator `stop_reason` (§4.4 patch) | report asymmetry vs Arm A; N/A Arm 0 |
| # tasks (B/C) | distinct `action_ledger[].task_id`, or `tasks_completed` (§4.4) | A≈1, B/C>1; Arm 0 has no tasks |
| **Per-role token cost (C3)** | `token_usage.{supervisor,worker,reflection,recipe,image_selector,total}.*_tokens` | supervisor: 0=A=Arm 0, C>0=planner cost; Arm 0 has no supervisor/worker/reflection buckets (legacy `planner`/`total` only) |
| Wall-clock | `results/<id>.json → agent_run.duration_seconds` | A likely lower than B/C (no Supervisor round-trips); Arm 0 likely lowest (no EnvState overhead) |
| # maintainer calls (**outcome, not control** — I2) | events with `rc!=0 or mutation_class` (mirrors observer gate `agent.py:816`) | report; behavior-dependent; Arm 0 = 0 (no Maintainer) |
| # env mutations / revision (**outcome**) | events with `mutation_class!=null`; max revision | report; N/A Arm 0 (no snapshot/revisions) |
| Interruption firing-rate (control-check, I1) | count of `interrupted` reports / shared-guard fires | assert comparable across A/B/C in dry-run; Arm 0 uses its own legacy guard (reported, not matched) |
| Failure-recovery | fraction of `failed_actions` whose signature does not recur | tests recovery; all 4 arms |
| Test-effectiveness / divergence (C4) | `test_run_attempts[]`; `verified_test_commands` | gate input (§4.1); all 4 arms |
| Error/crash | `error` (str or null) | → counted as 0; all 4 arms |

### 4.3 Cost-attribution (now clean via §3.7)

With the mandatory bucket split (§3.7): the planner's marginal cost = Arm C's `supervisor` bucket (≈ Arm A's `supervisor` = 0). `total.total_tokens` remains the headline overall cost per arm. Per-bucket `supervisor`/`worker` deltas are now interpretable; pre-register that planner-cost claims use the `supervisor` bucket, not `total`.

### 4.4 Optional instrumentation patches (recommended; none block the primary metric)

1. Capture `orchestrator.run()` result (currently discarded at the `_run_supervisor` `orchestrator.run()` call, `agent.py:880`) into the summary → real `tasks_completed` / `stop_reason` / `final_revision` (needed for the `no_more_tasks` asymmetry diagnostic, C2).
2. Add per-bucket `calls` counter + `wall_clock_seconds` to the summary (else harness `agent_run.duration_seconds`).
3. Persist an `envstate` block (`final_revision`, `n_probe_certified`, `n_open_failures`, `n_requirements`) from `self.env_snapshot` for exact probe/failure counts (probes are read-only and do NOT appear as ActionEvents).

(The bucket split itself, §3.7, is **mandatory**, not in this optional list.)

### 4.5 Extraction approach

A single new script (`scripts/` does NOT exist — confirmed — and must be created) globs `**/agent_run_summary.json` + `results/*.json`, records per repo `(instance_id, arm ∈ {0,A,B,C}, replicate, paper_build_success, environment_build_success, cleanroom.passed, verified_test_commands, total_tokens, supervisor_tokens, worker_tokens, reflection_tokens, n_actions, no_more_tasks_flag, n_tasks_proxy, n_maintainer, interruption_fires, final_rev, duration_seconds, execution_status)`, treats missing/error as success=0, and emits **clean raw artifacts only (CSV + JSON)** — narrative analysis runs downstream via subagents. **Arm 0 rows** carry no `cleanroom`/`supervisor`/`worker`/`reflection`/`final_rev`/`no_more_tasks`/`n_tasks` fields (legacy summary schema: `planner`/`total` buckets only, no EnvState block); the script must tolerate their absence (null), not error.

---

## 5. Dataset & Sampling

### 5.1 Ground-truth dataset facts (verified)

- `datasets/repo2run_table15.json` = **420 instances, 100% `language="python"`** → **language is NOT a usable stratifier; drop it.** Per-instance schema: `instance_id, full_name, repo_url, sha, base_commit, paper_build_success(bool), paper_build_success_label, language, source`. `base_commit == sha` for all (6-char short SHA, e.g. `9ba7b8`).
- `paper_build_success`: **361 True (86%) / 59 False (14%)** — the only meaningful stratifier.
- Fixtures `0425.json` (N=20), `first_5_failed.json` (N=6, incl. paper-failures), `1failed_repair.json`, `2failed_repair.json` are debug/repair sets — fine for the dry-run, useless for inference.

### 5.2 Subset, stratification, N

**Recommended: a stratified random sample drawn from the 420** (not all 420 — cost; not the fixtures — underpowered). v1 proposed N=120 for a 2-arm design. The 4-arm design changes the cost arithmetic (§9) and the power story (§5.4, I3), so:

- **Base sample: N = 120**, stratified on `paper_build_success` proportional to population (**103 success + 17 failure**), OR oversample the hard tail (**90 success + 30 failure**) since the scientific contrast lives in the failures. If oversampling, report **both stratum-weighted and raw**, and pre-register the weighting.
- **N is provisional and re-derived from the discordance pilot (I3, §9.3) before the full sweep.** If observed discordance is low, either shift toward the hard tail or switch the primary frame to equivalence (TOST), rather than inflating N.
- A cheap derived covariate (not a stratifier): dependency-file complexity (line counts of `requirements.txt`/`pyproject.toml`/`setup.py` after shallow clone) — secondary analysis only.

Draw with a fixed RNG seed; persist the chosen IDs to `datasets/ablation_n120_seed42.json` (same `{instances:[...]}` schema) so all four arms run the identical set.

### 5.3 Paired design

Run the **identical N `instance_id` set through ALL FOUR arms** (0, A, B, C). Pairing eliminates per-repo difficulty as a confounder. All pre-registered contrasts (0-vs-B, 0-vs-A, A-vs-C, B-vs-C) are paired on the same repos.

### 5.4 Power reasoning (revised per I3)

McNemar power depends on the count and asymmetry of **discordant pairs** (b, c), not N directly.
- Base EBSR ≈ 0.70 assumed on the sample.
- v1 assumed `p_disc ≈ 0.20-0.30` and a 10:20 split from priors → ~0.80 power at N≈110-130. **This is unvalidated.** The hard tail (`paper_build_success=False`) may fail ~100% in all arms → all-concordant-failure pairs carry zero McNemar information, so the *effective* N collapses toward the paper-success repos, where two strong arms likely agree (low discordance).
- **Therefore (I3 fix):** the dry-run is a **real discordance pilot** (§9.3). Run ~15-20 mixed-stratum repos at R=1 across all four arms, measure the *actual* discordant-pair rate for the pre-registered contrasts (0-vs-B, 0-vs-A, A-vs-C, B-vs-C), and **recompute N from observed `p_disc` before committing** — N is set by the *least-powered contrast in the family* under the Holm-corrected alpha (§6.1). For the headline **0-vs-B** contrast we expect *high* discordance (a fielded stack vs a bare baseline should disagree often), so 0-vs-B is unlikely to be the binding constraint; the binding contrast is more likely the within-stack A-vs-C / B-vs-C. If observed A-vs-C discordance < ~0.15, switch the primary planner frame to **equivalence testing (TOST on the risk difference, pre-registered margin ±2 points)** rather than McNemar superiority; the 0-vs-B superiority frame is retained regardless. Stratify the McNemar analysis by `paper_build_success` and pre-register that all-concordant-failure pairs are reported but carry no test weight.

---

## 6. Statistical Analysis

### 6.1 Tests (pre-register exactly)

**Pre-registered contrast family (4 paired McNemar tests):** **0-vs-B** (headline whole-stack), **0-vs-A** (world-model/Maintainer value), **A-vs-C** (clean planner), **B-vs-C** (worker engineering). Each is an **exact-binomial McNemar's test** on the paired 2×2 EBSR table (exact, not χ²; discordant counts modest). Report cells b, c per contrast. Two-sided.

- **Family-wise multiple-comparison correction (NEW, v3):** because there are now multiple pre-registered pairwise contrasts, apply **Holm-Bonferroni** over the family of 4 to control family-wise error at α = 0.05. Procedure: order the 4 p-values ascending p(1)≤…≤p(4); reject H0(i) iff p(i) ≤ 0.05/(4−i+1) for all j≤i. So the **smallest** p-value is tested at **corrected α = 0.05/4 = 0.0125**, then 0.05/3 ≈ 0.0167, 0.05/2 = 0.025, 0.05/1 = 0.05. Report both raw and Holm-adjusted p-values. The Wilcoxon cost/steps tests (below) are a **separate, descriptive efficiency family** and are not folded into the EBSR Holm family (pre-register this split; report their p-values raw, clearly labeled exploratory).
- **Headline — 0 vs B (whole stack):** does the EnvState+Supervisor stack beat bare ReAct on EBSR? Reported first; if the pilot shows high discordance (expected), this stays a superiority frame even if within-stack contrasts move to TOST.
- **0 vs A (world-model/Maintainer):** host-certified world model + Maintainer (no planner) vs bare ReAct.
- **Planner contrast — A vs C:** the clean planner ablation. If the pilot shows low discordance → **TOST equivalence** on the risk difference (margin ±2 pts) as the pre-registered frame for *this contrast only*.
- **Worker-engineering contrast — B vs C:** "does fullstate context + RCA prompt help, holding the planner fixed?"
- **Reported (not a single-component verdict) — within-stack whole-system (A vs B):** exact McNemar, clearly labeled system-vs-system, **outside** the Holm family (descriptive).
- **Cost / steps / wall-clock:** **Wilcoxon signed-rank** on paired per-repo deltas for `total.total_tokens`, the `supervisor` bucket (planner cost, A/B/C), `worker` bucket, `n_actions`, `duration_seconds` — on each relevant contrast incl. **0-vs-B** (note Arm 0 reports only `planner`/`total` buckets, so cross-arm cost vs Arm 0 uses `total.total_tokens` and `n_actions`). Skewed/bounded-below → signed-rank over paired t-test. **Restrict to the concordant-success subset** (repos both arms in the contrast solved) so efficiency is compared on the same achieved outcome.

### 6.2 Effect sizes (report with every p-value)

- Binary: **odds ratio OR = b/c** + **risk difference (b−c)/N** with 95% CI, plus raw absolute EBSR delta. (For equivalence: the risk-difference CI vs the ±2-pt margin.)
- Cost/steps: **matched-pairs rank-biserial correlation** + **median paired difference with bootstrap 95% CI**.
- **Planner marginal cost** = median of Arm C's `supervisor` bucket per repo (a single interpretable number, §3.7/§4.3), with bootstrap CI.

### 6.3 Nondeterminism handling (revised per I4)

Supervisor (`supervisor.py:97`), Worker (`worker.py:158`), Maintainer (`maintainer.py:93`), and the legacy ReAct planner of Arm 0 (`src/planner.py`) all call at temperature=0 — reduces but does NOT eliminate nondeterminism (provider batching, MoE routing on `minimax-m2.7`, container timing). Therefore:

- **Replicates: R = 5** (up from v1's R=3). I4: with R=3, majority vote tolerates exactly one flip and a 1/3-vs-2/3 repo becomes a hard 0/1, injecting label noise into the discordant cells that *are* the signal. R=5 is within the §9 budget and resolves the discordant cells better. If budget forces R=3, pre-register that **unstable (split) repos are a reported stratum excluded from the primary majority-vote table** as a sensitivity check. Keep temp=0; the API exposes no seed, so "seed" = run index capturing *uncontrolled* nondeterminism — document explicitly.
- **Binary reducer = majority vote (3-of-5)** → value entering the McNemar/TOST table.
- **Cost/steps reducer = per-repo mean (or median) across replicates** before the Wilcoxon. Report **within-cell replicate variance** and the **fraction of unstable repos per arm** (instability reduction is itself a candidate planner benefit).
- **Sensitivity:** also analyze with **strict** (all-R succeed) and **lenient** (any-of-R) reducers; majority is primary, the others sensitivity.

### 6.4 Pre-registration

Commit `PREREG-planner-ablation.md` BEFORE the full sweep containing: the frozen `instance_id` list + sampling seed; the **four-arm roster** (0, A, B, C) and how Arm 0 is sourced (current-branch flags-off as primary; pristine `184a9e3` only as an optional secondary sanity arm, §2.0/§8); H0/H1 for the full pre-registered contrast family — **0-vs-B (whole stack, headline)**, **0-vs-A (world-model/Maintainer)**, **A-vs-C (planner)**, **B-vs-C (worker engineering)**; the **Holm-Bonferroni family-wise correction** over these 4 EBSR contrasts at α=0.05 (smallest p tested at corrected α=0.0125; §6.1) and the explicit split that A-vs-B and the Wilcoxon efficiency tests sit outside the Holm family; primary EBSR via exact McNemar (or pre-registered TOST for the A-vs-C contrast if the pilot shows low discordance), two-sided, with the test-command comparability gate (§4.1); the §3.0 model-compat decision (planner.py port vs MiniMax-direct creds) so Arm 0 fairness is locked; secondary = Wilcoxon on cost/steps/wall-clock over the concordant-success subset; R=5 majority-vote reducer (+ strict/lenient sensitivity, unstable-repo stratum); effect-size estimands (OR, risk difference, rank-biserial, planner-marginal-cost via `supervisor` bucket); stratum weighting (if oversampling the tail); the test-command "materially different" normalizer; the pairwise-drop rules and max acceptable drop rate (§M2); and the stopping rule (no peeking, no adaptive N beyond the single pilot-driven re-derivation).

---

## 7. Experimental Controls & Confounds Held Fixed

- **Same model / temperature:** minimax-m2.7, temp=0, all four arms, all roles (incl. Arm 0's legacy ReAct planner).
- **Model-compat uniformity across arms (NEW, v3 — Arm 0 fairness):** all four arms must extract completion text identically. The EnvState roles got the `response_text()` reasoning-fallback in `9814edc`; the legacy `src/planner.py` did not (`:202` reads `.content` bare). Without the §3.0 port (or MiniMax-direct creds), Arm 0 collapses on MiniMax-via-OpenRouter for a model-compat reason unrelated to "bare vs EnvState," confounding 0-vs-B. The §3.0 port is a hard precondition for the 0-vs-B / 0-vs-A contrasts (Threat #12).
- **Same base image / image-selection path:** identical pre-orchestration code; **pin/fix the selected base image per `instance_id`** across all arms and replicates (M3) so image-selection LLM drift cannot contaminate the IV. Log `token_usage.image_selector` as a diagnostic.
- **Same timeout / `--steps`:** `--steps 30` for the EnvState arms (A/B/C) → 30 tasks × 6 actions = 180; **`--steps 180` for Arm 0** so its ReAct-step budget maps to the same 180 executed-action ceiling (§3.5). Realized action counts reported per arm so any divergence is visible.
- **Budget fairness:** **180 executed-action ceiling enforced in all four arms** — a shared global cap for A/B/C (§3.5), the `--steps`-mapped ceiling for Arm 0 — plus realized-count reporting and the Arm-A cap-to-comparator-median sensitivity (C2).
- **Clean-room gate vs external EBSR:** Arms A/B/C run `--enable-cleanroom` and inherit `_verify_cleanroom_or_fail` verbatim (built on the supervisor finalization tail, NOT legacy `run()`). **Arm 0 has no in-agent clean-room gate** — it predates the mechanism — and this is handled correctly because the **PRIMARY outcome is the harness-level rebuild (EBSR) computed uniformly outside the agent** (§1.3, §4.1). The external EBSR, not an in-agent gate, is the yardstick; Arm 0 is judged on the same rebuild as everyone else, so its lack of `--enable-cleanroom` does not bias the comparison (Threat #13).
- **Shared interruption policy:** the parameterized `interruption_decision` (rolling last-3 window) is identical across **A/B/C** (§3.5/I1); firing-rate is reported. Arm 0 keeps its own legacy guard (reported, not matched — it is the baseline, not a controlled arm of the within-stack contrasts).
- **Behavioral OUTCOMES (NOT "must-be-equal" controls) — I2 reclassification:** Maintainer-call count, probe count, mutation/revision count are *downstream of agent behavior*, which is what differs between arms. Treating them as "must be statistically indistinguishable or the run is void" would raise **false contamination alarms** when arms legitimately behave differently. v2 reclassifies them as **outcomes to report**. The true controls are the *byte-identical code paths* (observer, Maintainer prompt, ACL, probe certification) held fixed across A/B/C — these are by definition absent in Arm 0 (the floor). The only hard validity preconditions are: identical frozen instance set, pinned base image per repo, model-compat uniformity (§3.0), the 180-action ceiling mapped to every arm, the identical clean-room gate within A/B/C (+ uniform external EBSR for all four), identical interruption semantics within A/B/C, and the test-command comparability gate.

---

## 8. Threats to Validity

1. **Arms A/C don't exist yet / flag-routing trap (#1 confound).** Today `--enable-envstate --enable-cleanroom` (no supervisor) routes to **legacy `run()` with shadow-mode envstate** (verified: `run()` dispatch only checks `enable_supervisor`, `agent.py:990-993`), NOT the intended single-worker design. The new flags + `_run_fullstate_worker` + the Arm-C selector are **prerequisites**. Dry-run must confirm routing (§9.3).
2. **Asymmetric clean-room (confirmed).** Legacy `run()` does NOT call `_verify_cleanroom_or_fail`/`_finalize_supervisor_artifacts` (they live only inside `_run_supervisor`). Mitigation: Arms A & C are built on the supervisor finalization tail; the primary outcome is the uniform harness rebuild.
3. **Workplace reuse cross-contamination.** `--reuse-existing-workplace` keys off an existing `agent_run_summary.json` (`run_repo2run_benchmark.py:3314`); sharing `--output-root` lets one cell "reuse" another's run. Mitigation: unique `--output-root` per arm per replicate; never pass `--reuse-existing-workplace` during the sweep (asserted in the dry-run, §M1).
4. **Uncontrolled LLM nondeterminism** (MoE, temp=0 not deterministic). Mitigation: R=5, majority vote, unstable-repo fraction, strict/lenient sensitivity (§6.3).
5. **Token-bucket conflation (now FIXED).** §3.7's mandatory split makes planner cost = Arm C's `supervisor` bucket; per-bucket deltas are interpretable.
6. **Hard-tail floor effects.** `paper_build_success=False` repos may fail ~100% in all arms → all-concordant-failure pairs carry zero McNemar info. The discordance pilot (§9.3) reveals whether the tail is winnable; stratified reporting isolates it.
7. **Test-command divergence (now a GATE, not just reported, C4).** §4.1 excludes materially-different-test-command discordant pairs from the primary table (or re-runs against a canonical command). Capture + report divergence rate.
8. **Network / GitHub flakiness & Docker contention.** Fresh `git clone` + Docker build each run; transient failures break pairs. Mitigation: run arms **interleaved rep-by-rep** (rep1-A, rep1-B, rep1-C, rep2-A, …) so drift hits all arms equally.
9. **Short-SHA checkout drift.** `base_commit == sha` is 6-char; `git checkout --force <sha>` can be ambiguous/drift. Mitigation: log the checkout returncode; **drop the whole triple** for any repo that fails checkout in *any* arm (preserves pairing). Pre-register a **max acceptable drop rate** (>10% of triples dropped → sample compromised, redraw) so silent attrition can't bias toward easy repos (M2).
10. **Maintainer input differs in Arm A (M4).** Arm A's constant `FULLSTATE_TASK_SPEC` vs Arms B/C's varying narrow specs means the Maintainer sees different *input* (same code path). Flagged as a confound weakening "Maintainer held identical"; verify probe-request behavior comparable in the dry-run. (Note A-vs-C shares this only partially — C feeds varying specs — so A-vs-C also carries a small Maintainer-input difference; B-vs-C does not.)
11. **Cross-commit confound — pristine `184a9e3` Arm 0 only (NEW, v3).** The recommended Arm 0 is the **current branch flags-off** ("legacy ReAct + robustness-only fixes `42e7a02`/`0ef7e88`"), which shares the same harness/model-compat/dataset/budget as the EnvState arms — a true single-variable bare baseline. The **optional secondary pristine `184a9e3` arm** introduces a cross-commit confound: it carries different code beyond the agent loop and predates the model-compat fixes, so a pristine-vs-EnvState delta conflates "bare strategy" with "old code." Mitigation: pristine `184a9e3` is run (if at all) **only as a sanity check** on whether the `42e7a02`/`0ef7e88` deltas move EBSR, reported separately and explicitly NOT as the headline baseline; the primary 0-vs-B uses the flags-off current branch.
12. **Model-compat uniformity across arms (NEW, v3 — Arm 0 fairness).** Legacy `src/planner.py:202` reads `.content` bare with no `reasoning` fallback (the fallback `9814edc` added only to EnvState roles). On MiniMax-via-OpenRouter, an un-ported Arm 0 gets empty content and collapses for a reason orthogonal to "bare vs EnvState," fatally confounding 0-vs-B. Mitigation: the §3.0 port of `response_text()` into `src/planner.py` (or run on MiniMax-direct creds); dry-run asserts Arm 0 actually emits non-empty actions. This is a hard precondition for the 0-vs-* contrasts.
13. **Arm 0 has no in-agent clean-room gate — handled by external EBSR, not an in-agent gate (NEW, v3).** Arms A/B/C run `--enable-cleanroom`; Arm 0 cannot (it predates the mechanism). This is NOT a confound because the **primary outcome is the harness-level rebuild (EBSR) computed uniformly outside the agent for all four arms** (§1.3, §4.1) — the yardstick judges the output Dockerfile, not the construction path. The in-agent gate is a mechanism the EnvState arms use to *avoid declaring hollow successes*; the external EBSR is what actually scores every arm identically. Residual note: an EnvState arm whose in-agent gate fails will not finalize a "passing" Dockerfile, whereas Arm 0 always emits one — but both are then scored by the same external rebuild, so the comparison stays on one scale. The §4.1 test-command comparability gate remains the real anti-hollow protection across arms.
14. **External validity:** Python-only (100% of dataset) and the Repo2Run Table-15 distribution; no cross-language claims.

---

## 9. Harness & Execution Plan

### 9.1 Flag-plumbing edit (REQUIRED — confirmed blocker)

Verified: `build_agent_command` (`run_repo2run_benchmark.py:162-198`) forwards **none** of `--enable-supervisor / --enable-envstate / --enable-cleanroom / --enable-fullstate-worker / --fullstate-worker-prompt`. Today every benchmark run silently executes legacy `DockerAgent.run()`. Two minimal edits:

**Edit 1 — `build_agent_command()` (`run_repo2run_benchmark.py:162-198`), before `return command`:**
```python
if getattr(args, "enable_supervisor", False):        command.append("--enable-supervisor")
if getattr(args, "enable_fullstate_worker", False):  command.append("--enable-fullstate-worker")
if getattr(args, "fullstate_worker_prompt", False):  command.append("--fullstate-worker-prompt")
if getattr(args, "enable_envstate", False):          command.append("--enable-envstate")
if getattr(args, "enable_cleanroom", False):         command.append("--enable-cleanroom")
```
(`build_agent_command` is the single funnel, called at `run_repo2run_benchmark.py:3303`.)

**Edit 2 — `parse_args()` (around `run_repo2run_benchmark.py:3117-3249`):** add `--enable-supervisor`, `--enable-fullstate-worker`, `--fullstate-worker-prompt`, `--enable-envstate`, `--enable-cleanroom` as `action="store_true"` with help text.

Resulting arms:
- **Arm 0** = **no EnvState flags at all** (`--enable-supervisor` / `--enable-fullstate-worker` / `--enable-envstate` / `--enable-cleanroom` all absent) → the harness forwards nothing, and `agent.py` falls through to legacy `run()` (bare ReAct). Set `--steps 180` for Arm 0 (§3.5/§7). Arm 0 needs **no plumbing change** — the flags-off path is the default; the §3.0 model-compat port is its only prerequisite.
- **Arm B** = `--enable-supervisor --enable-cleanroom`
- **Arm A** = `--enable-fullstate-worker --enable-cleanroom`
- **Arm C** = `--enable-supervisor --fullstate-worker-prompt --enable-cleanroom`
- (Optional secondary) **pristine bare** = run on a checkout of `184a9e3` with no flags, as a cross-commit sanity arm only (§2.0/§8 Threat #11), never the headline baseline.

### 9.2 Output directory layout (full isolation)

```
outputs/ablation/
  arm0_bare_react/       rep1/ … rep5/
  armA_fullstate/        rep1/ … rep5/
  armB_supervisor/       rep1/ … rep5/
  armC_matched_planner/  rep1/ … rep5/
  (optional) arm0pristine_184a9e3/  rep1/ … repN/
```
Each cell: own `--output-root .../<arm>/repN`, containing `summary.json`, `results/<id>.json`, `workplaces/<id>/agent_run_summary.json`, `Dockerfile`, `eval_artifacts/`. Never share `--output-root` across cells.

### 9.3 Dry-run / discordance pilot (mandatory go/no-go gate — now dual-purpose, per I3)

Run **~15-20 mixed-stratum repos** (include `first_5_failed.json` paper-failures) through **all four arms** at R=1, small `--steps` (~40 for A/B/C; the proportionally-mapped step count for Arm 0), `--keep-docker-artifacts`, and verify:
- (a) the new flags reach `agent.py` (grep `agent_run.command_shell` in `results/<id>.json` for `--enable-fullstate-worker` / `--enable-supervisor` / `--fullstate-worker-prompt`), and **Arm 0's command carries NONE of them** (confirms the flags-off bare path);
- (b) **Arm 0 routes to legacy `run()` (bare ReAct, no orchestrator/Supervisor), Arm A routes to `_run_fullstate_worker`, Arm C routes to `_run_supervisor` with the fullstate planner, Arm B to plain `_run_supervisor`** (the #1 confound);
- (b2) **Arm 0 emits non-empty actions** — i.e. the §3.0 `response_text()` port (or MiniMax-direct creds) works and Arm 0 does NOT collapse on empty `content` (Threat #12); confirm the `src/planner.py` content extraction is non-empty on the chosen creds;
- (c) `cleanroom.passed` populated for A/B/C; `action_ledger` present for A/B/C (Arm 0 has the legacy summary schema, no `cleanroom`/`action_ledger`);
- (d) the **`supervisor`/`worker` token buckets are split** and Arm A's `supervisor` bucket = 0 (§3.7 landed); Arm 0 has only `planner`/`total` buckets;
- (e) the **180-action ceiling holds for all four arms** — the shared global cap fires in A/B/C and Arm 0's `--steps`-mapped ceiling holds; realized counts recorded (§3.5);
- (f) **interruption firing-rate comparable** across A/B/C; Maintainer probe-request behavior comparable (M4); Arm 0's legacy guard behavior recorded (not matched);
- (g) two cells with distinct `--output-root` produce independent `agent_run_summary.json`; `--reuse-existing-workplace` never set (M1);
- (h) the extraction script parses **all four arms**, tolerating Arm 0's missing EnvState fields as null (§4.5);
- (i) **measure the actual discordant-pair rate for every pre-registered contrast (0-vs-B, 0-vs-A, A-vs-C, B-vs-C) and recompute N** from the least-powered contrast under the Holm-corrected alpha (I3, §6.1); decide McNemar-superiority vs TOST-equivalence framing per contrast;
- (j) read real per-repo `token_usage.total` for all four arms to recompute cost before the full sweep.

### 9.4 Cost estimate (minimax/minimax-m2.7: $0.28/M prompt, $1.20/M completion)

Env-setup ReAct is prompt-token-heavy (observations re-sent each step; compression on by default).
- Arm 0: bare ReAct, no Supervisor/Maintainer/probe round-trips, raw observations (no full-snapshot re-send) → likely the **cheapest**, ~$0.3-1.2 / repo / replicate.
- Arm B: ~$0.5-1.5 / repo / replicate.
- Arm A: full-snapshot brief inflates per-step prompt tokens but removes Supervisor calls → ~$0.5-1.7.
- Arm C: planner round-trips **plus** fullstate worker prompts → likely the most expensive, ~$0.7-2.0.
- Planning midpoint: ~$1.05 / repo / replicate (blended across the **4 arms**, slightly below the 3-arm midpoint because the added Arm 0 is the cheapest).
- **Total now = 4 arms × 120 repos × 5 replicates × ~$1.05 ≈ $2,520** (budget **$1,500-$3,000**; the 4th arm raises the headline ~25-30% over the v2 3-arm ≈$1,980, partly offset by Arm 0 being the cheapest). At R=3: ~$1,500. Dry-run (≈80 runs across 4 arms) ≈ $84. **Recompute from the dry-run before committing** (the per-arm multiplier is now ×4, so the recompute matters more); if the recomputed total exceeds budget, the levers are R (5→3, with the unstable-repo-stratum sensitivity) and N (re-derived from the pilot discordance), in that order.

### 9.5 Metric extraction

`scripts/extract_ablation_metrics.py` (§4.5): per cell, load `summary.json` + `results/<id>.json` + `workplaces/<id>/agent_run_summary.json`; record per-repo rows (incl. `arm ∈ {0,A,B,C}`, `supervisor_tokens`, `worker_tokens`, `verified_test_commands`, realized `n_actions`, `no_more_tasks`), tolerating Arm 0's missing EnvState fields as null; reduce replicates (majority vote for binary, mean for cost/steps); inner-join the **four arms** on `instance_id` (assert identical frozen IDs); apply the test-command comparability gate (§4.1); build all **four pre-registered McNemar 2×2s — 0-vs-B (headline), 0-vs-A, A-vs-C, B-vs-C** → exact p (or TOST where pre-registered), OR=b/c, risk diff, 95% CI, plus the descriptive A-vs-B table; apply **Holm-Bonferroni** across the 4-contrast EBSR family and report raw + adjusted p (§6.1); on each contrast's concordant-success subset run Wilcoxon → p + rank-biserial + bootstrap CI; compute planner-marginal-cost = median Arm-C `supervisor` bucket; emit `ablation_report.json` (raw 2×2s for all contrasts, b/c, raw + Holm-adjusted p-values, effect sizes, per-stratum breakdown by `paper_build_success`, unstable-repo fraction per arm, excluded-pair list with reasons) + a per-instance paired CSV (all four arms wide). Cost USD = `prompt/1e6*0.28 + completion/1e6*1.20`. **Raw artifacts only — no inline narrative.**

### 9.6 Safe VM execution

- **Do NOT use inline `pkill -f run_repo2run_benchmark` over SSH** — it can match and kill the SSH session itself (known self-kill). Launch each arm via a **script file on the box** (`run_arm.sh`) under `setsid`/`nohup` (or `tmux`), recording its PGID: `setsid bash run_arm.sh > arm.log 2>&1 & echo $! > arm.pid`. Stop via a `stop.sh` on the box that kills the recorded PGID (`kill -- -$(cat arm.pid)`), never an inline broad `pkill`.
- Run arms/replicates **interleaved rep-by-rep** (rep1-0, rep1-A, rep1-B, rep1-C, rep2-0, …) so all four arms see the same network/Docker drift (§8 Threat #8). Clean `workplaces/` between cells if disk is tight.

---

## 10. Run Procedure & Decision Criteria

### 10.1 Procedure

1. Implement Arms A & C (§3) + the **Arm 0 `src/planner.py` model-compat port (§3.0)** + flag plumbing (§9.1) + **mandatory** token-bucket split (§3.7) + shared action cap (§3.5) + shared interruption fn (§3.5/I1). Land on `john-planner-v1`. (Arm 0 itself needs no new code beyond §3.0; it is the flags-off path.)
2. Draw and freeze `datasets/ablation_n120_seed42.json`; pin base image per `instance_id` (M3); commit `PREREG-planner-ablation.md` (§6.4) with the four-arm roster, the four-contrast family, and the Holm correction.
3. Dry-run / discordance pilot (§9.3). **No-go** unless checks (a)-(h) pass (incl. (b2): Arm 0 emits non-empty actions); use (i) to set final N and the per-contrast McNemar-vs-TOST framing; use (j) to confirm budget.
4. Full sweep: **4 arms** × N repos × R=5, interleaved, isolated `--output-root` per cell.
5. Extract raw artifacts (§9.5). Hand to downstream subagents for analysis.

### 10.2 Decision criteria

The **headline verdict** reads the **0-vs-B** contrast (does the whole EnvState+Supervisor stack beat the original bare-ReAct agent?). The **planner verdict** reads the **A-vs-C** contrast (clean), never A-vs-B. The **world-model/Maintainer verdict** reads **0-vs-A**. All EBSR contrasts are judged at their Holm-adjusted alpha (§6.1).

- **Validity precondition (must hold or results are void):** identical frozen instance set across all four arms; base image pinned per repo; **model-compat uniformity (§3.0) confirmed (Arm 0 not collapsing on empty content)**; 180-action ceiling mapped to every arm; identical clean-room gate within A/B/C (+ uniform external EBSR for all four); identical interruption semantics within A/B/C; test-command comparability gate applied; checkout-failed *tuples across all four arms* dropped pairwise within the max drop rate. (Behavioral counts — Maintainer/probe/mutation — are reported, NOT gated; I2.)
- **HEADLINE — does the program clear its floor (0-vs-B):** if McNemar shows **B > 0** with a meaningful, Holm-significant EBSR advantage (OR > 1, CI excluding 1), the EnvState+Supervisor stack beats the original agent — the program is justified. If **B ≤ 0** (the stack does not beat bare ReAct), that is the dominant finding and the within-stack ablations are moot for the headline (still reported). Report the directional 0-vs-A and A-vs-C decomposition to localize where any 0→B gain comes from (world-model/Maintainer vs planner).
- **DROP the planner if (A-vs-C):** McNemar shows **A ≥ C** on EBSR (OR ≤ 1, risk-difference CI lower bound ≥ −2 pts) **AND** Arm C's `supervisor` bucket (planner marginal cost) is non-trivial. Interpretation: the planner adds no reproducible-setup value over the same worker without it, and costs Supervisor tokens for nothing.
- **KEEP the planner if (A-vs-C):** McNemar shows **C > A** with a meaningful, Holm-significant EBSR advantage (risk diff ≥ +8 pts, Holm-adjusted exact-McNemar p < 0.05, OR > 1 with CI excluding 1) — especially concentrated in the `paper_build_success=False` tail. Interpretation: the planner's task decomposition / phase sequencing genuinely improves reproducible setup; accept the token cost if the gain justifies it.
- **EQUIVALENCE / inconclusive (A-vs-C):** if discordance is low and the risk-difference CI brackets zero within ±2 pts (pre-registered TOST), report a no-difference result with the OR CI. Tie-break on cost: the planner adds the Arm-C `supervisor` bucket for no EBSR gain, so equivalence-with-cost favors **dropping the planner**.
- **World-model/Maintainer verdict (0-vs-A):** whether host-certified world model + Maintainer (no planner) beats bare ReAct, holding the planner absent in both. Localizes the 0→A rung of the ladder.
- **Worker-engineering verdict (B-vs-C, reported separately):** whether full context + RCA prompt helps, holding the planner fixed. If B≈C, the worker engineering is inert and the simpler narrow brief is preferable; if C>B, the engineering helps regardless of the planner.
- **System note (A-vs-B):** reported for completeness as within-stack whole-system, explicitly NOT a single-component verdict; outside the Holm family.

---

## 11. Deliverables

0. **Arm 0 model-compat port (§3.0):** port `response_text()` (from `src/envstate/llm_response.py`) into `src/planner.py` (`:202`), so all four arms share identical completion-text extraction (reasoning-content fallback). Alternatively, lock the experiment to MiniMax-direct creds. Behavior-preserving when `content` is already non-empty. **Without this, Arm 0 is not a fair baseline** on MiniMax-via-OpenRouter.
1. **Code:** `src/envstate/fullstate_worker.py` (renderer, prompt, `FullStateWorkerPlanner`, shared `interruption_decision`, `run_fullstate_loop`); `agent.py` additions (`_run_fullstate_worker`, Arm-C worker-planner selector in `_run_supervisor`, **mandatory `supervisor`/`worker` token-bucket split**, flag wiring, mutual-exclusion guards, dispatch ordering); `src/envstate/orchestrator.py` optional `global_action_budget`; `src/envstate/worker.py` interruption-guard refactor (behavior-preserving); `src/planner.py` `response_text()` port (deliverable 0); `run_repo2run_benchmark.py` flag-plumbing edits (§9.1) **— and the harness must be able to run Arm 0 with all EnvState flags absent (flags-off legacy `run()`)**, which the §9.1 `getattr(..., False)` plumbing already yields (passing none of the flags forwards none). Default-OFF; Arm B and legacy `run()` behavior unchanged.
2. **Frozen sample:** `datasets/ablation_n120_seed42.json` (N IDs, stratified on `paper_build_success`, fixed seed, base image pinned per repo) — run through **all four arms**.
3. **Pre-registration:** `PREREG-planner-ablation.md` committed before the sweep (§6.4), with the **four-arm roster** (Arm 0 sourced as current-branch flags-off; pristine `184a9e3` only optional secondary), the **four-contrast family** (0-vs-B, 0-vs-A, A-vs-C, B-vs-C), the **Holm-Bonferroni correction**, and the per-contrast McNemar-vs-TOST decision rule.
4. **Extraction script:** `scripts/extract_ablation_metrics.py` emitting `ablation_report.json` (all four contrast 2×2s — 0-vs-B, 0-vs-A, A-vs-C, B-vs-C — with raw + Holm-adjusted p, planner-marginal-cost, per-stratum, excluded-pairs) + per-instance four-arm-wide paired CSV (raw artifacts only); tolerates Arm 0's missing EnvState fields.
5. **Run artifacts:** `outputs/ablation/{arm0_bare_react,armA_fullstate,armB_supervisor,armC_matched_planner}/rep{1..5}/` (+ optional `arm0pristine_184a9e3/`).
6. **Dry-run / pilot log** demonstrating routing (**0→legacy bare `run()`**, A→`_run_fullstate_worker`, C→`_run_supervisor`+fullstate planner, B→plain `_run_supervisor`), flags reaching `agent.py` (and **absent** for Arm 0), **Arm 0 emitting non-empty actions** (§3.0 port working), `cleanroom.passed`/`action_ledger` populated (A/B/C), `supervisor` bucket split (=0 in Arm A), 180-action ceiling holding in all four arms, comparable interruption rates (A/B/C), and the measured discordance for **all four contrasts** used to finalize N and the per-contrast test framing.
7. **Headline outputs** (computed downstream): the four paired McNemar 2×2s (0-vs-B headline, 0-vs-A, A-vs-C, B-vs-C) + b/c, exact p (raw + Holm-adjusted) (or TOST where pre-registered), OR, risk difference + 95% CI, raw EBSR per arm (all four), `coverage`, per-stratum breakdown, unstable-repo fraction, planner marginal cost (Arm-C `supervisor` bucket), Wilcoxon on cost/steps/wall-clock over each contrast's concordant-success subset, total USD per arm, and the A-vs-B within-stack system comparison labeled as such.

---

## 12. Changes from critique (resolution log)

### v3 — Arm 0 (bare ReAct) added (this revision)

- **V3-1 — no pre-EnvState floor; the program lacked a baseline to clear.** RESOLVED by adding **Arm 0 = bare ReAct**, the original pre-EnvState agent, defined by what it LACKS (no EnvState world model, no Maintainer, no host-probe certification, no Supervisor task-decomposition; it still runs the legacy `src/planner.py` ReAct `plan()` at `agent.py:1200,1204` — that is the ReAct planner, NOT the Supervisor). The **0-vs-B** contrast is the new HEADLINE ("does the whole stack beat the original agent?"), with **0-vs-A** (world-model/Maintainer value) and the existing A-vs-C (planner) / B-vs-C (worker engineering) forming a ladder: bare → +world-model/Maintainer (0→A) → +planner (A→C), prompt axis B↔C (§1.1, §1.5, §2.0, §2, §10.2).
- **V3-2 — how to source Arm 0 without a cross-commit confound.** RESOLVED: primary Arm 0 = **current branch flags-off** ("legacy ReAct + robustness-only fixes `42e7a02`/`0ef7e88`"), verified to be the base of `radical` = `184a9e3` plus two strategy-neutral fixes — same harness/model-compat/dataset/budget, single-variable. Pristine `184a9e3` is only an OPTIONAL secondary sanity arm (cross-commit confound; predates model-compat fixes), never the headline (§2.0, §9.1, §9.2, Threat #11).
- **V3-3 — Arm 0 model-compat would collapse on MiniMax-via-OpenRouter.** RESOLVED: legacy `src/planner.py:202` reads `.content` bare; `response_text()` (`9814edc`) was wired only into EnvState roles. Prescribed prerequisite (§3.0, deliverable 0): port `response_text()` into `src/planner.py` so all four arms share identical model-compat, OR run on MiniMax-direct creds. Dry-run check (b2) asserts Arm 0 emits non-empty actions (§9.3, Threat #12).
- **V3-4 — does the primary metric work for a clean-room-less arm?** RESOLVED (key enabler): the harness `environment_build_success` (EBSR) is computed **externally and agent-agnostically** — it rebuilds the produced Dockerfile and judges the OUTPUT, not the construction path (`run_repo2run_benchmark.py:3533-3540`, also `:657-658,:787-788`). So Arm 0 (no `--enable-cleanroom`) joins on the SAME EBSR scale as A/B/C with no flag; the in-agent clean-room is a mechanism, not the yardstick (§1.3, §4.1, Threat #13).
- **V3-5 — budget parity for a `--steps`-bounded arm.** RESOLVED: map Arm 0's `--steps` so its executed-action budget equals the shared 180 (`--steps 180`; ≈1 step ≈ 1 action). Report REALIZED action counts across all four arms so any difference is visible, not assumed (§3.5, §7).
- **V3-6 — multiple pre-registered contrasts inflate family-wise error.** RESOLVED: added **Holm-Bonferroni** over the four-contrast EBSR family (0-vs-B, 0-vs-A, A-vs-C, B-vs-C); smallest p tested at corrected α = 0.05/4 = 0.0125; A-vs-B and the Wilcoxon efficiency tests sit outside the family. Paired design and R=5 majority retained; the pilot still recomputes N/discordance numerically (§6.1, §6.4, §5.4, §10.2).
- **V3-7 — cost/arm-count/deliverables.** Arm count 3→4 everywhere; cost re-stated as **4 arms × 120 × 5 × ~$1.05 ≈ $2,520** (per-arm multiplier now ×4; Arm 0 is the cheapest; recompute-from-pilot caveat kept) (§9.4); deliverables add the §3.0 `response_text` port (deliverable 0) and the flags-off harness requirement for Arm 0 (§11).

### CRITICAL

- **C1 — IV confounded 3 ways (single-variable integrity).** RESOLVED by adding **Arm C (matched-context planner)**: Arm B's full `_run_supervisor`/orchestrator with the Worker swapped to the exact `FullStateWorkerPlanner` (full snapshot + RCA prompt) Arm A uses (§2.3, §3.8). **A-vs-C now isolates the planner cleanly** (only the planner differs); **B-vs-C isolates worker engineering**; A-vs-B is retained but relabeled a within-stack whole-system comparison and explicitly excluded from the planner verdict (§1.1, §6.1, §10.2). The planner question ("does the planner add value?") is answered by A-vs-C. Adopted the recommended 3-arm fix rather than the relabel-only fallback. (v3 then added Arm 0 above this stack as the bare-ReAct floor — see V3-1.)
- **C2 — budget not actually matched.** RESOLVED: replaced the unreachable task-count "ceiling" with a **180 executed-action ceiling enforced in all arms** — a shared global cap for A/B/C via an optional `global_action_budget` on the orchestrator + the fullstate loop, and a `--steps`-mapped ceiling for Arm 0 (§3.5). Added **realized executed-action reporting** (all four arms), the **`no_more_tasks` early-stop asymmetry diagnostic** (B/C only, §4.2/§4.4), and an **Arm-A-capped-at-comparator-median sensitivity analysis** (§3.5).
- **C3 — cost saving unmeasurable.** RESOLVED: the `supervisor`/`worker` token-bucket split is now **mandatory** (§3.7). Planner marginal cost = Arm C's `supervisor` bucket (Arm A's = 0 by construction); per-bucket deltas are now interpretable; decision rules use the `supervisor` bucket, not `total` (§4.3, §6.2, §10.2).
- **C4 — clean-room gameable via trivial test command.** RESOLVED: test-command comparability is now a **gate**, not just a report — discordant pairs with materially-different `verified_test_commands` are excluded from the primary McNemar table (or re-run against a canonical command), with a pre-registered normalizer (§4.1, §6.4, Threat #7).

### IMPORTANT

- **I1 — `should_interrupt` behaves differently in Arm A's single loop.** RESOLVED: extracted a **shared, identically-parameterized `interruption_decision`** keyed on a rolling **last-3-observation window** used by all arms; Arm B's per-task firing stays bit-identical; firing-rate is a reported control-check asserted comparable in the dry-run (§3.5, §7, §9.3).
- **I2 — Maintainer/probe/mutation counts wrongly treated as must-be-equal controls (circular).** RESOLVED: **reclassified them as behavioral OUTCOMES to report**, not validity gates; the true controls are the byte-identical *code paths*. Dropped the "statistically indistinguishable or void" gate that would raise false contamination alarms (§4.2, §7).
- **I3 — power rests on unvalidated discordance; hard tail may contribute zero info.** RESOLVED: the dry-run is now a **real discordance pilot** (~15-20 mixed-stratum repos, all four arms) that **recomputes N from the observed `p_disc` of every pre-registered contrast (0-vs-B, 0-vs-A, A-vs-C, B-vs-C)** — N set by the least-powered contrast under the Holm-corrected alpha — and switches the A-vs-C frame to **TOST equivalence** (margin ±2 pts) if its discordance < ~0.15 (0-vs-B stays superiority); McNemar stratified by `paper_build_success` with all-concordant-failure pairs reported but zero-weighted (§5.4, §9.3, §6.1).
- **I4 — R=3 too thin for documented nondeterminism.** RESOLVED: bumped to **R=5** (within budget); pre-registered the **unstable-repo stratum** (split replicate outcomes) as a separate reported group excluded from the primary majority table as sensitivity; R=3 retained only as a budget fallback with the same stratum treatment (§6.3, §9.4).

### MINOR

- **M1** — dry-run now asserts distinct `--output-root` cells produce independent summaries and `--reuse-existing-workplace` is never set (§9.3g, Threat #3).
- **M2** — pre-registered **max acceptable triple-drop rate (>10% → redraw)** for short-SHA checkout failures (§6.4, Threat #9).
- **M3** — **base image pinned per `instance_id`** across all arms/replicates so image-selection LLM drift can't contaminate the IV (§7).
- **M4** — Maintainer *input* differs in Arm A (constant synthetic spec vs varying specs); flagged as a confound weakening "Maintainer held identical," verified comparable in the dry-run; noted A-vs-C carries only a partial version of this and B-vs-C none (§3.2, Threat #10).
- **M5** — `scripts/` confirmed absent; the extraction script is a prerequisite deliverable, not an existing tool (§4.5, §9.5, §11.4).

---

**Key verified file references** (branch `john-planner-v1`; `agent.py` is at the repo root): flag attrs/auto-enable `agent.py:158-159` (today `self.enable_envstate = enable_envstate or enable_supervisor`, no fullstate flag — Arms A/C do not exist yet); `run()` dispatch `agent.py:990-993` (checks only `enable_supervisor`; with all EnvState flags off it falls through to legacy `run()` = **Arm 0** bare ReAct); legacy ReAct planner invocation `agent.py:1200,1204` (`self.planner.plan(...)`); `_run_supervisor` `agent.py:844-906` (worker construction ~862-866, `orchestrator.run()` ~880); `_build_observer` `agent.py:792-842` (Maintainer-call gate `agent.py:816`, certified-PRESENT probe filter `agent.py:944`); `_finalize_supervisor_artifacts` `agent.py:909-921`; `_verify_cleanroom_or_fail` `agent.py:922-974`; `_record_supervisor_path_usage` `agent.py:977-988` (Supervisor+Worker→`planner` conflation, docstring 979-985). `supervisor.py:8-14` (SETUP_PHASES), `:27-37` (prompt/emit), `:44-75` (render_planning_view), `:78-80` (parse_task_spec), `:82-104` (`Supervisor.next_task`, temp=0 at `:97`); `worker.py:9` (DEFAULT_MAX_ACTIONS=6), `:13-19` (WorkerReport), `:22-45` (`_looks_like_pin_edit`/`should_interrupt`, repeated-failure guard `:42-44`), `:60-87` (`run_task`, `observations[-3:]` `:72`), `:90-100` (`build_task_brief`), `:103-119` (`WORKER_SYSTEM_PROMPT`), `:125-136` (extract/finished), `:139-171` (`LlmWorkerPlanner`, temp=0 `:158`). `maintainer.py:47-63` (`build_maintainer_input`), `:75-104` (`interpret`, temp=0 `:93`). `orchestrator.py:45,47-58` (`_step`/`_make_step_fn`), `:60-83` (`run()`, bounds on `tasks_completed`, `no_more_tasks` `:72-74`). `cleanroom.py:21-42` (COPY inject), `:45-94` (`verify_cleanroom`, nothing-to-verify fail `:73-74`, build fail `:69`, test fail `:91`). `types.py:8-14` (`Source`), `:30-50` (`Requirement`/`Evidence`), `:79-88` (`EnvStateSnapshot`). Legacy ReAct planner: `src/planner.py:202` (`content = response.choices[0].message.content or ""` — NO reasoning fallback, the Arm 0 model-compat gap; §3.0). Model-compat helper: `src/envstate/llm_response.py:5` (`response_text()`, prefers `.content`, falls back to `reasoning` attr / `model_extra["reasoning"]`). Runner: `build_agent_command` `run_repo2run_benchmark.py:162-198` (forwards NO envstate flags — confirmed), called `:3303`; EBSR computation `:3533-3540` (`environment_build_success = dockerfile_generation_success AND test_execution AND all_test_commands_effective`), also rendered in the per-instance report at `:657-658,:787-788`; `--reuse-existing-workplace` `:3240,3314`. Dataset: `datasets/repo2run_table15.json` = 420 instances, 100% `language="python"`, 361 True / 59 False on `paper_build_success`, `base_commit == sha` (6-char). `scripts/` does not exist. **Arm 0 provenance (verified):** `radical` tip = `184a9e3` (= the bare-ReAct base; `john-planner-v1` forked from there); the two strategy-neutral robustness fixes between the fork and the EnvState work are `42e7a02` (tolerate `None` completion content) and `0ef7e88` (reject stale test evidence); the `response_text()` reasoning fallback was added in `9814edc` and wired ONLY into worker/supervisor/maintainer (NOT `src/planner.py` — confirmed by that commit's file list).
