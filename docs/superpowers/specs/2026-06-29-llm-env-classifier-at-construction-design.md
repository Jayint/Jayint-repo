# LLM Environment Classifier at Initial Graph Construction (Slice C) — Design

**Date:** 2026-06-29
**Branch:** `john-planner-v3`
**Status:** approved (brainstorming, 2026-06-29)
**Extends:** `2026-06-25-six-tier-environment-world-model-design.md`, `2026-06-25-services-tier-design.md` (Config + Services tiers, deterministic). Resolves services-tier **§13 Q1** (the "should an inferred service carry a soft link?" open question).
**Lineage:** the recalled hybrid design (deterministic scan → compact bundle → LLM classifier → hint/candidate → runtime promotion → certify) + the existing typed-patch / promotion machinery (`patch.py`/`patch_gate.py`) that was built for Slice B and never wired to construction. **This = the missing wire.**

## 1. One line

Add a **default-off, one-time construction phase** that feeds the existing compact evidence bundle to the LLM once and **adds Hint/Candidate Service/Config/DataAsset nodes with soft (non-blocking) edges** for environment needs the deterministic scanners can't infer — all graph mutation through the existing `patch_gate`, so `python_deps/depgraph` stays LLM-free.

## 2. What exists today vs. what this adds

**Today (deterministic only).** `build_dep_graph` runs `scan_config` + `scan_services` (pure, no LLM). Their edge model (verified in code, matches the written services spec §4):
- **confirmed** service (corroborated by CI/compose) → **hard** `requires` edge (`service_scan.py:292-302`).
- **inferred** service (package-table guess, no corroboration) → **no edge** — node-only + `data["inducing_package"]`, advisory (`service_scan.py:308`).
- **Config** → hard `requires` edge (`config_scan.py:322`), but the node is advisory/non-actionable (`schedule._is_actionable` excludes CONFIG; certify proves presence only).

**The pieces already built but unwired.** `static_collect.collect_static_evidence` + `compact_bundle_json` produce the exact §3.2 bundle (with `evidence_id`s) — **zero production consumers today**. `patch.py`/`patch_gate.py` provide a typed `PatchProposal` + `admit_proposal(graph, proposal, known_evidence_ids)` gate that creates MISSING-only nodes with a `promotion ∈ {hint,candidate}` tag and `hard:false` edges, grounded on `evidence_ref ∈ known_evidence_ids`. The `make_llm_classifier` factory + the orchestrator's temp-0 `complete_fn` exist (runtime path).

**What this slice adds.** One envstate-layer phase that connects those pieces at construction: `bundle → LLM → PatchProposal → admit_proposal → enriched graph`. Additive — the deterministic nodes are untouched; the LLM dedups against them and contributes net-new nodes + soft edges (including soft edges onto the previously edgeless **inferred** nodes, closing services-tier §13).

## 3. Design

### 3.1 Placement (additive, purity-preserving)

A new one-time phase in `run_v3` (orchestrator), run **once after the deterministic graph is built and before the scheduling loop** — the seam where both `current_map.dep_graph` and `build_agent.client` exist (mirrors the runtime-classifier wiring at `orchestrator.py:481`). `build_dep_graph` and all of `python_deps/depgraph` stay **untouched and LLM-free**; the LLM call lives only in `src.envstate`. Off (flag off, or no client) → the phase is a no-op → **off-state byte-identical**.

### 3.2 Data flow (almost all reuse)

```
collect_static_evidence(repo)        # EXISTING (static_collect.py) — DeterministicHit[]
  -> compact_bundle_json(hits)       # EXISTING — {"goal", "deterministic_hits":[{evidence_id,file,kind,name,snippet}]}
  -> complete_fn(messages)           # EXISTING shape — temp-0, JSON-accept, built from build_agent.client/model
  -> parse to PatchProposal          # reuse patch.parse_patch_proposal (+ a thin normalizer, §3.4)
  -> sanitize(proposal, bundle_ids)  # NEW (envstate) — drop entries whose evidence_ref ∉ bundle_ids; dedup vs graph
  -> admit_proposal(graph, proposal, known_evidence_ids=bundle_ids)   # EXISTING gate (patch_gate.py:214)
  -> merge_map(dep_graph=enriched)   # EXISTING
```

The bundle's `evidence_id` set is passed as `known_evidence_ids`; the gate rejects any node/edge citing an id not in the bundle (the hallucination guard).

### 3.3 The LLM contract (maps R → existing machinery)

The recalled output shape (`requirements:[{type,id,state,check_command,evidence_refs,rationale}]`) maps onto the existing `PatchProposal` so we reuse `parse_patch_proposal`/`patch_gate` rather than inventing a parser:

| Recalled field | Maps to (patch machinery) |
|---|---|
| `type` (Service/Config/DataAsset) | `NodeSpec.type` (NodeType) |
| `id` (`service:postgres`) | `NodeSpec.id` (prefix-validated by `patch_gate._KIND_PREFIX`) |
| `state: HINT \| CANDIDATE` | **`NodeSpec.promotion: "hint" \| "candidate"`** (lowercased) — **NOT a `State`** (per the standing rule: Hint/Candidate/Active = `Node.data["promotion"]` + `Edge.data["hard"]`, never a `State` value). The created node is always `State.MISSING`. |
| `check_command` | `NodeSpec.check_command` (None allowed for advisory SERVICE) |
| `evidence_refs: [...]` | `NodeSpec.evidence_ref` (the gate validates ∈ bundle ids) |
| `rationale` | dropped (not stored; logging only) |
| edges (chain) | `EdgeSpec` in `add_edges` with **`hard: false`** |

`parse_patch_proposal` already falls back to `state` for `promotion` (`patch.py:93`); we add a thin normalizer (lowercase `promotion`; accept `evidence_refs` → first/`evidence_ref`) so the LLM may emit either the patch-native or the recalled shape.

### 3.4 Edge rule — **hardness follows confidence, not the detector**

> **confirmed / certifiable obligation → hard edge.  inferred / hint / candidate (all LLM-construction output) → soft edge.  active (hard) only via runtime/gate promotion.**

- Deterministic **confirmed** service edges stay **hard** (unchanged).
- Deterministic **inferred** services (today: no edge) **may receive a soft edge** from the LLM pass (connecting `inducing_package → service`), closing services-tier §13 — keeps the cross-tier chain visible while non-blocking.
- All LLM-emitted construction edges are **soft** (`hard:false`).
- Soft edges never block: `schedule._dependencies_satisfied` only gates on hard edges (invariant #10). So nothing the LLM adds at construction is hard-scheduled — exactly the "no hard scheduling from a single weak static clue" guardrail.
- **Promotion to active = hard edge**, performed only by the EXISTING runtime/gate path (residual handler on a real failure; discover-task for config). The construction LLM never emits active.

### 3.5 Node types & DataAsset (small adds)

Service/Config already supported by `patch_gate._KIND_PREFIX`. DataAsset needs:
- `NodeType.DATA_ASSET → "data:"` in `patch_gate._KIND_PREFIX` (and `_node_type` round-trip).
- `data_asset_id(name)` in `ids.py`.
- Layer mapping (tier 6, alongside Config).
- `check_command` = an LLM-supplied file-presence test (`test -f <path>`) when derivable; else `None` → a hint (certify skip-guards a check-less node, like advisory services).

### 3.6 Idempotency & sanitize-then-admit

- `apply_proposal` dedups by node id (first-writer-wins). An LLM `service:postgres` that `scan_services` already created is a **no-op** — the deterministic node keeps its confirmed status; the LLM contributes only net-new nodes + soft edges.
- `admit_proposal` is all-or-nothing (rejects the whole batch on any validation error). Because an LLM may emit one slightly-off entry among good ones, the envstate layer **sanitizes first**: drop any requirement/edge whose `evidence_ref ∉ bundle_ids` or whose endpoint/type is illegal, then admit the clean subset. The gate stays strict/pure; we just maximize useful yield. Dropped entries are logged.

### 3.7 Prompt guardrails (the "CI/CD is dangerous" rule)

The system prompt instructs: goal is **local install/test/run**, not deployment; deployment-only / release / secret-store / cache / optional-matrix signals → **hint only** unless corroborated by a test/CI service or a code env-read; every requirement needs a **real `check_command`** or it is a hint with `check=None`; **every requirement must cite ≥1 `evidence_ref` from the bundle** (ungrounded → dropped by sanitize).

### 3.8 Arm / toggle

New default-off flag `enable_llm_env_classifier` (arm e.g. `v3gc`), cascading like the existing orchestration flags. Off (or no client) → phase is a no-op → off-state byte-identical. Re-baseline (B-on vs B-off) after landing.

## 4. Scope

**In scope (v1):** the construction phase + sanitize + the DataAsset adds + the arm; LLM over the **existing** `static_collect` bundle (compose, GH-Actions services, `.env.example`, source `os.getenv`/`environ` reads); Hint/Candidate Service/Config/DataAsset nodes + soft edges; reuse `parse_patch_proposal`/`admit_proposal`.

**Out of scope (explicit follow-ups):**
- **Bundle source expansion** to the full recalled list (README/docs, Makefile/scripts, `.devcontainer`, `Dockerfile`, `.gitlab-ci`, `conftest.py`/fixtures, pydantic `BaseSettings`/decouple in `env_read`). v1's bundle `env_read` is `os.getenv`/`environ` only; the LLM partly compensates from `.env.example`. Expanding `static_collect` unlocks the rest — separate slice.
- **Replacing** the deterministic scanners (we stay additive — D1).
- **Auto-active at construction** (active stays a runtime/gate-only promotion).
- **A reasoning/causal plane** (Slice B+ / Phase 5).
- **No new `State` value** — Hint/Candidate are `promotion` + soft edge only.

## 5. Backward compatibility

- Flag off or no LLM client → phase skipped → graph identical to today → off-state byte-identical.
- `python_deps/depgraph` unchanged except the small DataAsset `_KIND_PREFIX`/`ids` additions (additive, default-safe; no behavior change when no DataAsset node is proposed).
- Deterministic confirmed/inferred nodes + their edges unchanged; the LLM only adds (dedup'd) nodes + soft edges.
- v1 (`run_v1`) untouched (the phase lives in `run_v3` only).
- The standing rule holds: the LLM **reads** host-certified state and **proposes**; it never writes `SATISFIED` (the gate structurally forbids it).

## 6. Testing (TDD)

- **Normalizer/parse:** recalled-shape JSON (`state:HINT`, `evidence_refs`) → `PatchProposal` with `promotion="hint"`, `evidence_ref` set; patch-native shape also parses; junk/empty → empty proposal.
- **Sanitize:** entries with `evidence_ref ∉ bundle_ids` dropped; the clean subset survives; whole-batch not lost to one bad entry.
- **Enrichment phase (fake `complete_fn`):** a fixed candidates JSON over a real-ish graph adds the expected Hint/Candidate Service/Config/DataAsset nodes with **soft** edges (incl. a soft edge onto a pre-existing edgeless inferred node); deterministic nodes unchanged; dedup no-op for an id the scanner already made.
- **Non-blocking:** the added soft nodes are absent from `scheduler_frontier` and do not block `_dependencies_satisfied` for the test goal.
- **Grounding/safety:** a hallucinated `evidence_ref` and a `SATISFIED`/illegal-type proposal are rejected/dropped; graph never gains a SATISFIED node.
- **DataAsset:** a `data:` node round-trips through `patch_gate` (prefix, type, layer).
- **Off-state:** flag off (and client-present-but-flag-off) → byte-identical graph.
- **Manual real-LLM smoke (optional):** run the phase on the cloned `full-stack-fastapi-template` (compose `db: postgres:18` + pydantic settings) and inspect the added hint/candidate nodes + soft edges + rendered slice.

## 7. File structure / integration points (grounded 2026-06-29)

```text
src/envstate/env_classifier.py          NEW — the construction phase: build complete_fn (reuse llm_response),
                                          collect_static_evidence -> compact_bundle_json -> LLM -> normalize ->
                                          parse_patch_proposal -> sanitize(bundle_ids) -> admit_proposal -> graph.
                                          (envstate layer = the allowed LLM bridge; python_deps stays pure.)
src/envstate/orchestrator.py            run_v3: one-time call to the phase before the loop, gated by the new flag,
                                          when current_map.dep_graph and build_agent.client exist; merge_map result.
agent.py                                 new flag enable_llm_env_classifier (cascade like the others); pass to run_v3.
src/python_deps/depgraph/patch_gate.py  add NodeType.DATA_ASSET -> "data:" to _KIND_PREFIX (+ _node_type).
src/python_deps/depgraph/ids.py         add data_asset_id(name).
# REUSE (unchanged): static_collect.{collect_static_evidence,compact_bundle_json}; patch.parse_patch_proposal;
#   patch_gate.{validate_proposal,apply_proposal,admit_proposal}; llm_response.complete_with_retry;
#   schedule._dependencies_satisfied (soft-edge honoring already correct).
# UNCHANGED: build_dep_graph, scan_config, scan_services, certify, run_v1, the done-gate, world_model.Task.
```

## 8. Decisions log

- Hybrid, **additive** classifier (not replace) — deterministic scanners keep creating nodes; LLM layers on top, dedup'd (user, 2026-06-29).
- Node types **Service + Config + DataAsset** (user, 2026-06-29); DataAsset via a small `_KIND_PREFIX`/`ids` add.
- Bundle **as-is for v1**; full source expansion is a follow-up (user, 2026-06-29).
- **Edge rule: hardness follows confidence** — confirmed→hard, inferred/LLM→soft, active only via runtime/gate. Resolves services-tier §13 (user, 2026-06-29).
- R's **`state: HINT/CANDIDATE` → `promotion` tag + soft edge**, never a new `State` (standing rule).
- **Sanitize-then-admit** over strict all-or-nothing, to survive a single LLM slip (recommended; user to confirm).
- Default-off arm; off-state byte-identical; LLM bridge lives in `src.envstate` so `python_deps/depgraph` stays LLM-free.
