# DESIGN: Grounded Repair Memory (GRM) — graph-based agent memory on the contract graph

**Status:** Proposal / discussion draft
**Date:** 2026-06-20
**Branch:** `john-planner-v1`
**Author:** investigation + synthesis (Claude Code, 7-agent workflow `wf_0f056a37-5aa`)
**Purpose:** Fuse the existing long-term memory subsystem into the v1g contract graph so the graph's *fault diagnosis and repair selection* are driven by **execution-certified cross-run experience** instead of single-shot LLM speculation.

---

## 0. TL;DR for reviewers

- The agent **already has** a SetupX-style long-term memory store (`src/memory_manager.py`), but it has **no feedback loop** and is **only wired into the legacy ReAct path** — the v1g contract-graph arm never consults it, so it runs cold on every repo.
- The contract graph is **not "pure speculative"**: its *satisfaction/retirement* half is execution-certified; its *fault-diagnosis* half (Blockers, signatures, root-cause edges) is single-shot Maintainer-LLM speculation that nothing verifies.
- **SetupX's XPU** is the more mature memory design (pgvector, two-layer retrieval, telemetry tier-boosting) but its telemetry is **LLM-audit-guessed, not execution-certified** — its own documented weakness.
- **Proposal (GRM):** make a memory entry a *frozen certified subgraph*, project a 3-state trust label per node (speculative / retrieved / grounded), and close a feedback loop using the host certification that **already exists** (`host_satisfied`, `derive_attempt_outcome`). This is strictly better than XPU on its weakest axis.
- **Phase 1** (telemetry + grounded repair selection, flag-gated, A/B-able) is cheap and the right first bet. **Caveat:** payoff is conditional on faults transferring across repos (true at the system/build layer only); no single-run gain; validate on a 50-repo sweep with a warm store.

**Open questions for codex are in §8.**

---

## 1. How memory works in the agent system today

There are **two separate "memory-like" subsystems that never touch each other.** That disconnect is the core problem.

### 1.1 `LongTermMemoryManager` — the real long-term memory (`src/memory_manager.py`)

A SetupX-style experience store, simpler:

| Aspect | Implementation | Ref |
|---|---|---|
| Storage | Flat JSONL `memory/long_term_memories.jsonl`, linear scan per query | `load_memories` :456 |
| Embeddings | Local `SentenceTransformer`, cosine in pure Python | `cosine_similarity` :991 |
| Schema | `scope` (global/ecosystem/repo), `problem_signature`, `symptoms`, `root_cause`, `successful_fix`, `verification`, `anti_patterns`, `embedding_text`, `linked_memories`, `embedding` | `normalize_memory` :931 |
| Retrieval | On a **failure**: query from failed command + failure-line-extracted observation + repo + services; top-5 by cosine ≥ 0.30 | `retrieve()` :396 |
| Write | On a **successful** run: LLM distills the trajectory into reusable difficult-problem lessons (excludes trivial one-shot fixes) | `generate_memories_from_run()` :533 |
| "Graph" | LLM **relation-judge** classifies new vs cosine-neighbor as `duplicate`/`link`/`unrelated`; writes bidirectional `linked_memories` | `_judge_memory_relation` :729 |

Two structural facts:
1. **No feedback loop.** Write-once; never reinforced or decayed; **no telemetry counters**. A fix that resolved a fault 8× ranks identically to one that never has.
2. **`linked_memories` is never traversed at retrieval.** Retrieval is pure top-k cosine; the links are decorative.

### 1.2 Where it's wired — and where it isn't

`LongTermMemoryManager` is invoked **only in the legacy ReAct loop** via the `__RETRIEVE_MEMORY__` action: retrieve at `agent.py` ~1978–2010, write at ~2372–2383. **The v1g contract-graph path (`run_v1` → `planner.decide` → `refresh_host_graph`) never consults memory at all.** The best reasoning arm runs cold on every repo.

### 1.3 The contract graph: what is actually speculative

Verified directly in `contracts/schema.py`, `nodes.py`, `attempts.py`. The "pure speculative" framing is **half-right; the boundary is sharp**:

- **NOT speculative up front:** the planner *never creates nodes* — it only references existing `target_node_ids` (`planner.py` output is recipe/done/giveup only). No forward failure prediction anywhere.
- **Speculative half = the FAULT side, post-hoc:** every **Blocker** (`signature`, `kind`, `root_or_downstream`, `summary`), every Maintainer **atomic Contract**, all `violates`/`depends_on` root-cause edges — a single-shot Maintainer LLM read of one `TaskReport` (`maintainer.py:271-422`). `validate_patch` checks only that `evidence_refs` point at a real Attempt id; it **never verifies the signature text appears in that output**, nor that the causal edge is true (`validation.py:23-116`).
- **Grounded half = the SATISFACTION/RETIREMENT side:** atomic Contracts regex-promoted from real failing stdout (`extract.py:37-52`), host-created Attempts with rc-derived outcomes (`attempts.py`, `orchestrator.py`), `host_satisfied` from real import sweep / package probe / collect-only / verified pytest (`projection.py:114-186`, `validators.py:113-127`), Blocker auto-retire (`_auto_resolve_blockers`).

So a Contract going **`violated` is LLM speculation**; a Contract going **`satisfied` is execution truth**. `status` is **projected per cycle, never stored** (`graph.py:89-94`).

### 1.4 Notable existing gaps (a redesign should fix these)

- **`Contract.evidence_refs` is a dead field** — always `[]` (`goals.py`, `extract.py`, `attempts.py`); a satisfied contract carries no on-graph record of *which* execution certified it.
- **Blocker signature is unverified LLM text** — and per prior finding (`system-autoresolve-dormant`), the Maintainer paraphrases signatures, which pollutes the graph and breaks the deterministic `_auto_resolve_blockers` subject matching.
- **No accuracy feedback** — `ok_but_still_blocked` is shown to the planner as text but never down-ranks the misdiagnosis; nothing learns whether a chosen fix worked.
- **`'violated'` is entirely Blocker-driven** — a genuinely broken obligation the Maintainer fails to diagnose projects `unknown`, not `violated`. Fault coverage = quality of the single-shot diagnosis.
- **Attempt id collisions** — `attempt_id = slug(step.id + ':' + command[:20])` can collapse two steps into one node.

---

## 2. How SetupX implements memory (the XPU system)

SetupX (paper: *"SetupX: Can LLM Agents Learn from Past Failures in Functionality-Correct Code Repository Setup?"*, arXiv **2605.26186**; repo: github.com/OpenDataBox/SetupX) solves the same problem with a more mature memory layer, **XPU (eXPerience Unit)**.

- **Store:** PostgreSQL + pgvector, `IVFFlat` (lists=100), `text-embedding-3-small` (1536-d). `xpu_vector_store.py:45-68`.
- **Schema per XPU:** `signals` (applicability lang/os/python/tools + **regex + keywords + situation_triggers**), `advice_nl`, **`atoms`** (~13 typed actionable commands — `pip_install`, `apt_install`, `set_env`, … rendered to bash), **`telemetry` {hits, successes, failures}**. `xpu_adapter.py:14-45`.
- **Two-layer retrieval:** Layer 1 pgvector recall (N=10, min_sim 0.45), composite **`score = similarity · (1 + successes/hits) · tier_boost`** (golden 1.5 if hits≥5 & rate≥0.6; cold 0.6 if rate<0.3); Layer 2 LLM re-rank to top-3. `xpu_vector_store.py:219-374`, `retriever_agent.py`.
- **Feedback loop:** a **delayed LLM audit** — on the next retrieval, an LLM judges whether the previously-suggested XPU helped the last ≤5 steps and increments successes/failures from that verdict string. `retriever_agent.py:478-583`.
- **Ingestion:** Phase-3 LLM distillation of trajectories (prefers the Prosecutor/Judge "charges"), dedup-and-merge at cosine ≥ 0.85 via two more LLM calls. Ships a **600-entry warm store** (`data/xpu_warm.jsonl`).
- **Headline:** abstract claims **92% pass rate** / +19% over baseline. **Honest nuance:** 92% = `setup_completed` 85/92; the stricter adversarially-verified `not_guilty` rate is 77% (71/92).

**Key weakness to learn from, not copy:** XPU telemetry is **LLM-audit-derived, not execution-certified** — an XPU can reach "golden" tier on an LLM *guessing* it was adopted within a 5-step window (their own self-confirmation risk). Tiers are uncalibrated magic numbers; online ingestion silently drops telemetry.

### 2.1 Side-by-side

| Dimension | Current `LongTermMemoryManager` | SetupX XPU | GRM (proposed) |
|---|---|---|---|
| Store | flat JSONL, linear scan | pgvector + IVFFlat | JSONL now (ANN later); subgraph-serialized |
| Retrieval | pure top-k cosine | vector recall → LLM re-rank | vector + **hard deterministic subject gate** |
| Ranking | cosine only | `sim·(1+succ/hits)·tier` | same composite, **execution-certified inputs** |
| Telemetry | **none** | hits/successes/failures, **LLM-audit-graded** | hits/successes/failures, **host-certified** |
| Payload | NL `successful_fix` | typed `atoms` (bash) | frozen Contract/Blocker/Attempt subgraph |
| Graph use | `linked_memories` never traversed | n/a | **memory = subgraph grafted into live graph** |
| Wired into main loop | legacy ReAct only | yes | v1g contract-graph path |

---

## 3. The design: Grounded Repair Memory (GRM)

The opportunity: the agent **already has** an execution-certification pipeline SetupX lacks (`host_satisfied`, `derive_attempt_outcome`, `_auto_resolve_blockers`, the `_verified_test_run_passed` done-gate). So we can build memory telemetry that is **execution-certified, not LLM-guessed** — strictly better than XPU on its weakest axis — and use it to ground the graph's speculative half.

### 3.1 Trust-state model (core idea)

A new host function `graph.project_grounding(graph, node_id, host_satisfied)` returns one label **per node, per cycle, never stored** (mirrors `project_status`, `graph.py:89-94`):

- **SPECULATIVE** — LLM-authored this run, no certification, no prior. *(Today: the entire FAULT side.)*
- **RETRIEVED** — backed by a past-experience memory with execution-derived telemetry above a floor. *(A node that would be pure speculation now carries a calibrated cross-run prior.)*
- **GROUNDED** — execution-certified this run: a Contract in `host_satisfied`; an atomic Contract regex-promoted from real stdout; a host Attempt with rc-derived outcome; or a Blocker whose verbatim signature substring is confirmed present in its cited Attempt's stdout (new `signature_verified` check — closes the unverified-signature gap).

The frontier (`find_next_target_contracts`, `graph.py:154`) secondary-sorts **grounded > retrieved > speculative**. Host facts override priors every cycle.

### 3.2 Schema unification — a memory *is* a frozen certified subgraph

Stop maintaining two formats. A memory entry becomes a **MemorySubgraph**, serialized with the existing `node_to_dict`/`edge_to_dict`:

```
MemorySubgraph {
  id,
  nodes: (1 Contract, 1 Blocker, ≥1 Attempt),
  edges: (violates, addresses),          # closed EDGE_RULES untouched; depends_on NOT stored
  signature,
  embedding,
  provenance { source_repos, certified_by_command, distro, base_image, python },
  telemetry { hits, grounded_successes, grounded_failures, collect_only_successes, anti_pattern_hits }
}
```

Because node ids are deterministic (`ids.contract_id(kind, subject)`), a memory's Contract id (e.g. `contract:system_library:pg-config`) usually *already equals* the live node id — reuse is a natural merge, not a rename. The dead `Contract.evidence_refs` becomes the on-graph carrier of `[{attempt_id, memory_id, satisfied_via}]`.

Consumed two ways by phase: as a **prior annotation** on the matching live node (Phases 1–2, low risk), and as a **graft** that injects the whole subgraph before the Maintainer speculates (Phase 3, high risk).

### 3.3 Retrieval + binding (host-owned; planner stays pure)

The planner still emits only recipe/done/giveup and creates no nodes. Before each `planner.decide` in `run_v1` (`orchestrator.py` ~:109), a host step derives query targets **from the live graph frontier** (not a single last-failure):

1. For each `find_next_target_contracts` frontier node and each `root_blockers()` entry, build a failure query (`build_failure_query`, `memory_manager.py:853`) from the Blocker `signature` / Contract `subject` + last-400-char raw rc≠0 stdout.
2. **Hard deterministic gate (critical, defends `system-autoresolve-dormant`):** a candidate is eligible only if its stored subject — via `extract.extract_blocker_subject` — **equals a subject the live run just regex-promoted** (`promote_atomic_contracts`). Match on the *grounded extracted subject*, never the paraphrasable Maintainer signature.
3. Rank survivors by `composite = cosine · (1 + grounded_successes/max(hits,1)) · tier_boost` (SetupX shape, **execution-certified inputs**).
4. Render the top match under the matched node with `successful_fix` + persisted **anti-patterns**; record `RetrievalBinding{memory_id, target_contract_id, target_blocker_id, cycle, committed_revision}` on `current_map.retrieval_bindings`; bump `hits`.

### 3.4 THE grounding loop (the crux — reuses certification that already exists)

```
container exec (build_agent.run_recipe) → ActionLedger (real rc + stdout)
  → apply_deterministic: real probes (pip freeze / import sweep / system tools)
  → refresh_host_graph (projection.py:114-186):
        regex-promote atomics, recompute host_satisfied,
        _auto_resolve_blockers retires Blocker when subject now present
  → derive_attempt_outcome: rc-derived ok / failed / ok_but_still_blocked
  → attribute_grounded_outcomes (NEW, orchestrator.py ~:232):
        per RetrievalBinding, TWO host facts must agree (no LLM):
          (1) target_contract_id ∈ host_satisfied   (only real probes can do this)
          (2) an Attempt that `addresses` it, committed ≥ binding.committed_revision,
              outcome == ok, AND its command tokens overlap the memory's successful_fix
        → record_grounded_outcome(memory_id, success/failure, evidence)
        → Contract.evidence_refs += {attempt_id, memory_id, satisfied_via}
  → telemetry persisted → re-ranks composite on the NEXT retrieval, across repos
```

Three honesty guards (reuse existing boundaries):
- **`satisfied_via` tiering mirrors the done-gate** (`_verified_test_run_passed`, `maintainer.py:192-241`): full `grounded_successes` credit only for `import_sweep` / `package_probe` / `verified_pytest`; collect-only rc=0 increments a separate `collect_only_successes` at **half weight**.
- **Command-token overlap** disambiguates multi-binding: if two memories bind one contract, credit only the one whose `successful_fix` tokens overlap the certifying Attempt's commands; the other gets a `hit` only.
- **Journaled telemetry, merged once at run-end** via atomic `_write_all_memories` temp-replace — the JSONL store is not safe under parallel benchmark workers otherwise.

### 3.5 Why GRM beats the current speculative graph

Today: (1) the entire FAULT side is single-shot Maintainer LLM checked only structurally; (2) memory is write-once with zero counters, so a fix that worked 8× ranks no higher than one that never has, and `ok_but_still_blocked` is forgotten across runs.

GRM grounds **repair selection** with the same execution truth that gates `done_flag`. Three measurable wins, none from model self-report:
- **Proven-fix-first ordering** — frontier prefers the command that previously moved this obligation into `host_satisfied`.
- **Cross-run anti-pattern suppression** — `failed`/`ok_but_still_blocked` Attempts are persisted and surfaced as AVOID.
- **Populated audit trail** — `Contract.evidence_refs` records which command/memory certified each obligation.

---

## 4. Worked end-to-end example: `pg_config` / psycopg2 recurring across repos

**Run 1 (repo A declares `psycopg2-binary`).**
- Cycle 1: planner runs naive `pip install psycopg2` → fails `pg_config executable not found`. `extract.promote_atomic_contracts` creates `contract:system_library:pg-config` (subject from REAL stdout → grounded-extracted).
- Cycle 2: Maintainer authors a Blocker + `violates` edge (speculative).
- Cycle 3: planner runs `apt-get install -y libpq-dev && pip install psycopg2-binary`; system-tools probe now reports `pg_config`; `refresh_host_graph` puts the Contract into `host_satisfied`; `_auto_resolve_blockers` retires the Blocker; `derive_attempt_outcome` → ok.
- **At run end, `harvest_certified_subgraphs` emits M1** = {Contract:system_library:pg-config (evidence_refs=[A3.id, 'apt-get install -y libpq-dev']), Blocker(signature 'pg_config executable not found', signature_verified=True), Attempt(verified_effective: the libpq-dev fix), Attempt(anti-pattern: `pip install psycopg2`)}, telemetry{graft_count:0}, provenance{distro: debian, python: 3.11}.

**Run 2 (repo B, also psycopg2).**
- In `refresh_host_graph`, the live regex-promoted subject `pg-config` **hard-matches M1's grounded subject** (not the paraphrasable Maintainer signature).
- **Phase-2 behavior:** M1 attaches as a `prior`, rendered `RETRIEVED, host-certified 1/1: apt-get install -y libpq-dev; AVOID pip install psycopg2 (did NOT resolve)`. Planner runs the right fix in **cycle 1**, not cycle 3; `attribute_grounded_outcomes` certifies a grounded success → M1 → 2/2 across two distinct repos → golden tier.
- **Phase-3 behavior (later run):** M1 is grafted as a host patch *before* the Maintainer speculates, so the Maintainer never re-invents the Blocker and the planner never repeats the source-build dead end.

Two cycles saved, no wrong-root-cause edge, and the trust the planner placed in M1 is backed by two real container certifications — re-verified by the same `host_satisfied` pipeline that certifies any node, so **a bad memory can never fake `satisfied`**.

---

## 5. Phasing (cheapest, highest-leverage first)

### Phase 1 — Telemetry feedback loop + grounded repair *selection* (medium-small)
No new node types, no grafting, no node seeding — annotate existing frontier nodes only.
- `src/memory_manager.py`: stop popping `id` (`normalize_memory` :933), assign deterministic id from `_dedupe_key`; add `telemetry` + backfill on `load_memories:456`; composite ranking in `retrieve:396`; new `record_grounded_outcome` (reuse `_write_all_memories:839`, journaled); `format_retrieval_results:426` prints S/H. **~120 lines.**
- `src/envstate/world_model.py`: `RetrievalBinding` frozen dataclass + `retrieval_bindings` field (:69-86) + `merge_map:189` / `reset:180` passthrough. **~30.**
- `src/envstate/orchestrator.py`: pre-planner retrieval step before `planner.decide:109`; `attribute_grounded_outcomes` two-fact test after `derive_attempt_outcome` write-back (~:199-235). **~80.**
- `src/envstate/contracts/projection.py` + `validators.py`: populate `Contract.evidence_refs` on `host_satisfied` transition; stamp `Blocker.grounding` on retire in `_auto_resolve_blockers:70-111`. **~40.**
- `src/envstate/contracts/render.py`: annotate Next Target / Active Blockers with memory S/H. **~40.**
- `src/envstate/contracts/validation.py`: forbid maintainer `add_contracts` from setting `evidence_refs` (host-only). **~10.**
- `agent.py` (:1167-1180), `run_rat_benchmark.py` (:796-828), `multi_docker_eval_adapter.py` (:774-792): thread `memory_manager` + new `DOCKERAGENT_ENABLE_GRAPH_MEMORY` flag (default off, AND-ed under `enable_contract_graph`); flush near `_maybe_generate_long_term_memories`. **~40.**

**Independently shippable and A/B-able vs the v1g baseline.**

### Phase 2 — Prior overlay + 3-state grounding projection + `signature_verified` (medium)
- `src/envstate/contracts/graph.py`: `project_grounding()` (never stored, mirrors `project_status:89`); frontier ordering in `find_next_target_contracts:154`. **~40.**
- new `src/envstate/contracts/priors.py`: `attach_priors` per-cycle with the hard subject gate; cache by node id. **~150.**
- `Node.data["prior"]` block + `Blocker.data["signature_verified"]` host check (substring present in cited Attempt stdout). **~30.**
- `render.py` + `src/envstate/planner.py`: extend the `## Contract Graph` section of `PLANNER_SYSTEM_PROMPT` (:149-172) to define the three trust tiers and "never repeat a prior's anti-pattern". **~50.**

Still no grafting — priors only annotate nodes the planner could already reference.

### Phase 3 — Harvest + graft frozen certified subgraphs (medium-large; highest value/risk → last)
- new `src/envstate/contracts/memory_graph.py`: `MemorySubgraph`; `harvest_certified_subgraphs(final_map)`; `graft_subgraph → GraphPatch` (scope=`host`, via existing `apply_patch`/`validate_patch`); `composite_rank`. **~300.**
- `src/memory_manager.py`: `write_subgraphs`/`retrieve_subgraphs` via `node_to_dict`/`edge_to_dict`; keep flat-JSONL legacy as fallback. **~80.**
- `src/envstate/contracts/projection.py`: graft step after `sigs=_failure_signatures`, **before** the Maintainer pass; **applicability filter on provenance (distro/base-image/python)** — the negative-transfer guard. **~60.**
- `src/envstate/world_model.py`: `memory_feedback` plumbing (immutability-sensitive; cover with `refresh_host_graph` idempotency tests). **~30.**
- `agent.py` `_maybe_generate_long_term_memories`: call `harvest_certified_subgraphs` + `write_subgraphs`. `src/envstate/maintainer.py` `serialize_graph_for_maintainer`: surface grafted blockers so the LLM defers (`ids.blocker_id` determinism makes duplicates a `setdefault` no-op). **~40.**
- Stage internally: (3a) harvest+write (no live behavior change), (3b) graft+render, (3c) feedback close. `grafted_from` uses a **content hash** (not the colliding `slug`).

---

## 6. Risks & mitigations

- **Negative transfer (Phase 3's worst failure).** A Debian `apt libgl1` fix grafted onto Alpine. → applicability filter on `provenance` drops incompatible fixes pre-rank; `grounded_failures` auto-demote to cold tier; grafted nodes can never enter `host_satisfied` except via real probes, so a wrong graft fails loudly and self-corrects.
- **Cold-start seeding / poisoning.** → GRM does **not** seed nodes in Phases 1–2. Phase 3 only grafts when the deterministic subject gate matches a live regex-promoted subject — the fault is already proven present this run. We never predict a failure from the manifest alone.
- **Misattribution under multi-binding.** → command-token overlap; credit a `hit` only otherwise.
- **Telemetry corruption under parallel workers.** → per-run journaled deltas, single atomic merge at run-end.
- **Maintainer paraphrases signatures.** → match on grounded `extract_blocker_subject`, never the LLM signature; `signature_verified` so a paraphrased Blocker never reads as grounded.
- **Over-crediting weak satisfaction.** → `satisfied_via` tiering (collect-only half weight).
- **Scope confusion with legacy `__RETRIEVE_MEMORY__`.** → keep GRM strictly inside the v1g path behind `DOCKERAGENT_ENABLE_GRAPH_MEMORY`; leave legacy untouched as opportunistic secondary signal only.

---

## 7. Honest caveats — where this does NOT help

1. **Payoff is conditional on faults transferring across repos** — true at the **system/build/toolchain layer** (`pg_config`, `libGL.so.1`, headless OpenCV, version-specific wheels), false for **repo-specific** failures (bespoke conftest, first-party imports, one-off pins). For those, the subject never recurs, the gate never matches, telemetry stays `hits=0`, and GRM degrades gracefully to today's behavior — cosmetic, not harmful, but not a win.
2. **No single-run gain** — composite == raw cosine until the corpus accrues ≥5 certified hits. Validate on a **50-repo sweep with a warm store**, scored by `compute_essr.score_agent` (never `rat_results.json`).
3. **GRM grounds repair *selection*, not the Maintainer's causal diagnosis** (`root_or_downstream`, `depends_on`). If the dominant v1g defect is *wrong root-cause edges misdirecting the frontier* rather than *wrong fix selection*, leverage shifts to Phase 2's `signature_verified` + grounding-ordered frontier. Given the prior finding that the dominant v1 defect was the collect-only done-gate + lossy synthesis (both reinforced by GRM's done-gate-aligned tiering and proven-fix-first ordering), **Phase 1 is the right first bet — gated behind a measured A/B before funding 2–3.**

---

## 8. Open questions for discussion (codex)

1. **Is the deterministic subject gate too strict?** It requires the live run to *already* regex-promote a matching subject before a memory is eligible — which means memory can only *accelerate* a fault the host already grounded, never *pre-empt* one. Is pure-cosine fallback (with lower trust) worth the false-positive risk?
2. **Feasibility check first?** Before building, should we mine the existing v1g failure traces to confirm enough *recurring system-layer subjects* exist across the 50-set to justify GRM (tests caveat #1 on real data)? Recommended.
3. **Phase 1 sufficiency.** Does grounding *repair selection* alone (no grafting, no priors) move the honest ESSR enough to justify the parallel-worker telemetry plumbing, or is the value concentrated in Phase 3's graft?
4. **Store backend.** Stay on JSONL (linear scan) for the foreseeable corpus size, or jump to sqlite/FAISS now to avoid a later migration and the concurrency hazard?
5. **Credit attribution rigor.** Is command-token overlap a strong enough causal link, or do we need a stricter "the certifying Attempt *is* the bound Attempt" check (risking more undercounting)?
6. **Warm-store bootstrap.** Worth distilling an initial warm store from prior successful runs (SetupX ships 600), or grow purely online from this point?

---

## 9. Key references (this repo)

- Memory: `src/memory_manager.py` (:396 retrieve, :533 write, :729 relation-judge, :839 atomic write, :931 normalize)
- Memory glue: `agent.py` ~:391, ~:1978-2010, ~:2372-2383
- Contract graph schema: `src/envstate/contracts/schema.py`, `nodes.py`, `attempts.py`, `ids.py`
- Graph projection/host certification: `src/envstate/contracts/projection.py:114-186`, `graph.py:89-94/:154`, `validators.py:113-127`, `extract.py:37-52`
- Maintainer (speculation source) + done-gate: `src/envstate/maintainer.py:271-422`, `:192-241`
- Validation: `src/envstate/contracts/validation.py:23-116`
- Orchestrator loop: `src/envstate/orchestrator.py` (~:109 planner, ~:199-235 outcomes)
- Wiring: `run_rat_benchmark.py:796-828`, `multi_docker_eval_adapter.py:774-792`, `agent.py:3046`

## 10. Related prior findings
- Contract Graph V2 (concise 3-node/3-edge fault/repair overlay; host owns truth)
- Honest success def: real-success = `ebsr AND pass_rate≥0.8`; trust `compute_essr.score_agent`, never `rat_results.json`
- `system-autoresolve-dormant`: Maintainer paraphrases signatures → deterministic auto-resolve never fires (motivates the subject-gate + `signature_verified`)
