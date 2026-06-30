# V3-only branch — minimal file manifest

**Goal:** a branch that contains *only* the code responsible for the v3 (GSM) environment-builder
— graph construction → script materialization → block execution → host certification →
typed repair → maturity gates — that (a) tells the whole architectural story and (b) still
runs end-to-end.

**Source of truth for this map:** four read-only mappers over `john-planner-v3` @ `john-planner-v3`
(belief plane, proposal plane, truth plane, e2e/legacy boundary), 2026-06-30.

---

## 0. The key realization: the clean entrypoint already exists

`scripts/l2_repair_loop_smoke.py` constructs the **entire** v3 loop directly — graph → `Sandbox`
→ `BuildAgent` → `DeterministicMaintainer` → `run_v3` — **without touching `agent.py`/`DockerAgent`**.
That means we do NOT have to untangle `DockerAgent` (1908-line `run`, the v1/v2 ReAct arms, the
load-bearing `getattr(self, "enable_*", ...)` guards pinned by tests). The v3 branch's entrypoint is
a **descendant of l2_smoke**, and the whole legacy DockerAgent surface becomes *excludable*.

This turns the job from "deep refactor" into **"prune to the manifest + sever 4 heavy drags."**

The `run_v3` contract (from the live driver):

```python
final_map, stop = run_v3(
    agent,                              # V3BuildAgent (propose-only; synthesizer=None)
    maintainer=maintainer,             # DeterministicMaintainer(v3_only=True)
    initial_world_map=world_map,       # initial_map(..., dep_graph=graph)
    ledger=ledger,                     # ActionLedger
    sandbox_execute=sandbox.execute,   # (cmd)->(ok,str)  MUTATING
    probe=probe,                       # ()->EnvSnapshot via probe_env
    manifest=manifest,                 # parse_manifests(repo)
    exec_readonly=sandbox.exec_readonly,  # (cmd)->(rc,str)  PROBE
    enable_script_materialization=True,   # v3 hardwired ON
    enable_binding_install=True,          # fresh-replay gate — hardwired ON for the story
    reset_to_base=sandbox.reset_to_base,
    run_install_script=sandbox.run_install_script,
)
```

---

## 1. The three-plane manifest

Disposition legend: **AS-IS** = copy unchanged · **SLIM** = extract the v3 slice into a new small
module, drop the v1 remainder · **NEW** = write fresh · **EXCLUDE** = do not bring.

### Belief plane — `src/python_deps/` (LLM-free, envstate-free — verified clean)

All AS-IS. This is the irreducible graph core; it cannot be meaningfully shrunk because it *is*
the graph builder + renderer. Z3 is NOT here (closure is uv.lock-based) — exclude the parallel
`python_deps/{models,graph,z3_adapter,resolver,constraints,report,pypi_metadata,external_graph}`.

`depgraph/` (build + render + patch + schedule + runtime classify):

| File | ~LoC | Role in story |
|---|---|---|
| `schema.py` | 320 | The graph: Node/Edge/State/Layer/EDGE_RULES — *beliefs* |
| `ids.py` | 60 | Node-id constructors |
| `executor.py` | 127 | `Executor` protocol + Local/Docker executors |
| `emit.py` | 250 | topo_order, `_is_reciped`, `_apt_name`, `_pip_spec` |
| `build.py` | 324 | `build_dep_graph` — the 5-stage constructor |
| `scan.py` | 167 | static import scan |
| `roots.py` | 156 | resolver root selection |
| `naming.py` | 54 | import→dist name helper |
| `resolve.py` | 371 | uv.lock closure + import→package link |
| `resolve_lock.py` | 468 | pure uv.lock parser |
| `resolve_errors.py` | 362 | lock-drop error nodes |
| `resolve_link.py` | 135 | import→package link derivation |
| `probe.py` | 463 | install_closure / import_probe (container) |
| `ldd_probe.py` | 244 | DT_NEEDED native-lib discovery |
| `relink.py` | 145 | certified import links |
| `apt_resolve.py` | 117 | soname→apt |
| `apt_verify.py` | 171 | release-aware apt name reconcile |
| `certify.py` | 117 | **SOLE STATE-WRITER** (`SATISFIED`/`MISSING`) — *truth flips here* |
| `seed.py` | 108 | predicted native hints |
| `tables.py` | 97 | curated Debian tables |
| `pins.py` | 84 | lock-era exclude-newer pin |
| `build_script.py` | 214 | `render_build_script` — *the projection of belief* |
| `advise.py` | 331 | `build_advisory_for_repo` + `_best_evidence_line` (lazy dep of renderer) |
| `block.py` | 76 | `Block`, `compile_blocks`, `compile_replay_blocks` — *artifact* |
| `script.py` | 56 | `parse_setup_sh` / `render_setup_sh` round-trip |
| `patch.py` | 116 | **`PatchProposal`** — the LLM's one output shape |
| `patch_gate.py` | 253 | **PatchGate** — validate/apply/admit; never writes SATISFIED |
| `schedule.py` | 123 | `scheduler_frontier`, `frame_obligation` — *inner-loop frontier* |
| `req_slice.py` | 149 | requirement slice rendered into repair scope |
| `check_quality.py` | 24 | anti-weakening guard (trivial-check detector) |
| `evidence_log.py` | 45 | `Evidence` / `EvidenceBundle` |
| `static_collect.py` | 87 | CI/config/service static hints (classifier input) |
| `runtime_classify.py` | 153 | stdout→typed `Discovery` |
| `runtime_ingest.py` | 188 | runtime failure → graph updates |

`python_deps/` siblings (AS-IS, pure utilities, no legacy coupling):
`evidence.py` (339) · `import_mapping.py` (83) · `import_graph.py` (202) · `failure_classifier.py` (217)

### Proposal plane — the LLM (one verb: propose a typed graph change)

| File | ~LoC | Disposition | Role |
|---|---|---|---|
| `src/envstate/env_classifier.py` | 124 | AS-IS | construction-time classify callback |
| `src/envstate/repair_scope.py` | 91 | AS-IS | `RepairScope` §9 packet + render |
| `src/envstate/repair_loop.py` | 77 | AS-IS | bounded loop — **all termination guards live here** |
| `src/envstate/build_agent.py` → **`v3_build_agent.py`** | 1088 → ~250 | **SLIM** | extract `propose()` + `_extract_worker_action`, `_truncate_output`, `V3_PROPOSE_SYSTEM_PROMPT`; **drop** `run()`/`run_recipe()`/`_append_ledger_event` (severs synthesizer + world_model + ledger module-level drags) |
| `src/envstate/llm_response.py` | ~200 | AS-IS | `complete_with_retry` — the single LLM wire |
| `src/envstate/llm_classifier.py` | 103 | AS-IS | runtime residual classifier (§6) |
| `src/envstate/jsonutil.py` | ~small | AS-IS | `extract_json_object` |
| `src/envstate/diagnostics.py` | ~small | AS-IS | `log_llm_exchange` (stdlib-only) |

Every LLM call funnels through `llm_response._create_with_backoff → client.chat.completions.create`.
The proposal plane imports the belief plane only through `patch`, `patch_gate`, `static_collect`,
`req_slice`, `schema`. It never imports the truth plane (the loop is the caller).

### Truth plane — host execution + sole-writer certify + gates

| File | ~LoC | Disposition | Role |
|---|---|---|---|
| `src/sandbox.py` | 1047 | AS-IS | the only Docker I/O: `execute`, `exec_readonly`, `reset_to_base`, `run_install_script`, `InstallResult` |
| `src/envstate/block_emit.py` | 53 | AS-IS | graph→setup.sh→blocks→run→certify, dual-write ledger |
| `src/envstate/script_runner.py` | 90 | AS-IS | `run_blocks` — block rc0 NEVER writes SATISFIED; only host check does |
| `src/envstate/depgraph_live.py` | 212 | AS-IS | `certify_refresh` / `emit_drain` / `repair_failed_nodes` — belief↔container bridge |
| `src/envstate/install_localizer.py` | 79 | AS-IS | Stage-2 install-failure localize + `certify_reciped_only` |
| `src/envstate/gates.py` | 93 | AS-IS | `evaluate_installability_gate` + `evaluate_testability_gate` |
| `src/envstate/maintainer.py` → **`done_gate.py`** | 774 → ~150 | **SLIM** | extract `_verified_test_run_passed` + its 6 helpers + `_progress_synced_with_done`; **drop** the LLM `Maintainer` class and contract-graph machinery |
| `src/synthesizer.py` → **`test_oracle.py`** | 4084 → ~?? | **SLIM (spike first)** | extract `is_test_command`, `analyze_test_run`, `observation_pass_ratio` (what the done-gate's `_get_detector()` needs). **This is the one extraction whose surface must be measured before committing.** |
| `src/envstate/deterministic_maintainer.py` | 126 | AS-IS | `DeterministicMaintainer(v3_only=True)` — `_v3_done_gate` |
| `src/envstate/ledger.py` | 65 | AS-IS | `ActionLedger` append-only event store |
| `src/envstate/cleanroom.py` | 99 | AS-IS (optional) | fresh-image clean-room proof (not yet in loop) |

### Foundational / shared (MUST-CARRY — used by every plane)

| File | ~LoC | Disposition | Note |
|---|---|---|---|
| `src/envstate/world_model.py` | ~?? | AS-IS | `WorldModelMap`, `initial_map`, `Task`, `TaskReport`, `PlannerDecision`, `merge_map` |
| `src/envstate/contracts/graph.py` (`ContractGraph` only) | ~?? | AS-IS | embedded in `WorldModelMap` even on v3 (never *written* — `_v3_done_gate` skips it). Carry just this file; **exclude** the rest of `contracts/` |
| `src/envstate/orchestrator.py` → **`v3_loop.py`** | 983 → ~450 | **SLIM** | extract `run_v3` + its closures (`_dep_emit_phase`, `_runtime_ingest_phase`); **drop** `run_v1` and `EnvStateOrchestrator` |
| `src/envstate/graph_scheduler.py` | 118 | AS-IS | `next_decision` — replaces the LLM planner |
| `src/envstate/_loop_common.py` | ~small | AS-IS | `host_refresh_facts` |
| `src/envstate/snapshot.py` | ~?? | AS-IS | `probe_env` |
| `src/envstate/manifest.py` | ~?? | AS-IS | `parse_manifests` |
| `src/envstate/constants.py` | ~small | AS-IS | `VERIFY_TEST_CMD` |

### Driver — NEW

| File | Disposition | Role |
|---|---|---|
| `run_v3_e2e.py` (descendant of `scripts/l2_repair_loop_smoke.py`) | **NEW** | the single legible entrypoint: build graph (with `classify=`) → Sandbox → V3BuildAgent → DeterministicMaintainer → `run_v3` → evaluate gates → emit `setup.sh` |

---

## 2. The four surgical extractions (the only real work)

Everything else is copy-or-exclude. These four sever the heavy legacy drags:

1. **`test_oracle.py` ⊂ `synthesizer.py` (4084 LoC).** *Highest value, highest uncertainty.*
   The done-gate's only need from the 4084-line v1 synthesizer is test-output parsing
   (`is_test_command`, `analyze_test_run`, `observation_pass_ratio`). **Spike this first** — measure
   the true transitive surface; if it's clean (~200-400 LoC) extract it, if it's tangled, carry a
   trimmed `synthesizer.py` instead. This decision gates how "clean" the branch can be.
2. **`v3_build_agent.py` ⊂ `build_agent.py` (1088 LoC).** Extract `propose()` + 3 helpers + the
   system prompt. Confirmed clean — `propose` already runs with `synthesizer=None` and only needs
   `repair_scope.render`, `jsonutil`, `patch.parse`, `llm_response`, `diagnostics`.
3. **`v3_loop.py` ⊂ `orchestrator.py` (983 LoC).** Extract `run_v3` + its two phase closures; drop
   `run_v1` + `EnvStateOrchestrator`. `run_v3` is already a standalone function — low coupling to
   `run_v1`'s scope.
4. **`done_gate.py` ⊂ `maintainer.py` (774 LoC).** Extract `_verified_test_run_passed` + 6 helpers +
   `_progress_synced_with_done`; drop the LLM `Maintainer` class.

---

## 3. Story → file table (every claim has a home)

| Architectural claim | Where it lives |
|---|---|
| Graph is *belief* (soft hypotheses) | `depgraph/schema.py`, `build.py`; SOFT construction edges via `env_classifier.py` |
| LLM populates beliefs at construction (one verb) | `env_classifier.py` → `patch.py` → `patch_gate.admit_proposal` |
| Script is the *deterministic projection* of belief (not truth) | `depgraph/build_script.py` `render_build_script` |
| Inner loop: render→execute→certify→repair | `v3_loop.py` `_dep_emit_phase` → `block_emit` → `script_runner.run_blocks` → `repair_loop` |
| Host is the **only** truth; sole writer | `depgraph/certify.py:81` (the single `SATISFIED` write) via `certify_refresh` |
| Block rc0 ≠ truth | `script_runner.py` invariant (only host check flips state) |
| Agent has proposal power, not write/truth/done power | `v3_build_agent.propose` → `patch_gate` (never SATISFIED) → host certify |
| Four gates between "LLM said so" and "true" | parse (`patch.py`) → PatchGate (`patch_gate.py`) → render+execute (`block_emit`/`script_runner`) → certify (`certify.py`) |
| Bounded repair (termination proven) | `repair_loop.py` (budget + known_invalid + reject-once + convergence guard) |
| Anti-weakening (can't relax a check to pass) | `patch_gate.py` guard + `depgraph/check_quality.py` |
| collect-only is a *probe*, not a gate | `gates.py` rejects it; `done_gate.py` `_shows_execution` requires "N passed" |
| Installability gate = fresh-replay rc0 | `v3_loop.py` `reset_to_base()` → `run_install_script()` → `certify_reciped_only` (Stage 2) |
| Testability gate = real pytest (live: full pass) | `gates.evaluate_testability_gate` ← `done_gate._verified_test_run_passed` |
| Gates outside the graph (no NodeType.GATE) | `gates.py` returns `GateResult`, never mutates graph |
| Discover→resolve coupling (the honest gap) | `apt_resolve.py` + Stage 2.5 — **see §5** |

---

## 4. Deliberately excluded (and why it's safe)

| Excluded | Why the v3 path never reaches it |
|---|---|
| `agent.py` `DockerAgent` (whole) | driver descends from l2_smoke; DockerAgent only needed for v1/v2 arms |
| `src/envstate/planner.py`, `src/planner.py` | LLM strategic planner — constructed-but-never-called on v3 (`next_decision` replaces it) |
| `src/envstate/maintainer.py` `Maintainer` class | v3 implies `DeterministicMaintainer`; LLM maintainer never constructed |
| `src/envstate/contracts/*` except `graph.py` | only `Maintainer`/`Planner` use apply/extract/patch/validation/render/projection |
| `src/synthesizer.py` (minus `test_oracle` slice) | 4084-line v1 synthesis; only test-output parsing is needed |
| `src/recipe_repair.py`, `observation_compressor.py`, `memory_manager.py`, `image_selector.py` | arm-0 DockerAgent features |
| `python_deps/{models,graph,z3_adapter,resolver,constraints,report,pypi_metadata,external_graph}` | v1-era Z3 solver path; `build_dep_graph` uses uv.lock instead |
| `EnvStateOrchestrator` class | legacy v0/A/B/C supervisor |

---

## 5. The honest delta: the outer gate-loop is not yet closed

What runs e2e **today** (proven by l2_smoke): the **inner loop** (graph → render → execute →
certify → bounded typed repair) **plus** the binding-install fresh-replay gate (`enable_binding_install`).

What is **not** yet wired: the **outer gate-ladder loop** — "gate failure → inject new graph
obligations → re-satisfy frontier → re-gate." Today `gates.py` is **observability only**
(`enable_gate_observability`, derives `GateResult`s on exit without writing back), and binding-install
is a separate certify path. This matches the project's own roadmap (Stage 2.5 = discover→resolve
coupling for the libGL-style inert-`#@need` bug; Stage 3 = done-gate enforcement, deferred).

**Implication for "contains all the story":** the branch carries every *component* of the story, and
the inner loop + installability gate run e2e. To make the **outer loop** binding (the full ladder),
the branch needs a small piece of NEW glue in `v3_loop.py` — feed gate failures back as obligations —
which is exactly Stage 2.5/3 work. Two honest options:

- **(A) Scope the branch to the working e2e** (inner loop + binding-install gate + gates-as-observability),
  and mark outer-loop closure as the branch's first build item. Lowest risk; e2e green from day one.
- **(B) Close the outer loop as part of standing up the branch** (do Stage 2.5/3 here). Bigger, but the
  branch then demonstrates the complete ladder.

Also note for the paper: the **live** testability gate is *stricter* than the reported `pass_rate ≥ 0.8`
metric — the live done-gate requires a **full** pass; `0.8` is the offline honest-scorer bar
(`synthesizer.MIN_PASS_RATIO` → `verification_bundle.py`). Keep these distinct.

---

## 6. Risk + recommended sequence

1. **Spike the `test_oracle` extraction** (§2.1) — measure the real synthesizer surface. *Go/no-go on a clean branch.*
2. Create the worktree/branch; copy the AS-IS manifest (belief plane + sandbox + the AS-IS envstate set).
3. Land the 4 extractions behind unit tests (each has an existing test file to port:
   `test_repair_loop.py`, `test_block_emit.py`, `test_script_runner.py`, `test_patch_gate*.py`,
   `test_gates_*.py`, `test_graph_scheduler_*.py`, `test_v3_*`).
4. Write `run_v3_e2e.py`; run it against a known repo (the libGL/opencv case) to reproduce the l2_smoke PASS.
5. Decide §5 (A) vs (B) for the outer loop.

**Carry-forward warnings (from prior incidents):** do this in a git **worktree**; legacy is **read-only**;
**no broad `git add`/`git rm`/`reset`** (subagent git ops have discarded WIP here before); commit-by-commit.

---

## 7. Build outcome (branch `v3-core`, 2026-06-30)

Built as **scope A** in a sibling worktree (`/Users/john/john-planner-v3-core`, branched from
`10efb9e`; the `john-planner-v3` branch + WIP untouched). Validated with system `python3` —
the full suite runs without Docker; the Docker+LLM e2e is a separate run.

| Commit | What landed | Validation |
|---|---|---|
| `c1731c9` | `v3_build_agent.py` — `V3BuildAgent.propose` lifted from `build_agent.py` (1088 LoC), zero legacy drag | parity vs original, 8 tests |
| `36c54ca` | `scripts/run_v3_e2e.py` — the legible entrypoint (classifier → `run_v3` w/ materialization+binding-install+gate-observability → `setup.sh`) | argparse + all imports resolve |
| `6da6e34` | `run_oracle.py` (716 LoC) + `done_gate.py` (238 LoC) lifted from `synthesizer.py`/`maintainer.py`; consumers rewired | parity tests, 98-test v3 subset |
| `b91a359` | **Prune**: deleted `agent.py`, both planners, `maintainer.py`, `build_agent.py`, arm-0 modules, `verification_bundle`/`artifact_verify`/`workplace_replay`, the planner-facing `contracts/*`, the z3-era `python_deps/*`, `EnvStateOrchestrator`, + 97 legacy/parity tests; restored `models.py` to the verbatim 6-class subset | **1276 passed, 32 skipped** (2 pre-existing PDF-dataset failures) |

**Deviations from the plan (residual coupling found during the build — honest record):**

1. **`synthesizer.py` is NOT deleted.** `src/sandbox.py` (kept) uses `Synthesizer` as its
   preflight command-classifier. Removing it requires extracting/slimming the Sandbox classifier
   dependency — a follow-up. The done-gate's need was already severed (→ `run_oracle.py`).
2. **`run_v1` is NOT deleted** from `orchestrator.py`. Two keep-anchor tests
   (`test_graph_scheduler_wiring.py`, `test_v3_task_branch.py`'s B3 path) reference it. Removing it
   means rewriting those tests — a follow-up. `EnvStateOrchestrator` *was* removed.
3. **More of `contracts/` is kept than predicted** (`apply/extract/ids/nodes/patch/validation/schema`
   beyond `graph.py`) — `deterministic_maintainer.py` (kept) imports them.
4. **`models.py`**: the z3 classes were dropped, but the 6 classes the depgraph stack needs
   (`PythonRequirement`, `PythonVersionRequirement`, `ImportFinding`, `ImportPackageMapping`,
   `DependencyFailure`, `PythonDependencyEvidence`) are kept **verbatim** (incl. `to_dict` /
   `is_dependency_shaped`) — an initial lossy stub was caught and replaced.

**Still open (not in scope A):**
- The **outer gate-ladder loop** (§5) — gates remain observability-only; closing it is Stage 2.5/3.
- The real **Docker+LLM e2e run** of `run_v3_e2e.py` (needs an API key + target repo; Docker is available).
- Removing residuals 1–2 above for a strictly v3-only tree.
