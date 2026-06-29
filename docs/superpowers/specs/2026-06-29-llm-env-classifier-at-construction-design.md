# LLM Environment Classifier at Initial Graph Construction (Slice C) — Design

**Date:** 2026-06-29
**Branch:** `john-planner-v3`
**Status:** approved (brainstorming, 2026-06-29) — **revised to the hard-delete (replace) model**
**Extends / supersedes:** the Config + Services tiers of `2026-06-25-six-tier-environment-world-model-design.md` and `2026-06-25-services-tier-design.md`. This **replaces** their deterministic node-creation with an LLM semantic classifier (the recalled hybrid design's "regex/AST evidence → LLM classifier → hint/candidate → promotion", v1). It resolves services-tier §13 (inferred → soft edge) by making *all* construction edges soft.
**Lineage:** the recalled hybrid design + the existing typed-patch/promotion machinery (`patch.py`/`patch_gate.py`, built for Slice B, never wired to construction) + the existing-but-unconsumed evidence bundle (`static_collect.py:27`).

## 1. One line

**Delete** the deterministic Config/Service node-creators (`scan_config`/`scan_services`, build.py Stages 3c/3d/3e) and replace them with an **LLM semantic classifier**: the deterministic file parsers feed a compact evidence bundle, the LLM classifies it into **Hint/Candidate** Service/Config/DataAsset nodes with **all-soft edges**, applied through the existing `patch_gate`. The LLM proposes; the host validates and applies; `python_deps/depgraph` stays LLM-free.

## 2. The finding this closes (and what changes)

> *"Initial config/service construction is still mostly deterministic direct graph mutation. The bundle exists (static_collect.py:27) but build still directly calls `scan_config`/`scan_services` (build.py:297). The 'regex/AST evidence → LLM classifier → hint/candidate → promotion' design is not wired."*

**Deleted (the deterministic node-creators):**
- `scan_config()` / `_config_node()` (config_scan.py) and `scan_services()` / `_service_node()` / the curated `package→service` table / the confidence ranking / the structural-edge creation (service_scan.py).
- `attach_in_image_provisioning()` **call** in build (Stage 3e) — the armed action layer, which read confirmed service nodes that no longer exist at build time.
- build.py Stages **3c / 3d / 3e**.

**Kept (the deterministic *parsers*, now pure evidence collectors):**
- `scan_ci_services`, `scan_compose_services` (service_scan.py); `parse_env_example`, `scan_env_reads` (config_scan.py). These already feed `static_collect.collect_static_evidence` — that becomes their only consumer.

**Added:**
- a `package` hit kind in the bundle (so the LLM can do the dep-induced inference the curated table used to, e.g. `psycopg2 → postgres`) — `collect_static_evidence(repo_path, graph)`.
- the LLM classifier phase (envstate) — the sole Config/Service/DataAsset source.
- `NodeType.DATA_ASSET → "data:"` in `patch_gate._KIND_PREFIX` + `ids.data_asset_id`.

## 3. Design

### 3.1 Placement (the LLM is the sole classifier; `python_deps` stays pure)

`build_dep_graph` no longer emits Config/Service nodes. The classifier runs **after `build_dep_graph` returns and before the advisory is rendered**. **Placement (refined during plan grounding — see the plan):** inside `build_advisory_for_repo` (advise.py), between `build_dep_graph` and `render_dep_graph_advisory`, via an **injected `classify` callback** — `advise.py` stays LLM-free (it only invokes an opaque `Callable`), and `agent.py` builds the callback from `self.client`/`self.model`. Preferred over a `run_v3` phase because it is where build+render already co-occur, it has `repo_path`, and it lets *both* the scheduler graph and the advisory string include the LLM nodes (a `run_v3` phase would leave the advisory stale and lacks `repo_path`). The LLM call lives only in `src.envstate`; `python_deps/depgraph` makes no LLM call (only the small additive `_KIND_PREFIX`/`ids` helpers change there).

**This phase is the default Config/Service/DataAsset source when an LLM client is present** (not a default-off experimental arm — there is no deterministic fallback). A flag `enable_llm_env_classifier` exists as an explicit *disable*; with no client the phase is skipped and **those tiers are simply absent** (a deliberate, accepted consequence — see §5).

### 3.2 Data flow (the recalled pipeline, mostly reuse)

```
collect_static_evidence(repo, graph)   # parsers + NEW package hits -> DeterministicHit[]
  -> compact_bundle_json(hits)         # {"goal", "deterministic_hits":[{evidence_id,file,kind,name,snippet}]}
  -> complete_fn(messages)             # temp-0, JSON-accept, from build_agent.client/model (reuse llm_response)
  -> parse to PatchProposal            # reuse patch.parse_patch_proposal + thin normalizer (§3.3)
  -> sanitize(proposal, bundle_ids)    # drop entries whose evidence_ref ∉ bundle_ids / illegal; dedup
  -> admit_proposal(graph, proposal, known_evidence_ids=bundle_ids)   # EXISTING gate
  -> merge_map(dep_graph=enriched)
```

The bundle `evidence_id` set is the `known_evidence_ids` — the gate rejects any node/edge citing an id not in the bundle (hallucination guard).

### 3.3 The LLM contract (maps the recalled shape → existing machinery)

| Recalled field | Maps to |
|---|---|
| `type` (Service/Config/DataAsset) | `NodeSpec.type` |
| `id` (`service:postgres`, `config:DATABASE_URL`, `data:fixtures.db`) | `NodeSpec.id` (prefix-validated by `_KIND_PREFIX`) |
| `state: HINT \| CANDIDATE` | **`NodeSpec.promotion: "hint" \| "candidate"`** (lowercased) — **never a `State`** (standing rule: Hint/Candidate/Active = `Node.data["promotion"]` + `Edge.data["hard"]`). The node is always `State.MISSING`. |
| `check_command` | `NodeSpec.check_command` (None allowed for advisory SERVICE) |
| `evidence_refs:[...]` | `NodeSpec.evidence_ref` (validated ∈ bundle ids) |
| `rationale` | dropped (logging only) |
| edges (the cross-tier chain, e.g. `pkg:psycopg2 → service:postgres`) | `EdgeSpec` in `add_edges`, **`hard:false`** |

`parse_patch_proposal` already accepts `state` as a `promotion` fallback (patch.py:93); a thin normalizer lowercases `promotion` and accepts `evidence_refs → evidence_ref`.

### 3.4 Edge rule — **all construction edges are soft**

Every node the classifier appends gets **soft** edges (`hard:false`). There is no confirmed=hard / inferred=no-edge distinction at construction anymore (that lived in the deleted scanners). Consequences:
- Soft edges never block: `schedule._dependencies_satisfied` gates only on hard edges (invariant #10). Nothing the LLM adds at construction is hard-scheduled — the "no hard scheduling from a single weak static clue" guardrail holds by construction.
- The cross-tier chain (`pkg:psycopg2 → service:postgres → config:DATABASE_URL`) is now expressed as soft edges — visible to the agent, non-blocking. (This is the §13 resolution: inferred links exist, softly.)
- **Promotion to active = a hard edge**, performed only by the EXISTING runtime/gate path (residual handler on a real failure; discover-task for config). The construction LLM never emits active.

### 3.5 Node types & DataAsset

Service/Config already supported by `_KIND_PREFIX`. DataAsset adds: `NodeType.DATA_ASSET → "data:"` (+ `_node_type` round-trip), `data_asset_id(name)` in `ids.py`, Layer mapping (tier 6). `check_command` = a file-presence test (`test -f <path>`) when derivable, else `None` (a hint; certify skip-guards a check-less node).

### 3.6 Sanitize-then-admit

`admit_proposal` is all-or-nothing (any validation error rejects the whole batch). Because an LLM may emit one off entry among good ones, the envstate layer **sanitizes first**: drop any requirement/edge whose `evidence_ref ∉ bundle_ids` or whose type/endpoint is illegal, then admit the clean subset. Gate stays strict/pure; dropped entries logged.

### 3.7 Prompt guardrails (the "CI/CD is dangerous" rule)

System prompt: goal is **local install/test/run**, not deployment; deployment-only / release / secret-store / cache / optional-matrix signals → **hint only** unless corroborated by a test/CI service or a code env-read; every requirement needs a **real `check_command`** or it is a hint with `check=None`; every requirement must cite ≥1 `evidence_ref` from the bundle (ungrounded → dropped). The LLM does the `psycopg2 → postgres` dep-induced inference from the new `package` hits (replacing the curated table).

## 4. Scope

**In scope (v1):**
- **Delete** the deterministic node-creators + build Stages 3c/3d/3e (§2).
- Refactor `config_scan.py` / `service_scan.py` to **parser-only** modules (keep the four parsers; remove `scan_config`/`scan_services`/`_config_node`/`_service_node`/the table/edges).
- `collect_static_evidence(repo, graph)` + a `package` hit kind.
- the `env_classifier` phase + the DataAsset `_KIND_PREFIX`/`ids` adds.
- Sequence in run_v3: `build → enrich → (advisory/loop)`.
- Reuse `parse_patch_proposal` / `admit_proposal` / `complete_with_retry`.

**Out of scope (explicit follow-ups):**
- **Bundle source expansion** to the full recalled list (README/docs, Makefile/scripts, `.devcontainer`, `Dockerfile`, `.gitlab-ci`, `conftest`/fixtures, pydantic `BaseSettings`/decouple). v1 keeps today's parsers + the package hit; the LLM partly compensates from `.env.example`.
- **Re-homing the armed service action layer** (`attach_in_image_provisioning` / start_recipe / binding-config) onto the LLM's post-build service nodes — that's where the one *hard* gate (binding-config waits for service) will live. Its build call is removed here; the function is left unwired (deletion decided when the action layer is rebuilt).
- **Auto-active at construction** (active stays runtime/gate-only).
- A reasoning/causal plane.

## 5. Backward compatibility & risk (this is an invasive change)

- **Off-state is NOT byte-identical.** The deterministic config/service tiers are gone; with the flag off or no client, the graph lacks those tiers. This breaks the codebase's usual "flag-off → byte-identical" invariant by design, and removes the flag-based LLM-vs-deterministic A/B (A/B now requires a git-revert).
- **Config/Service/DataAsset detection requires an LLM client.** Headless/cron runs without a model configured, or an API outage, yield no such tiers (today they'd be present deterministically). Accepted (the benchmark harness always has a client).
- **`build_dep_graph`'s output changes for every consumer.** It no longer emits Config/Service nodes; the advisory builder (advise.py:327) and any direct consumer see those tiers only after enrichment → the run_v3 sequence must be `build → enrich → advise`.
- **Test churn.** `test_config_scan.py` / `test_service_scan.py` and build-graph assertions for Config/Service nodes are deleted or rewritten (parser tests kept; node-creation tests removed). New tests cover the classifier phase.
- **Reliability tradeoff.** A confirmed CI `services: postgres` (ground truth) is now an LLM-graded soft hint/candidate, active only on a runtime failure — consistent with the recalled policy ("explicit CI → CANDIDATE; active from runtime"), but it does move a deterministic certainty behind a probabilistic classifier.
- The trust boundary holds: the LLM proposes a `PatchProposal`; the gate validates (never `SATISFIED`; promotion ∈ {hint,candidate}; evidence-grounded; edges legal) and the host applies. v1 (`run_v1`) untouched.

## 6. Testing (TDD)

- **Parsers survive:** `scan_ci_services`/`scan_compose_services`/`parse_env_example`/`scan_env_reads` still tested (kept).
- **Bundle:** `collect_static_evidence(repo, graph)` includes `package` hits from the graph's PACKAGE nodes; bundle JSON shape stable.
- **Normalizer/parse:** recalled-shape JSON (`state:HINT`, `evidence_refs`) → `PatchProposal` (`promotion="hint"`, `evidence_ref`); patch-native shape also parses; junk → empty.
- **Sanitize:** entries with `evidence_ref ∉ bundle_ids` dropped; clean subset survives; one bad entry doesn't lose the batch.
- **Classifier phase (fake `complete_fn`):** fixed candidates JSON over a built graph adds the expected Hint/Candidate Service/Config/DataAsset nodes with **soft** edges (incl. `pkg → service` chain); never `SATISFIED`; dedup no-op for an existing id.
- **Non-blocking:** added soft nodes absent from `scheduler_frontier`; don't block `_dependencies_satisfied` for the test goal.
- **Deletion:** `build_dep_graph` no longer emits Config/Service nodes (assert the old node-creation tests are gone/replaced; build returns a graph without those tiers absent the phase).
- **DataAsset:** a `data:` node round-trips through `patch_gate`.
- **No-client:** phase skipped cleanly (no crash; tiers simply absent).
- **Manual real-LLM smoke:** the cloned `full-stack-fastapi-template` (compose `db: postgres:18` + pydantic settings + `psycopg`) → inspect the LLM's hint/candidate nodes, the `pkg:psycopg → service:postgres` soft chain, and the rendered slice.

## 7. File structure / integration points (grounded 2026-06-29)

```text
src/python_deps/depgraph/build.py        REMOVE Stages 3c/3d/3e (scan_config, scan_services,
                                          attach_in_image_provisioning calls, ~lines 297-310).
src/python_deps/depgraph/config_scan.py  DELETE scan_config/_config_node (+ project/package-induced
                                          node creation, edges); KEEP parse_env_example, scan_env_reads
                                          (+ framework reads) as evidence parsers.
src/python_deps/depgraph/service_scan.py DELETE scan_services/_service_node/package-table/edges/
                                          (call-site of) attach_in_image_provisioning; KEEP
                                          scan_ci_services, scan_compose_services as parsers.
src/python_deps/depgraph/static_collect.py  collect_static_evidence(repo, graph): add `package` hit kind.
src/python_deps/depgraph/patch_gate.py   add NodeType.DATA_ASSET -> "data:" to _KIND_PREFIX (+ _node_type).
src/python_deps/depgraph/ids.py          add data_asset_id(name).
src/envstate/env_classifier.py           NEW — build complete_fn (reuse llm_response); collect_static_evidence
                                          -> compact_bundle_json -> LLM -> normalize -> parse_patch_proposal
                                          -> sanitize(bundle_ids) -> admit_proposal -> graph.
src/envstate/orchestrator.py             run_v3: one-time classifier phase after build, before advisory/loop,
                                          when client present; merge_map result. Sequence build -> enrich -> advise.
agent.py                                 enable_llm_env_classifier flag (explicit disable; default = on when
                                          a client exists); pass to run_v3.
# REUSE unchanged: patch.parse_patch_proposal; patch_gate.{validate,apply,admit}_proposal;
#   llm_response.complete_with_retry; schedule._dependencies_satisfied (soft-honoring already correct).
# UNCHANGED: certify, run_v1, the done-gate, world_model.Task.
```

## 8. Decisions log

- **Hard-delete** the deterministic Config/Service node-creators; LLM is the sole classifier (user, 2026-06-29). Parsers kept as evidence collectors.
- **All construction edges soft**; confirmed/inferred distinction removed; active only via runtime/gate (user, 2026-06-29). Resolves services-tier §13.
- **Package evidence** added to the bundle so the LLM keeps dep-induced service inference (user, 2026-06-29).
- Node types **Service + Config + DataAsset** (user, 2026-06-29).
- Bundle **as-is + package hit** for v1; full source expansion deferred.
- R's **`state: HINT/CANDIDATE` → `promotion` + soft edge**, never a `State` (standing rule).
- **LLM-required / off-state not byte-identical accepted** (user, 2026-06-29) — no deterministic fallback; flag is an explicit disable.
- **Armed service action layer** (attach_in_image_provisioning) unwired here; re-homed onto LLM nodes when the action layer is rebuilt (deferred).
- Sanitize-then-admit over strict reject-all (recommended).
