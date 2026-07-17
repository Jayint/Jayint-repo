# SPEC: Dependency Diagnosis Graph (failure-scoped, cross-layer)

**Status:** Implementation spec — *draft for review*. Two subsystems are intentionally left OPEN (§10 LLM error parsing, §11 graph maintenance) for further discussion before locking.
**Date:** 2026-06-22 · **Branch:** `john-planner-v1`
**Companion docs:** `docs/DESIGN-deps-diagnosis-graph.md` (paper-grade rationale + worked examples), `docs/DESIGN-deps-diagnosis-graph-debate.md` (the 3 rival schemas + critiques). This SPEC is the buildable distillation; read the DESIGN for the *why*.

---

## 0. Scope

A **standalone, within-run** diagnosis overlay over `src/python_deps/`, with **Z3 strictly behind it**. It answers, for one Docker-build env-setup run: *what failed → what env need does it imply → what lower need is the root cause → what provider/action resolves it, and was it actually proven?*

**Out of scope (do not build here):** the contract graph (`src/envstate/contracts/`), cross-repo memory / telemetry (GRM), editing the repository. The only free variable is the **environment**.

**Core invariants (non-negotiable):**
- **The module is pure** — it never executes commands. The agent/harness runs commands and probes and feeds results in (as the existing `diagnostics` dict already does).
- **The host certifies truth, never the LLM** — a `Need` becomes `proven` only when *its own* `check_command` returns rc 0. Install ≠ proven. `pytest --collect-only` ≠ proven.
- **Minting is failure-scoped; certification is observation-scoped.** Only *node creation* (`Failure`/`Need`) is gated on a failure. **State** is updated on **every** observation — success commands flip Needs to `proven`; package-mutating commands invalidate downstream `proven` Needs (§6, §11). The graph is never updated on failures *only*, or it goes stale by construction.
- **`proven` is revocable.** A later command can break an earlier-proven Need (e.g. a second `pip install` downgrades a shared dep). `depends_on` doubles as the invalidation index; `is_ready()` re-certifies touched Needs before finalize (§11).
- **Every state is a deterministic function of the observation stream `(static evidence + ordered diagnostics)`** — reproducible, unit-testable on captured-log fixtures.

### Role mapping (Planner → BuildAgent → Maintainer)

The graph's three-actor split maps ~1:1 onto the v1 three-role architecture; this is *why* it fits without a new trust model:

| Graph operation | Owner role | Rationale |
|---|---|---|
| Static seed (`diagnose`, §5) | **Planner** (cold start) | proactive front-end: enumerate the closure + system-lib priors before the first command |
| `ingest` classify / link / certify / Z3 / Attempt (§6) | **Maintainer** | deterministic + host-certified = the truth-side role; **no LLM here** (see below) |
| `next_action` / `render_for_planner` | **Maintainer** writes → **Planner** reads | Maintainer renders diagnosis into the WorldModelMap; Planner consumes a clean view, not raw stderr |
| LLM oracle `propose_*` (§9) | **Planner** | proposals = strategy = the LLM-in-the-loop role |
| Execute recipe; emit observation | **BuildAgent** | evidence source; never touches the graph |

**Payoff that justifies the wiring:** `is_ready()` (every Need `proven` by its own `check_command`) replaces the lenient `pytest --collect-only` done-gate (`maintainer.py:286`) — a strictly stronger, honest finalize condition (collect-only can never be a valid `check_command`).

**Reactive ingest stays LLM-free, by design.** A single failure reveals one layer at a time (`No module named cv2` → naming Need; after install, `libGL.so.1` → system Need); the cross-layer chain emerges incrementally and deterministically. The LLM's leverage is the *opposite* of reactive ingest — **proactive anticipation** in the Planner (`propose_chain` at cold start: "installing opencv → pre-stage libgl1", collapsing failure cycles) plus the **novel-error fallback** (§10). When deterministic ingest can't link a Need to any Provider, it leaves an *unresolved Need*; that gap is the signal that triggers the Planner's oracle next cycle — keeping each role pure at the cost of one extra cycle.

---

## 1. Overview

```
diagnose(static_evidence)  ── cold start ──▶  graph (Needs=unknown, Providers=candidate)
ingest(graph, cmd, output) ── per tick ───▶  append Failure/Need/Attempt, host flips state
next_action(graph)         ── selector ──▶   deepest unproven Need → best provider | relax pin
is_ready(graph)            ── done gate ─▶   root Need proven by REAL test + all chain Needs proven
```

Two engines sit *behind* the graph; the graph trusts neither without a check:
- **Z3** — exact engine for pip-layer version arithmetic (§8).
- **LLM** — fuzzy engine for ecosystem semantics, called only at gap-points; outputs are low-trust (§9). *(Error-parsing role is OPEN — §10.)*

---

## 2. Trust model

Three projected tiers (never stored; recomputed each cycle):

| Tier | Meaning | Set by |
|---|---|---|
| `speculative` | LLM/heuristic guess, no certification | LLM oracle, untrusted classification |
| `solved` | Z3 says the closure is consistent vs PyPI metadata (constraint-proof) | Z3 `sat` |
| `grounded` | executed + host-certified this run | host running `check_command` |

`Need.state ∈ {unknown | proven | refuted}` is the host-certified satisfaction flag — orthogonal to the tier and the **load-bearing honesty mechanism**.

---

## 3. Data model

4 node types, 8 edge types. Additions land in node `data`; do not add node types (preserves concision). Node ids are stable content-hashes (dedup keys).

### Nodes

**`Failure`** — the only root node; 1:1 with `DependencyFailure` (`models.py:51`).
```
id            sha1(failure_type + import_name + package_name + library)
failure_type  one of the classifier types (§6 / §10)
command       command that produced it
symptom_text  DependencyFailure.message (~500-char excerpt)
details       DependencyFailure.details verbatim
state         active | resolved | stale
first_seen / last_seen   cycle ints
```

**`Need`** — the cross-layer, host-certifiable obligation.
```
id             kind:target              # python_import:cv2 | system_library:libGL.so.1 | version_range:numpy
kind           python_import | pip_dist | version_range | system_library | toolchain
               | python_runtime | pip_closure_consistent | (env_var | service = annotation-only)
layer          naming | pip | system | interpreter | solver_conflict | runtime_env
target         cv2 | opencv-python | libGL.so.1 | numpy | gcc | >=3.10
specifier      version/range when applicable
pin_direction  none | floor | ceiling   # ceiling = "must pin BELOW" (import_name_error case)
check_command  THE proof obligation (§7); rc==0 ⇒ proven. NOT --collect-only.
state          unknown | proven | refuted   # host flips; never the LLM
trust          high | medium | low
origin         failure_indicated | depends_on_derived | static_cold_start
source         which Failure/Need/table/oracle produced it
```

**`Provider`** — a candidate action, tagged by manager.
```
id                manager:spec            # pip:opencv-python | apt:libgl1 | runtime:python3.11
manager           pip | apt | conda | runtime | env_directive
spec              install/select string
version           Z3 (name,version) identity when from a PackageCandidate
provides_targets  Need ids it may satisfy (one provider → many needs)
retracts_targets  Need ids it makes UNNEEDED (opencv-python-headless retracts libGL)
candidate_meta    has_wheel, yanked, requires_python, rank (from PackageCandidate)
trust             high | medium | low
status            candidate | selected | exhausted | ineffective
source            pypi | curated_import_map | curated_swap_table | unsat_core | llm | agent_observed
```

**`Attempt`** — per-run diagnostic memory (not a log).
```
id                  sha1(command + cycle)
command             executed transaction (pip line | apt | env bake | 'z3_solve')
used_provider_ids   providers it instantiated
addresses_need_ids  needs it targeted
outcome             succeeded | failed | unknown
outcome_evidence    the check_command output the host used to decide
solver_status       sat | sat_soft_relaxed | unsat | solver_unavailable | solver_error
blocks              for failed attempts: the (pkg,version) no-good set → Not(And(vars))
cycle               int
```

### Edges

| Edge | From → To | Meaning |
|---|---|---|
| `indicates` | Failure → Need | symptom implies this obligation (entry edge) |
| `depends_on` | Need → Need | source not provable until target (lower layer) proven — **root-cause centerpiece** |
| `provided_by` | Need → Provider | provider may satisfy need (many-to-many) |
| `retracts` | Provider → Need | choosing provider makes need UNNEEDED (deletes a depends_on subtree) |
| `conflicts_with` | Need ↔ Need | mutually exclusive; **Z3 unsat-core-sourced only**; ≥3-way ⇒ `ConflictGroup` |
| `addresses` | Attempt → Need | attempt targeted this need |
| `used` | Attempt → Provider | attempt instantiated this provider (anti-loop key) |
| `outcome` | Attempt → Failure | `{resolved\|failed}` (attribute-distinguished) |

`ConflictGroup{id, member_need_ids[≥2], core_reasons[], irreducible, minimization_timed_out}` — the ≥3-way hyperedge surrogate.

---

## 4. Interfaces (pure, deterministic)

```python
graph = diagnose(static_evidence: PythonDependencyEvidence) -> Graph        # §5
graph = ingest(graph, command: str, output: str, diagnostics: dict) -> Graph # §6
action = next_action(graph) -> RepairAction | None                          # selector
ready  = is_ready(graph) -> bool                                            # done gate
```

`next_action`: act on the **deepest unproven Need** reachable via `depends_on`; pick its highest-`trust`, non-`exhausted`/non-`ineffective` Provider; prefer a `retracts` provider when it voids a deeper subtree; if the frontier is a `conflicts_with`/`ConflictGroup`, the action is **relax a pin**, not install.

`is_ready`: **re-certifies before answering** — re-runs the `check_command` of any Need touched since it was last certified (§6.7b / §11.2), so stale `proven` flags can't pass the gate. Then true iff the root real-test Need is `proven` (its check is the real `pytest -q`, never collect-only) **and** every Need on every live chain is `proven` by its own check.

---

## 5. Initialization (cold start) — `diagnose()`

Pure projection from `evidence.collect_python_dependency_evidence(repo)` + curated tables + **one** speculative Z3 solve. **No `Failure` nodes yet; every Need starts `unknown`.**

1. **Imports → naming Needs.** Each `external` `ImportFinding` (`import_graph.scan_imports`) → `Need(python_import:<name>, layer=naming, check_command="python -c 'import <name>'", origin=static_cold_start)`. Skip `stdlib`/`project_local`.
2. **Curated map → pip Providers.** `CURATED_IMPORT_TO_PACKAGE` + `direct_name` fallback (`import_mapping.py`); curated = `trust=high`, direct_name = `trust=low`; attach all multi-provider candidates.
3. **Latent `retracts`.** Curated swap table seeds latent retractions keyed by target (activate when the target Need appears at runtime).
4. **Manifests → believed Needs.** `declared_dependencies → Need(pip_dist)`; `python_requires → Need(python_runtime, layer=interpreter)`.
5. **One speculative Z3 solve** over the declared closure → surface `conflicts_with` knowable from declared pins.

**Deliberate under-claim:** the entire `system`/`toolchain`/`runtime_env` layer + dynamic imports are absent at cold start (statically invisible). They are runtime-discovered (§6).

---

## 6. Runtime update — `ingest()` (deterministic core)

`ingest` fires on **every** observation, success or failure. **Minting** (steps 1–3) is failure-scoped; **certification + maintenance** (steps 4–7) run on both paths. **Step 1's parsing is committed as regex below; the LLM extension is OPEN — §10.**

**On a failure observation (minting + certification):**
1. **Classify** — `classify_dependency_failure(command, output)` → `DependencyFailure`; upsert `Failure` (dedup by id, bump `last_seen`).
2. **Indicate** — `infer_rule_based_constraints` (+ new branches §12) → `Need(s)`; add `indicates` edges.
3. **Root-cause link** — when a runtime fault reveals a lower-layer need, add `depends_on`; activate any latent `retracts` whose target now exists. *(One-shot chain hypothesis is an LLM gap-fill — §9/§10; reactive linking stays deterministic, §0 role mapping.)*
4. **State flip (host)** — host runs the Need's `check_command`, feeds rc; flip `unknown → proven` (rc 0) or `→ refuted` (rc≠0).
5. **Attempt** — on an install/apt/env transaction (or `z3_solve`), create `Attempt`; wire `used`/`addresses`; the subsequent check sets `outcome`; `resolved` retires the Failure, `failed` emits a `blocked_assignment`.
6. **Conflicts** — a new hard `version_range` Need triggers `solve_dependency_problem`; `unsat` → `conflicts_with`/`ConflictGroup` from the unsat core (§8).

**On a success observation (certification + invalidation — the anti-staleness path):**
- **7a. Positive certification.** A successful command is the positive-evidence path: re-run the `check_command` of any Need it plausibly satisfies (e.g. `pip install opencv` → re-check `python_import:cv2`) → flip `unknown → proven`. Without this, every success is invisible and the graph is stale.
- **7b. Invalidation-on-write.** Any command that **mutates a package** (install / upgrade / uninstall) walks `depends_on` from the touched target and demotes every downstream `proven` Need back to `unknown` (it must be re-certified). This is a graph traversal, not a re-run — the edges *are* the invalidation index. This is the single most important anti-staleness rule (§11).

New *nodes* per cycle are bounded by the live failure's neighborhood (failure-scope); *state updates* are bounded by the touched target's `depends_on` subtree.

---

## 7. `check_command` generation

Checks are **deterministic templates** keyed by `Need.kind`, instantiated at mint time and stored as a field. **They are not LLM-written** — the certifier must be unforgeable (a model that writes the check could write `true`). Generated once, re-run by the host many times.

| kind | template | example |
|---|---|---|
| `python_import:<mod>` | `python -c "import <mod>"` | `python -c "import cv2"` |
| `pip_dist:<pkg>` | `python -m pip show <pkg>` | `pip show psycopg2` |
| `version_range` / `pip_closure_consistent` | `python -m pip check` | — |
| `system_library:<lib.so>` | `ldconfig -p \| grep <lib.so>` | `ldconfig -p \| grep libGL.so.1` |
| `toolchain:<bin>` | `command -v <bin>` (or `<bin> --version`) | `pg_config --version` |
| `python_runtime:<spec>` | `python --version` (vs `<spec>`) | — |
| **root** `repo_tests_pass` | **the real test command** (`python -m pytest -q`) | — |

**Status in current code:** only goal Contracts carry checks (`goals.seed_backbone`); `extract.py` promotes atomics with `check=""`. The `check_for(kind,target,specifier)` registry is **new build work** (§12).

**Root-check caveat (honesty relocated, not solved):** the spec prescribes the real test as the root check and forbids collect-only, but cannot *force* the harness to run it. If the harness routes collect-only output as the `test_run` evidence, the root falsely proves. The burden moves to **evidence-routing**, which must be audited.

---

## 8. Z3 integration (behind the graph)

Z3 owns the **pip layer only** — version arithmetic. Entry: `resolver.solve_dependency_problem(diagnostics, latest_report, enable_z3=True)`; `max_packages=20`, `max_versions_per_package=8` (failure-scoped, not all of PyPI).

**Encoding:** one `Bool` per `(package,version)`; clauses = required `Or`, at-most-one, specifier exclusions, `requires_python`, transitive `requires_dist` `Implies`, blocked-assignment `Not(And(vars))` no-good cuts, no-floating. **Two-pass:** hard+soft → drop `soft_edges` (`sat_soft_relaxed`). Interpreter chosen 3.11→3.8.

**Graph mapping:** `selected_packages`→`Provider`s; `install_commands`→Attempt recipe; the solve→`Attempt(command="z3_solve")`; `blocked_assignments`→`Attempt.blocks`; `unsat_core`→`conflicts_with`/`ConflictGroup`. `status` drives trust: `sat`→`solved` tier; `solver_unavailable/error`→graph degrades to pip-layer diagnosis without version selection.

**Trust boundary:** Z3 `sat` = `solved`, **not** `proven`. The host must install the closure and pass the Need's check; a wrong pin → `blocked_assignment` → re-solve.

**Typed unsat core = build work (§12, "C-2").** Today `unsat_core` is free-text reasons with no clause↔Need map (clauses are per-(package,version)). Until `_Clause` carries `owner_need_id` and the core is grouped by owning Need, `conflicts_with` is a **heuristic**, not Z3-certified. Do not claim certified fidelity until this lands.

---

## 9. Actor split + LLM oracle interface

The deterministic graph is in charge and **calls** the LLM as a knowledge oracle for bounded sub-questions. Outputs are typed, low-trust, host-certified, anti-looped.

| Pipeline point | Oracle call (proposed) | Output slot | Certification |
|---|---|---|---|
| §6.2 indicate | `propose_provider(need)` | `Provider(source=llm, trust=low)` | host installs + runs need check → proven/ineffective/exhausted |
| §6.3 link | `propose_chain(failure)` | ordered `depends_on` Needs (low-trust) | each hop host-certified deepest-first; spurious hops dropped |
| §6.5 repair | `propose_swap(provider)` | `retracts` Provider | try; retract subtree on proof; resurrect on ineffective |
| §6.1 classify | `classify_fallback(log)` | `DependencyFailure` into fixed schema | **OPEN — §10** |

**Hard guards (all oracle calls):** the deterministic selector prefers curated/solver providers first; LLM outputs never flip `Need.state` (host check only); LLM never writes a `check_command`, never does version math, never declares `is_ready`.

---

## 10. ⚠ OPEN / TODO — LLM error parsing (discuss before locking)

The deterministic regex classifier (§6.1) is the committed baseline. Whether/how to add an **LLM fallback for error parsing + error-domain classification** is **not yet decided.** Candidate role (from the discussion): LLM as a *fallback normalizer* only when regex returns `not_dependency_related` / low-confidence — it extracts into the fixed schema while the deterministic policy still decides the dominant source (EnvGraph's pattern). High-value distinctions the regex can't make: env-fault vs repo-bug vs flaky/network; dominant-error selection in multi-error logs; novel build-tool formats.

**Questions to settle:**
1. **Trigger:** only on `not_dependency_related`, or also on a regex confidence threshold? How is "low-confidence" defined for a regex match?
2. **Output authority:** does the LLM only *normalize* (extract `failure_type`/`target`) with the deterministic policy choosing the layer, or may it assign the **domain/layer** directly (env-fault vs repo-bug vs flaky) — a thing no rule can decide?
3. **Schema fidelity:** how do we constrain the LLM to the fixed `DependencyFailure` schema + reject hallucinated entities (verify `target` appears in raw text / graph, à la EnvGraph grounding)?
4. **Confidence/provenance:** tag `source=llm, confidence=…`; how does downstream trust ordering use it?
5. **Dominant-error selection:** if the LLM picks the root error from a messy log, is that classification or a (separate) triage step? Where does it live?
6. **Cost/latency:** fallback only (keep determinism on the 90%) — confirm we never call it when regex matched.
7. **Eval:** measure classifier precision/recall on the new/uncovered rows *separately*; quantify the LLM-fallback lift over regex-only.

**Resolved (folded in §0/§6/§9):** reactive ingest is LLM-free — regex classifies, the host certifies, and an unresolved Need (no Provider) is the gap signal that hands off to the Planner's oracle next cycle. The LLM's primary leverage is **proactive** (`propose_chain` at cold start in the Planner), not error parsing in the Maintainer.

**Still OPEN (this section):** the *novel-error fallback* only — what to do when regex returns `not_dependency_related`/low-confidence. **Provisional stance (not committed):** LLM normalizes-into-schema as a fallback, runs in the **Planner** (keeps the Maintainer LLM-free), never flips state; deterministic policy keeps the source decision; domain-level calls (env-fault vs repo-bug vs flaky) are a distinct, explicitly-low-trust annotation host-certified by the resulting `check_command`.

---

## 11. ⚠ OPEN / TODO — Graph maintenance (discuss before locking)

The maintenance subsystem is **sketched, not locked.** The debate proposed the rules below; we need to decide which to implement, how, and where (if anywhere) the LLM helps. Note: pure bookkeeping should stay deterministic — the LLM's only plausible maintenance role is **semantic dedup**.

**Candidate rules (from the debate — to be confirmed):**
1. **Failure retirement** — `outcome=resolved` → `state=resolved`, drop from slice; `stale` after N cycles if indicated Need proven.
2. **Revocable `proven` (anti-staleness) — now committed (§6.7b, §0).** `proven` is not a freeze. Two mechanisms keep it honest, cheapest first: **(a) invalidation-on-write** — a package-mutating command walks `depends_on` from the touched target and demotes downstream `proven → unknown` (a traversal, not a re-run; the edges are the index); **(b) re-certify at the gate** — `is_ready()` re-runs `check_command` for any Need touched since last certified before finalizing (cost bounded to once, when correctness matters most; also closes the collect-only hole). Ground truth remains the clean-room rebuild (`ebsr ∧ pass_rate≥0.8`) — the graph is calibrated against it, never a substitute. *Open knob below: the trigger/scope of (a).*
3. **`ineffective` providers** — install rc 0 but check fails → `status=ineffective`, never re-picked; steer re-diagnosis of the layer.
4. **Anti-loop / no-good cuts** — failed Attempt → `blocked_assignment` → Z3 `Not(And(vars))`; extend to non-pip managers (apt/conda) as dead OR-branches.
5. **Retraction + resurrection** — chosen `retracts` provider retires subtree; if later `ineffective`, un-retire.
6. **Dedup** — content-hash upsert; `depends_on`/`conflicts_with`/`retracts` are sets.
7. **Conflict refresh** — `conflicts_with` re-derived per unsat solve, retired on `sat`; cold-start-seeded conflicts retired only by a re-solve over the same declared subset.
8. **Bounding + trim/act reconciliation** — 40-node/60-edge/16 KB slice; pin the deepest-unproven Need un-trimmable; trim breadth-first.

**Questions to settle:**
1. **Invalidation scope (the remaining knob on rule 2)** — invalidation-on-write is committed as a `depends_on` traversal; still to settle: does a `pip install X` invalidate only X's `depends_on` subtree, or all Needs sharing X's manager? Re-cert at the gate is bounded, but mid-run re-checks need a scope rule (subtree-only is the cheap default).
2. **Semantic dedup (the LLM question)** — should an LLM merge Failures/Needs that are the same root cause under different surface strings (paraphrased error, different `.so` name)? Advisory + reversible only? What's the rollback if it wrongly merges distinct nodes?
3. **Anti-loop for non-pip managers** — Z3 cuts only cover pip; how exactly do apt/conda `exhausted`/`ineffective` providers get enforced as no-go?
4. **Trim policy** — confirm breadth-first order (siblings → conflict peers → proven subtrees) and the un-trimmable pin; how does it interact with `next_action`?
5. **Resurrection edge cases** — order of un-retract vs re-validate; can a resurrected Need re-enter as `unknown` cleanly?
6. **Staleness/giveup** — when is a chain declared terminal (`IMPOSSIBLE`)? Keep deterministic (hard-vs-hard unsat) vs allow LLM *advice* (not decision)?
7. **State persistence** — maintenance assumes a persistent within-run graph; confirm the storage/serialization (the module is currently a per-call pure projection — `builder.py`).

**Provisional stance (for discussion):** rule 2 (revocable `proven`) is now **committed** as a correctness requirement (invalidation-on-write + re-certify-at-gate, §6.7); implement 1/3/4/6/8 deterministically alongside it; treat 5 (resurrection) as a correctness fix needing ordering rules; LLM maintenance limited to *advisory semantic dedup*, off by default.

---

## 12. Prerequisite code (gating — must land before the empirical section)

- **C-1: classifier rows** — add `build_failure` (`gcc`/`pg_config`/`Building wheel for X … error`), `env_service`, `dynamic_import` to `failure_classifier.py`; matching `infer_rule_based_constraints` branches incl. `native_library_missing → system_library` (currently returns no constraint, `constraints.py:30`). *Without C-1 the psycopg2 chain never materializes.*
- **C-2: typed unsat core** — extend `_Clause` (`z3_adapter.py`) with `owner_need_id`/`owner_provider_id`; group `unsat_core` clauses by owning Need to recover `conflicts_with` endpoints; on empty/timeout core → `conflict_unexplained` fallback node.
- **C-3: check registry** — `check_for(kind, target, specifier)` (§7), extending checks from goal Contracts to every Need.
- **C-4: persistent within-run graph** — promote the per-call projection (`external_graph/builder.py`) to a stateful, append-only `Graph` the agent threads across cycles.

---

## 13. Evaluation (summary; full plan in DESIGN §9)

- **RQ-1 diagnosis correctness** — on `(static evidence + captured failure log)`, correct layer + root-cause Need? Gold-labeled on the taxonomy buckets; runs on the `diagnostics` dict, agent-independent.
- **RQ-2 end-to-end** — graph+solver vs raw-log ReAct on **honest success** (`ebsr ∧ pass_rate ≥ 0.8`; never build-success/collect-only; score via `compute_essr.score_agent`, never `rat_results.json`).
- **RQ-3 ablations** — drop-solver / drop-cross-layer / drop-failure-scoping / drop-`retracts`.
- **Metrics** — repair cycles to green; root-cause hit rate; wasted-provider-installs avoided; unsat→sat via blocked_assignment; honest-vs-collect-only delta.

---

## 14. Other open decisions (forks, lower priority than §10/§11)

- **Granularity** — import-granularity Needs (`python_import:cv2`, package as data) vs a `python_package` node kind. *Lean: import-granularity (clean check, reuses `ids.contract_id` grammar).*
- **Seed-recipe authority** — solver `install_commands` run directly as the first recipe vs handed to the planner as a proposal. *Lean: direct for first install.*
- **`solved` as first-class projected status** vs a data flag. *Lean: first-class.*

---

## 15. References

- Code: `src/python_deps/` — `models.py`, `failure_classifier.py`, `constraints.py`, `graph.py`, `resolver.py`, `z3_adapter.py`, `import_graph.py`, `import_mapping.py`, `external_graph/{builder,dto,projectors,rendering,budget}.py`; spec of record `docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md`.
- Rationale + worked examples: `docs/DESIGN-deps-diagnosis-graph.md`. Debate artifacts: `docs/DESIGN-deps-diagnosis-graph-debate.md`. Visual: `docs/deps_diagnosis_graph_visualization.html`.
- Prior art: EnvGraph (localize-then-revise-repo; no solver), SetupX/XPU (cross-repo memory — relevant only to deferred GRM).
