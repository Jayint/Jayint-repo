# DESIGN: Merging the python_deps dependency graph into the contract graph

**Status:** Proposal / discussion draft (for review in a fresh session)
**Date:** 2026-06-22
**Branch:** `john-planner-v1`
**Scope:** Wire the existing `src/python_deps/` resolver/graph into the `src/envstate/contracts/` contract graph as a proactive **RESOLVE** front-end whose proposals are **CERTIFIED** by the contract graph's host probes.
**Explicitly OUT of scope for this doc:** cross-repo memory (GRM) — deferred. See `docs/DESIGN-grounded-repair-memory.md` for that separate layer.

---

## 0. TL;DR

- `src/python_deps/` is a complete-but-**unwired** homegrown "EnvGraph": static evidence (manifest parse + AST import scan) + PyPI metadata → a **Z3 SMT closure solver** that proposes a pinned `pip` install plan, plus a budget-capped LLM-facing graph projection. It has **no tests** and **zero imports outside itself**.
- The `src/envstate/contracts/` **contract graph** is the live agent's fault/repair overlay: typed `Contract`/`Blocker`/`Attempt` nodes, host-`host_satisfied`-certified status, a goal `depends_on` backbone. It is wired into the agent loop (`run_v1`).
- **The merge:** `python_deps` becomes the **proactive deterministic seeder + pip-layer solver**; the contract graph **certifies** its proposals at runtime and owns the system/build/runtime/config/tests layers. They are **not two glued graphs** — `python_deps` projects onto the *same node-id grammar* the contract graph already uses (`ids.contract_id(kind, subject)`), so it is a proactive pre-fill of one graph.
- Net new trust tier: **SOLVED** (Z3-consistent, not yet executed), sitting between `speculative` (LLM guess) and `grounded` (host-certified).

---

## 1. Context — the three artifacts a cold reader needs

### 1.1 `src/python_deps/` (the "pydeps graph") — homegrown EnvGraph + solver
Self-contained, 18 files (~4,600 lines), all staged but unwired. Its own spec: `docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md`.

Pipeline:
1. **Static evidence** — `evidence.collect_python_dependency_evidence(repo)` parses `requirements*.txt`/`constraints*.txt`/`pyproject.toml`/`setup.cfg`/`setup.py`; `import_graph.scan_imports(repo)` AST-walks `*.py` (≤1000 files; regex fallback on `SyntaxError`) and classifies each top-level import as `project_local` / `stdlib` / `external`. Produces `PythonDependencyEvidence` (`models.py:202`).
2. **Failure classification** — `failure_classifier.classify_dependency_failure(command, observation)` → `DependencyFailure` (`models.py:51`) with `failure_type ∈ {module_not_found, import_name_error, no_matching_distribution, dependency_conflict, native_library_missing, syntax_requires_newer_python, not_dependency_related}` (regex-based).
3. **Constraint solve** — `resolver.solve_dependency_problem(diagnostics, latest_report, enable_z3=True)` builds a `ConstraintGraph` (`models.py:138`) from declared deps + PyPI metadata (`pypi_metadata.py`, cached) and runs a **real Z3 SMT solver** (`z3_adapter.py`). Output `SolverResult` (`models.py:167`): `status ∈ {sat, sat_soft_relaxed, unsat, solver_unavailable, solver_error}`, `selected_python`, `selected_packages: {name→version}`, `install_commands`, `verification_commands`, `unsat_core`.
4. **LLM projection** — `external_graph.builder.build_external_dependency_graph_slice(...)` → `ExternalDependencyGraphSlice` (budget-capped 16 KB; node kinds `PythonPackage`/`PythonImport`/`File`/`Manifest`/`PythonRuntime`/`PackageVersion`/`Requirement`/`ResolverFailure`/`VerifyTarget`; `PythonPackage` carries `used_in_code` + `declared`). Pure projection, no network.

`import_mapping.CURATED_IMPORT_TO_PACKAGE` is a 6-entry import→pip table (`cv2→opencv-python`, `sklearn→scikit-learn`, …).

**Capability vs the EnvGraph paper:** the paper (arXiv id unverified — reported as 2604.03622; confirm before citing) localizes failures (external-dep / internal-ref / logic) and lets an LLM edit the repo; it has **no solver**. `python_deps` adds the Z3 closure solver the paper lacks, and is already on the *environment* axis (emits install commands, not repo edits).

### 1.2 `src/envstate/contracts/` (the contract graph) — the live certifier
Concise fault/repair overlay inside `WorldModelMap.contract_graph`. (Full design: `docs/DESIGN-concise-contract-graph.md`.)

- **Nodes** (`nodes.py`, `schema.py`): `Contract` / `Blocker` / `Attempt`. `Contract.data{level(goal|atomic), kind, subject, layer, required, check, evidence_refs, ...}`; `Blocker.data{signature, kind(BlockerKind), layer, active, ...}`; `Attempt.data{commands, outcome(pending|ok|failed|ok_but_still_blocked), ...}`.
- **Edges**: `violates`(Blocker→Contract), `addresses`(Attempt→Contract), `depends_on`(Contract→Contract).
- **Layers** (`schema.py`): `deps / system / runtime / build / tests / config`.
- **BlockerKind**: `module_not_found, missing_binary, missing_system_library, version_conflict, build_failure, service_unreachable, env_var_missing, test_collection_failure, unknown`.
- **Status is PROJECTED, never stored** (`graph.py:89` `project_status`): `satisfied` iff in `host_satisfied`; else `violated` iff an active Blocker `violates` it; else `unknown`.
- **Cold-start** `goals.seed_backbone()`: 7 goal Contracts (`repo_tests_pass`[required], `repo_tests_collect`, `repo_imports_work`, `repo_deps_installed`, `repo_build_ready`, `repo_services_ready`, `repo_config_ready`) + foundational + `depends_on` backbone.
- **Reactive atomic promotion** `extract.promote_atomic_contracts(graph, signatures)` (`extract.py`): deterministic regex over rc≠0 stdout mints `contract:python_import:<mod>` / `contract:binary:<x>` / `contract:system_library:<lib>` via `ids.contract_id(kind, subject)`.
- **Host certification** `projection.refresh_host_graph()`: re-seed → promote atomics → `_auto_resolve_blockers` (retire when subject now present) → build `host_satisfied` from real import sweep / package probe / collect-only rc=0 / verified pytest.
- **Ownership** (`schema.py`): `HOST_CREATABLE={Contract, Attempt}`; `MAINTAINER_CREATABLE={Contract, Blocker}`; `MAINTAINER_FORBIDDEN_FIELDS={status, outcome, active}`. The Maintainer LLM is the only source of *speculative* Blockers.
- **Loop** (`orchestrator.py` `run_v1`): `planner.decide` → commit Attempts → `build_agent.run_recipe` → `apply_deterministic` probe → `refresh_host_graph` → `derive_attempt_outcome` → `maintainer.update`. Gated by `--enable-contract-graph` / `--arm v1g` / `DOCKERAGENT_ENABLE_CONTRACT_GRAPH`.

### 1.3 Why merge rather than choose
There are currently **two homegrown dependency representations**: `extract.py` (reactive, execution-certified, wired) and `python_deps` (proactive, solver-backed, unwired). They are the two halves of one design built separately. The merge wires `python_deps` in as the proactive front-end and lets the contract graph supply the runtime certification `python_deps` lacks.

---

## 2. Core design principle: one graph, two writers, shared id grammar

`extract.py` already mints `contract:python_import:<name>` / `contract:system_library:<lib>` via `ids.contract_id(kind, subject)`. **`python_deps` projects onto the exact same ids.** Therefore:

- `python_deps` is the **proactive** seeder (cold start, before any failure).
- `extract.py` is the **reactive** safety-net (fills gaps `python_deps` missed, e.g. dynamic imports). Same id → `apply_patch` merges; if the node exists it is a no-op.
- The contract graph stays the single source of truth and **status projection** — `python_deps` never sets status.

This is a *pre-fill of one graph*, not a second graph stitched on. It preserves the contract graph's 3-node/3-edge concision: `python_deps`' richer detail (versions, candidates) lands in `Contract.data`, not new node types.

---

## 3. Architecture: RESOLVE → CERTIFY (two layers)

```
┌─ RESOLVE ─ python_deps ─────────────────────────────┐
│ static evidence (manifest + AST imports)            │
│ + PyPI metadata → ConstraintGraph → Z3 solve        │
│ + curated import→pkg and pkg→system-lib tables      │
│ OUT: DepResolution{ graph_patch (host-scope),       │
│      seed_recipe (install_commands), resolver_status│
│      pinned_closure, unsat_core }                   │
└───────────────┬─────────────────────────────────────┘
                │ project onto ids.contract_id grammar (host patch)
                ▼
┌─ CERTIFY + REPAIR ─ contract graph (owns truth) ────┐
│ run seed_recipe → host_satisfied probes             │
│ promote SOLVED → GROUNDED, or open Blocker          │
│ OWNS system/build/runtime/config/tests + backbone   │
│ two repair loops route by python_deps classifier:   │
│   • pip conflict  → blocked_assignment → RE-SOLVE   │
│   • system/build  → system_library Contract → apt   │
└─────────────────────────────────────────────────────┘
```

GRM (cross-repo memory) would sit below CERTIFY, harvesting certified closures + system fixes — **deferred**, not in this doc.

---

## 4. Trust ladder (three tiers)

Per node, **projected each cycle, never stored**:

| Tier | Source | Meaning |
|---|---|---|
| **speculative** | LLM Maintainer | unverified guess (lowest) |
| **solved** | `python_deps` Z3 `sat` | closure consistent vs PyPI metadata (constraint-proof, not execution-proof) |
| **grounded** | this-run `host_satisfied` | executed + certified (highest) |

Only your stack spans constraint-solving **and** execution grounding; this is the headline contribution.

---

## 5. Projection map (`python_deps` → contract graph)

| `python_deps` object | → contract element | id / data written |
|---|---|---|
| external `ImportFinding('cv2')` + `cv2→opencv-python` | **atomic Contract** | `contract:python_import:cv2`, data`{kind:python_import, subject:cv2, layer:deps, resolved_package:opencv-python, declared:bool, used_in_code:true}` |
| `SolverResult.selected_packages{opencv-python:4.9}` | enrich that Contract.data | `resolved_version:4.9`, `resolver_status:sat` |
| curated syslib hint `opencv-python→libgl1` | **atomic Contract** | `contract:system_library:libGL.so.1`, data`{kind:system_library, layer:system, hint_source:curated}` |
| `SolverResult.install_commands` | **seed_recipe** (RecipeSteps) | host commits Attempts that `addresses` the deps Contracts |
| `python_requires '>=3.9'` | enrich foundational Contract | `python_version_compatible.data{specifier}` |
| `SolverResult.status=unsat` + `unsat_core` | **Blocker** | kind=`version_conflict`, `violates` → `repo_deps_installed`, signature = unsat-core text |
| declared deps | `depends_on` edges | deps Contracts → `repo_deps_installed` (backbone exists) |

The bundle `python_deps` hands the orchestrator:

```
DepResolution {
  graph_patch:    GraphPatch     # atomic deps + syslib Contracts (+ optional version_conflict Blocker), host scope
  seed_recipe:    list[RecipeStep]  # SolverResult.install_commands
  resolver_status: str           # sat | sat_soft_relaxed | unsat | solver_unavailable
  pinned_closure: dict[str,str]  # selected_packages
  unsat_core:     tuple[str,...]
}
```

Produced by a thin new adapter **`src/envstate/contracts/deps_bridge.py`** (keeps `python_deps` free of contract-graph imports).

---

## 6. Status projection extension (one new branch)

Extend `graph.project_status` (or a parallel `project_grounding`) — still projected, never stored:

```
in host_satisfied              → GROUNDED   (unchanged)
else data.resolver_status==sat → SOLVED     (NEW)
else active Blocker violates   → VIOLATED   (unchanged)
else                           → UNKNOWN    (unchanged)
```

Planner view then renders `contract:python_import:cv2 [SOLVED → opencv-python==4.9]` before the install, `[GROUNDED]` after the probe confirms it.

---

## 7. The loop + the two repair loops

```
COLD START
  collect_evidence → solve_dependency_problem
  → deps_bridge → DepResolution
  → apply graph_patch (host scope); seed_recipe is the first install recipe
RUN seed_recipe        → Attempts committed, addressing deps Contracts
PROBE                  → apply_deterministic + refresh_host_graph
  → SOLVED contracts whose import resolves → GROUNDED
  → derive_attempt_outcome
ROUTE failures via python_deps.failure_classifier:
  • pip-layer (no_matching_distribution / dependency_conflict)
      → record version_conflict Blocker
      → append blocked_assignment → RE-SOLVE → new DepResolution    ← python_deps loop
  • system/build (native_library_missing / build failure / missing binary)
      → system_library / binary Contract → planner/maintainer → apt ← contract-graph loop
  • module_not_found python_deps missed (dynamic import)
      → extract.py reactively promotes Contract → re-solve to include
LOOP until host_satisfied covers the repo_tests_pass closure
```

**The split is the heart of the interaction:** the classifier sends *pip-version* faults back into Z3 (deterministic re-solve) and *system/build* faults to the contract graph (apt). Z3 is PyPI-only and cannot solve system libs — that layer is contract-graph-owned.

---

## 8. Ownership & precedence (three writers, host owns truth)

1. **`python_deps` (cold-start, deterministic):** host-scope patch; may add Contracts + write `resolver_status`/`resolved_*` data; **never** `status`/`outcome`/`active`.
2. **`extract.py` (reactive safety-net):** same id grammar → no-op if node exists, adds if new. Runs *after* `python_deps` so `python_deps` wins on shared nodes.
3. **Maintainer LLM:** unchanged — only source of *speculative* Blockers, now visibly below `solved`.
4. **Host (`refresh_host_graph` / `host_satisfied`) always overrides:** a `SOLVED` node that fails to import stays un-grounded and a Blocker opens. No proposer can fake `grounded`.

---

## 9. Layer split (who owns what)

| Layer | Owner | Mechanism |
|---|---|---|
| `deps` (pip closure, versions, conflicts) | **python_deps** | Z3 solve + re-solve loop |
| `system` (apt, `.so`, headers) | **contract graph** | curated hint seed + reactive `system_library` Contract + apt |
| `build` | contract graph | build_failure Blocker → system deps / build tools |
| `runtime` / `config` / `tests` | contract graph | service/env probes, done-gate |

`python_deps` proposes system-lib **hints** (curated table) but does **not** solve them; the contract graph certifies and repairs them.

---

## 10. Worked trace (psycopg2)

Cold start: `python_deps` solves → seeds `contract:python_import:psycopg2 [SOLVED → psycopg2==2.9]` + curated `contract:system_library:libpq-dev`; `seed_recipe = pip install psycopg2==2.9`. Run → build fails (`fatal error: libpq-fe.h`). Classifier → `native_library_missing` → **system loop** (not Z3): the already-seeded `libpq-dev` Contract gets an apt Attempt; `psycopg2` stays `SOLVED`-not-grounded with a `violates` Blocker. apt libpq-dev → reinstall → probe → `psycopg2` flips `GROUNDED`. No re-solve, because it was never a *version* problem — the classifier routed it to the system layer.

---

## 11. Open decisions (the forks to settle in review)

1. **Granularity** — deps Contracts at *import* granularity (`python_import:cv2`, package as `data`) vs a new *package* node kind (`python_package:opencv-python`). **Lean: import granularity** — preserves concision, reuses `extract.py` grammar, merges cleanly.
2. **Seed-recipe authority** — solver `install_commands` run *directly* as the cold-start recipe (fewer cycles) vs handed to the planner as an editable *proposal* (planner stays in control). **Lean: direct for the first install**, planner owns subsequent cycles.
3. **`SOLVED` as a first-class projected status** vs folding into `unknown` with a data flag. **Lean: first-class** — it's the contribution and it tells the planner "trust this more than a guess, still verify."
4. **One classifier** — adopt `python_deps.failure_classifier` as the single brain; `extract.py` becomes a thin adapter. (7 categories ⊃ `extract.py`'s set.)
5. **Solver cadence** — cold-start + on-pip-conflict only (bounded), never every cycle.
6. **System-lib priors without GRM** — ship a curated `pkg→apt` table (analogous to `CURATED_IMPORT_TO_PACKAGE`); reactive discovery for the long tail. (Memory/learning is GRM, deferred.)

---

## 12. How this compares to the "original" graph (for framing)

A three-rung ladder:
1. **Localize** — EnvGraph paper: "the problem is *external* vs internal vs logic." Tells the LLM *where*; LLM still fixes; nothing verifies; stateless (rebuilt each iteration).
2. **Localize + Solve** — `python_deps` today: "...external, *and here is a consistent pinned plan*."
3. **Localize + Solve + Certify + Track** — this merge: a **persistent, execution-certified state model** of every obligation, localized on the *environment* layer axis (deps/system/build/runtime/config/tests, vs EnvGraph's 3 code categories), ordered by a `depends_on` causal backbone, with `host_satisfied` telling the LLM what is **already proven and must not be re-touched**.

---

## 13. Static pre-fill note (parser)

`python_deps` already does the EnvGraph-style static pre-fill, using Python `ast` (`import_graph._imports_from_ast`) with a line-regex fallback on `SyntaxError`. EnvGraph also uses `ast`, **not tree-sitter**. tree-sitter would only add value for the *syntax-error / target-Python-version-mismatch tail* (error-tolerant, version-agnostic parsing) and is an optional drop-in upgrade behind the same function boundary — **measure the existing `"used regex import fallback"` error-log rate over the dataset before adopting it.** Not on the integration critical path.

Static pre-fill front-loads the *statically visible* pip/import closure (cutting `ModuleNotFoundError` cycles) but **cannot** see system libs, build deps, version conflicts, or dynamic imports — those require the runtime certify loop. Pre-fill *and* runtime, not pre-fill *instead of*.

---

## 14. Out of scope (explicit)

- **GRM cross-repo memory / telemetry** — separate layer, deferred (`docs/DESIGN-grounded-repair-memory.md`).
- **Editing the repository to fix deps** (EnvGraph's repair mode) — conflicts with fidelity; the environment is the only free variable. A wrong/unsatisfiable pin is *surfaced*, not silently rewritten.
- **Multi-language** — Python only.

---

## 15. Integration steps (suggested order)

1. `src/envstate/contracts/deps_bridge.py` — adapter: `DepResolution = resolve(repo, diagnostics)`; project `SolverResult` + evidence → `GraphPatch` on `ids.contract_id` grammar; build `seed_recipe`.
2. Extend `graph.project_status` (or add `project_grounding`) with the `SOLVED` branch (§6).
3. `orchestrator.run_v1`: cold-start call to `deps_bridge` → apply host patch + run `seed_recipe`; post-probe routing of pip-conflict Blockers back to `solve_dependency_problem` with accumulated `blocked_assignments`.
4. Curated `pkg→apt` system-dep table (in `python_deps`, emitted as syslib-hint Contracts).
5. `render.render_graph_for_planner`: show the trust tier + `resolved_package==version`.
6. Reduce `extract.py` to the reactive safety-net; route classification through `python_deps.failure_classifier`.
7. Tests — `python_deps` currently has **none**; add unit tests for `deps_bridge` projection, the `SOLVED` projection, and the two-loop routing. Gate behind a flag (e.g. `DOCKERAGENT_ENABLE_DEPS_RESOLVE`) under `enable_contract_graph`, A/B vs the v1g baseline (score with `compute_essr.score_agent`, never `rat_results.json`).

---

## 16. Risks

- **Z3 SAT is a hypothesis, not truth** — a `solved` pin can be wrong on the platform Z3 didn't model (yanked wheel, ABI). Mitigation: `SOLVED` never implies `grounded`; host probe must confirm; pip failure → `blocked_assignment` → re-solve.
- **Cold-start cost** — Z3 + PyPI fetch per repo, no memory to amortize (GRM deferred). Mitigation: `pypi_metadata` local cache; Z3 is fast for small closures; bounded re-solves.
- **Two-writer drift** — keep projection strictly one-way (`python_deps` → Contracts); contract graph owns truth.
- **`python_deps` is untested** — integration must add coverage; do not wire it into the default arm until tested.
- **Granularity mismatch** — if fork #1 chooses a `python_package` node kind, it breaks the shared-id-grammar merge; the import-granularity choice is what makes the merge clean.

---

## 17. References

- `src/python_deps/` — `models.py`, `import_graph.py`, `evidence.py`, `failure_classifier.py`, `constraints.py`, `z3_adapter.py`, `resolver.py`, `pypi_metadata.py`, `import_mapping.py`, `external_graph/{builder,dto,projectors,rendering,budget}.py`
- `src/python_deps` spec: `docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md`
- Contract graph: `src/envstate/contracts/{schema,nodes,ids,goals,extract,attempts,graph,projection,validators,validation,render,apply,patch}.py`; `src/envstate/{orchestrator,planner,maintainer,world_model}.py`
- Contract graph design: `docs/DESIGN-concise-contract-graph.md`
- Deferred memory layer: `docs/DESIGN-grounded-repair-memory.md`
- EnvGraph paper (localize-then-repository-revise; no solver): local PDF `/Users/john/Downloads/EnvGraph__Environment_Alignment_for_Repository_Level_Code_Generation.pdf`; anonymized repo https://anonymous.4open.science/r/EnvGraph; arXiv id **unverified** (reported 2604.03622 — confirm)
- SetupX (XPU cross-repo memory; LLM-audited telemetry): arXiv **2605.26186** — relevant to the deferred GRM layer, not this doc
- Visual: `docs/envgraph_port_initial.graphml`, `docs/envgraph_port_runtime.graphml`, `docs/envgraph_port_visualization.html`
