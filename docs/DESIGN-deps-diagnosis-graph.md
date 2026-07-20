# A Failure-Scoped, Cross-Layer Dependency Diagnosis Graph for Docker Build Agents

*Design specification for a within-run, environment-only diagnosis overlay that sits in front of `src/python_deps/` and its Z3 solver. The graph is the agent-facing surface; Z3 is the engine behind it; the host certifies truth.*

---

## 1. Motivation

### 1.1 The agent's evidence is the wrong shape for the agent's decision

An LLM build agent that makes a fixed Python repository's tests run may change exactly one thing: the **environment** (pip installs, version pins, apt/system packages, env vars, interpreter). The repository source is frozen. The agent's only feedback is what the environment *produces under execution*: pip resolver logs, import tracebacks, `ldconfig` output, `gcc` errors, pytest collection failures.

That feedback is the wrong *shape* for the decision the agent must make. Consider the single most common symptom in this domain:

```
ModuleNotFoundError: No module named 'cv2'
```

This one string is consistent with **four root causes living in four different layers**:

| Layer | Root cause | Correct repair |
|---|---|---|
| **naming** | `cv2` maps to distribution `opencv-python`, not `cv2` | `pip install opencv-python` |
| **pip** | the distribution is simply undeclared | declare + install |
| **system** | the wheel installed, but `libGL.so.1` is missing | `apt-get install libgl1` *or* swap to `opencv-python-headless` |
| **interpreter** | the only compatible version needs Python ≥3.10 | bump the base image |

Raw logs force the LLM to **re-derive this cross-layer attribution from scrollback on every turn**. The observed pathology (documented in this project's own run analyses) is *symptom-treatment*: the agent re-runs `pip install opencv-python` against a missing `.so` and oscillates, because nothing in the log says "this is a system fault, not a pip fault."

Two structurally distinct symptoms make the point sharper:

- A **version conflict** (`scipy 1.11.0 requires numpy>=1.25, but you have numpy 1.21.0`) buried in a 300-line pip backtrack is *invisible* in scrollback, and it is **not a missing provider** — both `numpy==1.21` and `numpy>=1.25` are individually satisfiable. It is a *mutual exclusion between two needs*.
- A **build-time system dependency** (`pg_config executable not found` while building `psycopg2`) is a **multi-hop, cross-manager chain**: the pip distribution depends on a system package (`libpq-dev`) which depends on a toolchain (`gcc`). Re-running `pip install psycopg2` never converges.

### 1.2 Why a graph

A graph turns heterogeneous runtime evidence into typed, layered, root-cause nodes so that three failures of raw-log scaffolds disappear:

1. **Cross-layer ambiguity** → an explicit `layer` tag and a `depends_on` edge that *visibly crosses layers* make "which layer owns this symptom" machine-readable.
2. **Buried conflicts** → a `conflicts_with` relation, sourced from the Z3 unsat core, surfaces an irreducible version conflict as a first-class, budget-protected element the agent cannot scroll past.
3. **No accumulation** → each turn *appends* evidence to a persistent diagnosis and *invalidates* stale state, giving monotone progress instead of oscillation. Every node exists because of exactly one observation, so the diagnosis is auditable.

### 1.3 Why failure-scoped, not a full planner

A full-planner dependency graph would enumerate the entire resolved closure (every transitive package × version) up front. That is (a) network-heavy and expensive, (b) mostly irrelevant — the agent only needs the subgraph implicated by the *observed* failure, and (c) un-explainable — a reviewer cannot tell which node drove the current repair. **Failure-scoped** means the graph materializes only nodes/edges reachable from an observed `DependencyFailure`: the missing import, its candidate distribution, the conflicting pins, the missing `.so`. This keeps the prompt slice inside the existing 40-node / 60-edge / 16 KB budget (`external_graph/dto.py`), makes every node causally legible, and matches the agent's actual decision ("fix *this* failure"), not a planner's ("compute the whole world").

---

## 2. Design goals & non-goals

**Goals**

- **G1 — Diagnosis, not resolution.** The graph answers *which layer broke and why this symptom appeared*; Z3 answers *which versions are compatible*. Z3 stays strictly behind the graph and never sees natural language; the LLM never does version arithmetic.
- **G2 — Cross-layer.** One typed graph spans naming, pip-version, system-native, interpreter, and solver-conflict layers — including the two layers `python_deps` cannot currently model (system/toolchain) and which `projectors.py:109` deliberately drops.
- **G3 — Host certifies, never the LLM.** A `Need` becomes `proven` only when *its own* `check_command` returns rc 0, observed and fed back by the host — **never** because a `Provider` installed, and **never** via `pytest --collect-only`. This is the anti-hollow-success invariant.
- **G4 — Failure-scoped & pure.** The module never executes. Runtime evidence is injected via the existing `diagnostics` dict, exactly as `solve_dependency_problem(diagnostics, latest_report, …)` already consumes it. Every graph state is a deterministic function of `(static evidence + diagnostics)`, hence reproducible and unit-testable on captured-log fixtures.
- **G5 — Bounded & explainable.** ≤ 4 node types, ≤ 8 edge types, one acting rule. A reviewer grasps the schema in 30 seconds.

**Non-goals**

- The separate contract graph (`envstate/contracts`) — out of scope.
- Cross-repo memory / telemetry (GRM, SetupX-style transfer) — out of scope; this is **within-run only**.
- Editing the repository — forbidden; the environment is the only free variable.
- Discovering provider-swap escapes or native-dep chains from first principles — the graph *encodes and reasons over* a small curated hint table; it does not *learn* it (§11).

---

## 3. The core abstraction

### 3.1 Decision: four nodes, eight edges, a three-valued `Need.state`

We keep the codex seed's four node kinds — **Failure, Need, Provider, Attempt** — and justify each against deletion. We **adopt three improvements** debated across the candidate designs and **reject one over-formalization**:

1. **Kept the seed's drop of the `Probe` node.** A proof obligation is an *attribute* of the thing being proven, so `check_command` is a **field on `Need`**, not a node. (Parsimony, unanimous across designs.)
2. **Adopted the three-valued satisfaction state** (`unknown | proven | refuted`) as a **field on `Need`** — the load-bearing honesty mechanism. A `Need` flips to `proven` *only* by its own `check_command` (G3). This is the single sharpest idea from the capability-satisfaction angle, **imported without** that angle's AND/OR tree.
3. **Rejected reifying the whole graph as an AND/OR satisfaction tree.** For the median single-missing-package fault, a one-node `Need` suffices; a root+import+distribution+provider chain is over-engineering, and a typed transition system with non-monotonic state is *not* propositional logic and must not be sold as one. We keep `depends_on` as a plain edge with the acting rule "descend to the deepest unproven `Need`," which buys the same root-cause behavior at a fraction of the conceptual cost. (Resolves capability-angle weakness #1, #2.)
4. **Collapsed the seed's `Attempt resolved/failed Failure` into one `outcome` edge** carrying a `{resolved|failed}` attribute (keeps the edge count down; an `Attempt` that resolves X while spawning Y simply emits two `outcome` edges with different attributes).
5. **Added two edges the critiques proved are mandatory:** `conflicts_with` (Need↔Need, Z3-sourced) and **`retracts`** (Provider→Need, the provider-swap escape). Without `retracts`, the cross-manager swap the design claims as core value (opencv-python-headless *deleting* the `libGL` need) is invisible in the schema and left to LLM inference. We pay one edge to make it machine-readable.

The conflict family is handled by an edge plus, for the ≥3-way case, a single **`ConflictGroup`** record (a hyperedge surrogate) attached to all members — see §5.3 — so symmetric pairwise edges never misrepresent a 3-way irreducible conflict as three independent 2-way relaxations.

### 3.2 Node types

#### `Failure` — the only root node (1:1 with `DependencyFailure`)

```
id              sha1(failure_type + import_name + package_name + library)   # stable, dedup key
failure_type    one of the 7 classifier types + 3 NEW (build_failure, env_service, dynamic_import)
command         exact command that produced it
symptom_text    DependencyFailure.message  (~500-char regex excerpt)
details         DependencyFailure.details verbatim (required_by/requirement/library/python_specifier/...)
first_seen / last_seen   cycle ints
state           active | resolved | stale
```

**Why it exists:** it is the *only* structured fact the graph gets for free (`classify_dependency_failure → DependencyFailure`, `models.py:51`). Every other node is **derived** from it, so every `Need`/`Provider`/`Attempt` traces to exactly one observed symptom — the explainability anchor. 1:1 with `DependencyFailure` means zero new classification logic beyond the three new `failure_type` regexes (§4, §C-1).

#### `Need` — the cross-layer, host-certifiable obligation

```
id              kind:target          # python_import:cv2 | system_library:libGL.so.1 | version_range:numpy | ...
kind            python_import | pip_dist | version_range | system_library | toolchain | python_runtime
                | pip_closure_consistent | env_var | service        # env_var/service are ANNOTATION-only (see below)
layer           naming | pip | system | interpreter | solver_conflict | runtime_env
target          cv2 | opencv-python | libGL.so.1 | numpy | gcc | >=3.10
specifier       version/range/ceiling constraint when applicable    # NOTE: carries DIRECTION (see import_name_error)
pin_direction   none | floor | ceiling                              # ceiling = "must pin BELOW" (api_compat case)
check_command   THE proof obligation; rc==0 PROVES it. NOT pytest --collect-only.
state           unknown | proven | refuted                          # host flips; never the LLM
trust           high | medium | low      (from ImportPackageMapping.trust / DependencyConstraint.trust)
origin          failure_indicated | depends_on_derived | static_cold_start
source          which Failure/Need/curated-table produced it
```

**Why it exists:** `DependencyConstraint`, `ConstraintEdge` (required side), and `ConstraintGraph.required_packages` express a "need" in **three incompatible places** today; `Need` unifies them into one node with one vocabulary, and adds the two layers Z3 cannot model — `system_library`/`toolchain` (pip-cannot-provide; the row `projectors.py:109` drops) — as first-class kinds. The `layer` enum carries the *entire* cross-layer contribution at the cost of one field. `state` + `check_command` are what let the host **certify** rather than the LLM **assert** (G3), defeating the project-wide collect-only done-gate defect.

The `pin_direction` field resolves a taxonomy breakage all three critiques flagged: `import_name_error` (`cannot import name 'soft_unicode' from 'markupsafe'`) is a **version ceiling** on an already-proven Need, not a missing provider. Re-running the import check after an *upgrade* makes it worse. `pin_direction=ceiling` makes the corrective direction explicit and routes it to a `block_candidate` constraint in Z3 (§5.4).

#### `Provider` — a candidate action, tagged by manager

```
id                  manager:spec       # pip:opencv-python | pip:opencv-python-headless | apt:libgl1 | runtime:python3.11
manager             pip | apt | conda | runtime | env_directive      # env_directive only for annotation-layer needs
spec                install/select string (opencv-python==4.9.0 | libgl1 | a base-image tag)
version             Z3 (name,version) boolean identity, when from a PackageCandidate
provides_targets    list of Need ids it may satisfy (one Provider → many Needs)
retracts_targets    list of Need ids choosing it makes UNNEEDED (opencv-python-headless retracts system_library:libGL.so.1)
candidate_meta      has_wheel, yanked, requires_python, rank (from PackageCandidate)
trust               high | medium | low
status              candidate | selected | exhausted | ineffective    # see §4.3 for 'ineffective'
source              pypi | curated_import_map | curated_swap_table | unsat_core | agent_observed
```

**Why it exists:** one `Need` legitimately has **many** providers across managers — the design's core value and exactly where symptom-treatment fails. `libGL.so.1` is satisfiable by `apt:libgl1` **OR** by swapping the pip provider to `pip:opencv-python-headless` (which *retracts* the system need entirely). The explicit `manager` field is what lets the agent cross the pip/apt boundary the pip-only projector cannot represent. We collapse the prototype's `PackageVersion` / `PythonRuntime` / apt-package / wheel-swap into this one node because the agent's decision verb ("install/select this") is identical — only the manager differs. (We concede in §11 that an interpreter swap has run-wide blast radius the `manager` enum only weakly encodes.)

#### `Attempt` — per-run diagnostic memory (not a log)

```
id                   sha1(command + cycle)
command              exact executed transaction (a pip line, apt-get, an ENV bake, or 'z3_solve')
used_provider_ids    which Providers it instantiated
addresses_need_ids   which Needs it targeted
outcome              succeeded | failed | unknown
outcome_evidence     the check_command output the host used to decide
solver_status        sat | sat_soft_relaxed | unsat | solver_unavailable | solver_error   (when sourced from SolverResult)
relaxed_soft         from SolverResult.relaxed_soft_constraints (audit trail)
blocks               for failed attempts: the (package,version) set forbidden next solve = the Not(And(vars)) no-good cut
cycle                int
```

**Why it exists:** `record_solver_result` already appends every `SolverResult` to `diagnostics["solver_reports"]` — the existing attempt ledger — and `blocked_assignments` is the existing "this (pkg,version) was tried, do not retry" primitive (`graph.py:400`), consumed as a Z3 no-good cut `Not(And(vars))` (`z3_adapter.py:112`). Promoting both to first-class `Attempt` nodes preserves the no-good-cut **structure** the projector currently flattens to a text bullet (`projectors.py:533`), and is the anti-loop mechanism: a failed `Attempt`'s providers are marked `exhausted` and not re-offered.

### 3.3 Edge types

| Edge | From → To | Meaning | Why it earns its place |
|---|---|---|---|
| `indicates` | Failure → Need | this symptom implies this obligation | the entry edge: turns a log line into a typed, layered need. Direct output of `infer_rule_based_constraints`. |
| `depends_on` | Need → Need | source need cannot be proven until target (lower-layer) need is proven | **CENTERPIECE.** Converts symptom-treatment into root-cause repair: the agent acts on the deepest unproven need. The one relation `python_deps` has *no* representation for (no `requires_dist` edge kind exists; re-parsed every solve at `z3_adapter.py:266`). Cross-manager, cross-layer by design. |
| `provided_by` | Need → Provider | this provider may satisfy this need (many-to-many) | carries the multi-provider / provider-swap value; maps `SolverResult.selected_packages` + `PackageCandidate` sets onto the need they answer. |
| `retracts` | Provider → Need | choosing this provider makes this need UNNEEDED (deletes a `depends_on` subtree) | **NEW (8th edge), mandated by critique.** The cross-manager escape (`opencv-python-headless` voids `libGL`; `psycopg2-binary` voids the toolchain) is otherwise invisible. Encoded from the curated swap table, not discovered. |
| `conflicts_with` | Need ↔ Need | mutually exclusive needs (both satisfiable, empty intersection). **Sourced ONLY from Z3 unsat core**, never LLM-guessed | **CENTERPIECE 2.** Version conflicts are need-vs-need, not missing-provider. Gives the unsat core a typed home (§5). Symmetric; ≥3-way conflicts use a `ConflictGroup` (§5.3) instead of misleading pairwise edges. |
| `addresses` | Attempt → Need | this action targeted this need | lets a failed attempt mark refutation on the right need, not the whole graph. |
| `used` | Attempt → Provider | this attempt instantiated this provider | the anti-loop edge: a failed attempt's used providers are exactly the booleans Z3 must exclude (`Not(And(vars))`); do not re-offer them. |
| `outcome` | Attempt → Failure | this attempt `{resolved\|failed}` this failure (one edge, attribute-distinguished) | closes the loop: `resolved` retires the Failure and flips the addressed Need toward `proven`; `failed` refreshes a Failure and refutes the Need. |

### 3.4 ASCII schema

```
                         ┌──────────────────────────────────────────────┐
                         │  Z3 / ConstraintGraph  (ENGINE, behind graph) │
                         │  Bool(pkg,ver) · unsat_core · blocked_assign.  │
                         └───────▲───────────────────────┬──────────────┘
            unsat_core (typed)   │                       │  selected_packages / status
                                 │                       ▼
   ┌─────────┐  indicates   ┌─────────┐  provided_by  ┌──────────┐
   │ Failure │─────────────▶│  Need   │──────────────▶│ Provider │
   │ (root)  │              │ state:  │◀──────────────│ manager: │
   └────▲────┘              │ unk/prv/│   retracts     │ pip|apt| │
        │                   │ ref     │                │ conda... │
        │ outcome           │ layer:… │                └────▲─────┘
        │ {resolved|failed} │ check_  │                     │ used
        │                   │ command │                     │
   ┌────┴────┐  addresses   └──┬───┬──┘                ┌─────┴────┐
   │ Attempt │──────────────────┘   │ depends_on      │ Attempt  │
   │ blocks: │                       │ (cross-LAYER,   └──────────┘
   │ no-good │                       ▼  the root-cause edge)
   │  cut    │                  ┌─────────┐  conflicts_with   ┌─────────┐
   └─────────┘                  │  Need   │◀═════════════════▶│  Need   │
                                │ (deeper)│   (Z3 unsat_core; │ (deeper)│
                                └─────────┘    ≥3 → ConflictGroup)└──────┘

Acting rule:  act on the DEEPEST unproven Need reachable via depends_on;
              pick its highest-trust non-exhausted Provider;
              declare done only when every Need on the live chain is state=proven
              by its OWN check_command — never because a Provider installed.
```

---

## 4. Lifecycle

The lifecycle is the contribution. Cold start is the hypothesis space; runtime append is where diagnosis happens; maintenance keeps it small, monotone, and loop-free. All transitions are deterministic functions of `(static evidence + diagnostics)`.

### 4.1 Cold start (pure projection, no network, no execution)

Built from `PythonDependencyEvidence` (`models.py:202`) + curated tables + **one** speculative Z3 solve. **No `Failure` nodes exist yet.**

1. **Imports → naming-layer Needs.** From `scan_imports` (`import_graph.py`, AST + regex), every `ImportFinding` with `classification=="external"` becomes `Need(python_import:<name>, layer=naming, state=unknown, check_command="python -c 'import <name>'", origin=static_cold_start)`. `project_local` and `stdlib` imports are **not** materialized (not env-owned).
2. **Curated map → pip Providers.** From `CURATED_IMPORT_TO_PACKAGE` (6 entries) + the `direct_name` fallback: each `python_import` Need gets `provided_by → Provider(manager=pip, trust=high)` for curated mappings (`cv2 → opencv-python`) and `trust=low` for `direct_name` guesses. Multi-provider candidates (`cv2 → opencv-python | opencv-python-headless | opencv-contrib-python`) are all attached, ranked by trust. **The curated swap table** seeds `retracts` edges: `opencv-python-headless --retracts--> (a system_library:libGL.so.1 Need if/when it appears)`, recorded as a latent retraction keyed by target so it activates the moment that Need materializes at runtime.
3. **Manifests → believed-satisfiable Needs.** `declared_dependencies → Need(pip_dist, origin=static_cold_start)`; `python_requires → Need(python_runtime, layer=interpreter, check_command="python --version")`.
4. **One speculative Z3 solve** (`solve_dependency_problem` over the declared closure) runs purely to expose `conflicts_with` edges knowable from declared pins alone. If declared deps are already unsat, the unsat core seeds `conflicts_with` *before* the agent wastes a turn.

**Known vs hypothesis at cold start.** *Known:* the imports exist in source; the declared deps and `python_requires` are stated; curated mappings are facts for their 6 entries. *Hypothesis:* every Need's `state=unknown` (nothing is proven until a `check_command` passes); every `direct_name` Provider is a low-trust guess; and **the entire system/toolchain/runtime_env layer is absent** — `libGL.so.1`, the `gcc` chain, dynamic imports, env vars, and services are statically invisible by construction. The cold-start graph deliberately **under-claims**: "here are the obligations I can see statically; none are proven."

### 4.2 Runtime append (how evidence flips state)

The agent runs a **real** command and feeds raw output back via `diagnostics`. The graph never executes; it ingests. Fixed 6-step pipeline per observation:

1. **Classify.** `classify_dependency_failure(command, output) → DependencyFailure`. Upsert a `Failure` node (dedup by `id`; if it exists, bump `last_seen_cycle`, do not duplicate).
2. **Indicate.** `infer_rule_based_constraints` (+ the new branches in §C-1) emit the indicated `Need(s)`; add `indicates` edges. A dynamically-imported `lxml` that was invisible at cold start becomes a **brand-new** `Need(python_import:lxml)` and, by arriving late, **invalidates** any prematurely-complete state.
3. **Root-cause link.** When a runtime fault reveals a lower-layer need, append a `depends_on` edge from the existing higher-layer Need to the new one. Canonical: `cv2`'s import Need was believed-installable, but a runtime `libGL.so.1: cannot open shared object` (`native_library_missing`) creates `Need(system_library:libGL.so.1, layer=system)` and wires `python_import:cv2 --depends_on--> it`. For `build_failure`, the chain deepens: `pip_dist:psycopg2 --depends_on--> system_library:libpq-dev --depends_on--> toolchain:gcc`. **Any latent `retracts` edge whose target now exists is activated.**
4. **State flips (host certifies).** The agent runs a Need's `check_command` and feeds the rc as evidence; the host flips `unknown → proven` (rc 0) or `unknown → refuted` (rc≠0). A Need is `proven` **only** by its own check, **never** because a Provider installed. The `dynamic_import` case is the lifecycle proof point: cold start may show all import Needs proven, yet a real test triggers `importlib.import_module('lxml')` and the runtime `ModuleNotFound` appends a new Need that did not exist statically.
5. **Attempt recording.** When the agent executes an install/apt/ENV transaction (or a `z3_solve`), create an `Attempt`; wire `used → Providers`, `addresses → Needs`. The subsequent `check_command` decides `outcome` and adds the `outcome` edge. **`resolved`** retires the Failure; **`failed`** refreshes one and feeds a `blocked_assignment` into `diagnostics`.
6. **Conflicts.** A new hard `version_range` Need (from a `dependency_conflict` failure) triggers `solve_dependency_problem`. If `unsat`, the unsat core seeds `conflicts_with` edges / a `ConflictGroup` (§5).

New nodes per cycle are bounded by the single live failure's neighborhood — this preserves failure-scope.

### 4.3 Maintenance (invalidation, retirement, anti-loop, dedup, bounding)

1. **Failure retirement.** On `outcome=resolved`, set `Failure.state=resolved` and stop projecting it into the agent-facing slice (retain for the audit trail). A Failure not re-observed for N cycles **and** whose indicated Need is `proven` becomes `stale` and is trimmed first.
2. **Need resolution + re-validation.** A `proven` Need with no unproven `depends_on` descendants drops out of the frontier. **Re-validation trigger (resolves an unsoundness all three critiques raised):** when an Attempt completes, re-run the `check_command` of every `proven` Need on the same `depends_on` chain that shares a manager with the new install (cheap, bounded). If a later repair regressed an earlier-proven Need, it flips `proven → unknown` and re-enters scope. This breaks the "freeze-on-proven is unsound" trap.
3. **ok-but-still-blocked (the `ineffective` provider state).** If an Attempt's install returns rc 0 **but** the targeted Need's `check_command` still fails, set the used Provider's `status=ineffective` (distinct from `exhausted`, which is for failed installs). An `ineffective` provider is **never re-picked**, and a `satisfied_install_but_unsatisfied_need` marker steers the agent to **re-diagnose the layer** (e.g. wrong import→dist mapping) rather than exhaust providers into a dead-end `refuted` Need. (Resolves the most common real trap, flagged by all three critiques.)
4. **Anti-loop / no-good cuts.** Every failed Attempt's `(used Provider, addressed Need)` becomes a `blocked_assignment` fed back into `diagnostics`; the next Z3 solve adds `Not(And(vars))` (`z3_adapter.py:112`). The graph rule mirrors this for **all** managers, not just pip: an `exhausted` or `ineffective` Provider is a visibly-dead OR-branch the agent must honor — this covers the cross-manager (apt/conda) oscillation that the pip-only Z3 cut alone does **not** prevent. (Resolves the "cuts are pip-only and agent-fed" critique.)
5. **Provider-swap retraction + resurrection.** When a Provider with `retracts_targets` is chosen and proven, the listed system/toolchain Needs are **retired** (not refuted) — structurally recording "we escaped the system dep by swapping the wheel." **Resurrection rule:** if that Provider is later marked `ineffective`/`exhausted`, the retired Needs are **un-retired** and re-enter scope. (Resolves the unspecified-resurrection critique.)
6. **Dedup.** All nodes carry stable content-hash ids; re-observation upserts (bumps counts/timestamps) rather than duplicating. `depends_on`, `conflicts_with`, `retracts` are sets — no parallel edges.
7. **Conflict refresh.** `conflicts_with` edges are re-derived fresh on each unsat solve and **retired** on any `sat`/`sat_soft_relaxed` re-solve — they are *witnesses*, not durable facts. **Exception (resolves the cold-start-conflict drop bug):** a `conflicts_with` edge seeded from *declared pins* is retired only by a re-solve over the **same** declared-pin subset, not by an unrelated relaxation elsewhere.
8. **Bounding + trim/act reconciliation.** The slice inherits the dto budget (40 nodes / 60 edges / 16 KB). **Critical fix:** the deepest unproven Need of each live chain (the act target) is *also* the most frontier-distant (first to trim under the naïve rule). We **pin the deepest unproven Need of every live chain as un-trimmable** and trim *breadth first* — sibling providers, then `conflicts_with` peers, then proven/retired subtrees — before chain depth. Trim-priority and act-priority now agree.

---

## 5. The solver behind the graph

Z3 sits **strictly behind** the graph as the version-arithmetic engine. The graph is the agent-facing diagnosis surface; the LLM never does version math; Z3 never sees natural language.

### 5.1 Mapping graph elements to solver objects

| Graph element | Solver object | Citation |
|---|---|---|
| `Need(pip_dist / version_range)` + `specifier`/`pin_direction` | `ConstraintGraph.required_packages` (hard `Or`); `ConstraintEdge` `declared_specifier` (floor) / `block_candidate` (ceiling) | `graph.py:31`, `z3_adapter.py:205` |
| `depends_on` within the pip layer | `requires_dist` clauses (`Implies`), re-parsed from `PackageCandidate.requires_dist` | `z3_adapter.py:266` |
| `Provider(manager=pip)` | `Bool((name,version))`; `PackageCandidate` | `z3_adapter._build_variables` |
| `Attempt(command='z3_solve')` | a `SolverResult` | `models.py:167` |
| `Attempt.blocks` (failed install) | `blocked_assignments → Not(And(vars))` | `z3_adapter.py:112` |
| `conflicts_with` / `ConflictGroup` | `unsat_core` (via `assert_and_track`, `relax_soft=True`) | `z3_adapter.py:164` |

`SolverResult.status` maps to graph behavior: `sat` → providers offered high-confidence; `sat_soft_relaxed` → offered + annotated (a soft pin was dropped); `solver_unavailable`/`solver_error` → graph **degrades gracefully** to a pip-layer diagnosis *without* version selection (the cross-layer `depends_on`/system Needs still function and the agent picks providers by `trust`).

### 5.2 The headline engineering contribution: typed unsat core

**This is stated as work to do, not an assumed property** — the adversarial review correctly proved the seed's "thread a clause-id ↔ Need map" was unimplementable as written, because `_collect_*_clauses` emits **per-(package,version)** clauses, not per-Need clauses, labeled with an ephemeral `c{index}` and a free-text `reason` (`z3_adapter.py:184–203`). A single `version_range` Need fans out to *many* exclusion clauses (`declared_specifier`: one `Not(var)` per non-satisfying candidate, lines 227–232; `requires_dist`: one `Implies` per candidate, 266–291). There is no clause↔Need bijection to thread.

**The fix (a real code change, §C-2):** extend `_Clause` from `(expr, reason)` to `(expr, reason, owner_need_id, owner_provider_id)` — tag *every* clause `_collect_*_clauses` emits with the `Need`/`Provider` id it derives from. Then **group** the unsat-core clauses by their owning Need to recover Need-vs-Need endpoints:

- core clauses map to **exactly two** owning Needs → draw a `conflicts_with` edge between them.
- core clauses span **>2** owning Needs → emit a single **`ConflictGroup`** record (§5.3) referencing all of them — never misleading pairwise edges.
- **empty core / 5 s `_EXPLAIN_TIMEOUT` returns `()`** (lines 180, 188) → fall back to a `conflict_unexplained` node listing all hard-pin Needs as suspects, flagged `minimization_timed_out=True`, so the centerpiece **degrades** rather than **vanishes** on exactly the largest conflicts. (Resolves the empty-core critique.)

Until §C-2 lands, the paper presents `conflicts_with` as a **heuristic** (string-matched), not a Z3-certified edge. We do not claim fidelity the code cannot deliver.

### 5.3 `ConflictGroup` (the hyperedge surrogate)

```
ConflictGroup {
  id, member_need_ids: [≥2], core_reasons: [verbatim z3 strings],
  irreducible: True (always — produced under relax_soft=True, hard-only),
  z3_status: unsat, minimization_timed_out: bool
}
```

A 3-way conflict (A needs numpy<2, B needs numpy≥2, C pins numpy==1.9) is **one** `ConflictGroup`, not a triangle of three 2-way edges, so the agent does not pick the wrong single relaxation and re-trigger unsat. (Resolves the 3-way critique.)

### 5.4 Version conflict, fully worked (need-conflicts-need)

`pip check` emits `scipy 1.11.0 requires numpy>=1.25, but you have numpy 1.21.0`.

1. **Classify** → `Failure(dependency_conflict, details.requirement="numpy>=1.25", required_by=scipy)`.
2. **Indicate** → `Need(version_range:numpy, specifier=">=1.25", pin_direction=floor, hard=True)`. The declared `Need(version_range:numpy, "==1.21")` already exists. Two numpy needs now coexist.
3. **Solve** → `solve_dependency_problem` rebuilds the `ConstraintGraph`; no numpy version lies in `[==1.21] ∩ [>=1.25]` → `status=unsat`.
4. **Explain** → `_explain_unsat` re-solves with `relax_soft=True` (so only **hard** pins are blamed, never an LLM-imputed soft edge — a publishable trust property). With §C-2, the core clauses group to exactly the two numpy Needs.
5. **Project** → `conflicts_with` edge between `Need(numpy==1.21)` and `Need(numpy>=1.25)`.
6. **Agent reads** "irreducible conflict, relax one specifier" — not 300 lines of pip backtrack — and relaxes the soft repo pin. Re-solve returns `sat_soft_relaxed`; the `conflicts_with` edge is retired (witness no longer holds); the chosen numpy Provider gets `provided_by` edges; an Attempt installs it; `pip check` rc 0 proves the need. **The honest terminal proof is the real pytest run, not collect-only.**

If instead the conflict is **hard-vs-hard with no soft pin to relax** and the repo cannot be edited, the root reaches a terminal **`IMPOSSIBLE`** state (distinct from `unknown`/`refuted`): the agent gives up *honestly* with "environment cannot be made ready without forbidden repo changes," instead of looping. (Resolves the genuinely-irreducible critique.)

---

## 6. Worked examples

### 6.1 `cv2 → libGL.so.1` (naming → system; provider installs but need still unproven; provider-swap)

- **Cold start.** `scan_imports` finds `import cv2` → `Need(python_import:cv2, layer=naming, state=unknown)`; curated map → `Provider(pip:opencv-python, trust=high)` **and** `Provider(pip:opencv-python-headless, trust=medium, retracts_targets=[system_library:libGL.so.1])` (latent). No system Need yet.
- **Append #1.** Agent runs `pip install opencv-python` (Attempt A1, `used pip:opencv-python`, `addresses cv2`) then `python -c 'import cv2'` → `ImportError: libGL.so.1: cannot open shared object file`. Classify → `Failure(native_library_missing, library=libGL.so.1)`. Indicate → `Need(system_library:libGL.so.1, layer=system, check_command="ldconfig -p | grep libGL.so.1")`. Root-cause link: `cv2 --depends_on--> libGL.so.1`. **A1's install returned rc 0 but the check failed → A1 `outcome=failed` against the libGL Failure; `pip:opencv-python` is *not* exhausted (the wheel was fine, the `.so` was not), and cv2 stays `unknown`.** The latent `retracts` edge from `opencv-python-headless` activates. Multi-provider for the new need: `Provider(apt:libgl1)`.
- **Append #2 (path A — apt).** Agent picks `apt:libgl1` (A2), reruns `ldconfig` (proves the system Need) and `python -c 'import cv2'` rc 0 (proves cv2). A2 `outcome=resolved` retires the libGL Failure; both Needs `proven`. A1 remains in the audit trail so the agent does **not** re-diagnose cv2 as a pip problem.
- **Append #2 (path B — swap).** Agent instead installs `opencv-python-headless`; on proof, its `retracts` edge **retires** `system_library:libGL.so.1` (not refuted), and the `apt:libgl1` sibling is trimmed. *Manager guard:* a pip Attempt on a *system-layer* Need is type-rejected, killing the reinstall-the-wheel loop at the schema level.

### 6.2 `psycopg2 → pg_config / libpq-dev` (build-time, multi-hop, cross-manager)

- **Cold start.** `declared_dependencies` includes `psycopg2` → `Need(pip_dist:psycopg2)`; curated swap table attaches latent `Provider(pip:psycopg2-binary, retracts_targets=[toolchain:gcc, system_library:libpq-dev])`.
- **Append #1.** Agent runs `pip install psycopg2` → `pg_config executable not found ... Building wheel for psycopg2 ... error`. **This requires the new `build_failure` classifier (§C-1)** — without it the line falls to `not_dependency_related` and *nothing materializes*; this is why the classifier rows are a hard prerequisite, not future work. Classify → `Failure(build_failure)`. Indicate → `Need(system_library:libpq-dev, layer=system, check_command="pg_config --version")` and `Need(toolchain:gcc, check_command="which gcc")`. Root-cause chain: `pip_dist:psycopg2 --depends_on--> libpq-dev --depends_on--> gcc`.
- **Append #2.** Agent walks to the **deepest** unproven Need (`gcc`), `apt-get install build-essential libpq-dev`, proves both system Needs, then re-`pip install psycopg2`, proves `psycopg2`. **Or** chooses `pip:psycopg2-binary`, whose `retracts` edges retire the entire toolchain subtree in one move. Either way the chain converges; the classic infinite `pip install psycopg2` retry is structurally impossible.

### 6.3 `numpy<2` vs `numpy>=2` (unsat version conflict)

Fully traced in §5.4. Cold start seeds the conflict from declared pins if already unsat; append refreshes it from a runtime `dependency_conflict`; maintenance retires it on a `sat_soft_relaxed` re-solve. Terminal `IMPOSSIBLE` if hard-vs-hard with no relaxable pin.

---

## 7. How the agent uses it for diagnosis & repair

**Interface (pure, deterministic):**

```python
graph = diagnose(static_evidence)                 # cold start: PythonDependencyEvidence + curated tables + 1 solve
graph = ingest(graph, command, output, diagnostics)  # runtime append: classify → indicate → link → flip → record
action = next_action(graph)                        # one-next-action selector
done   = is_ready(graph)                           # honest success oracle
```

- **One-next-action.** `next_action` returns: act on the **deepest unproven Need** reachable via `depends_on` in the live failure's chain; pick its highest-`trust`, non-`exhausted`, non-`ineffective` Provider; prefer a `retracts` Provider when it voids a deeper subtree (cheaper repair). If the frontier is a `conflicts_with`/`ConflictGroup`, the action is **relax a soft pin**, not install a package.
- **Proven-not-installed success.** `is_ready` returns true **iff** the `root` real-test Need is `proven` (its `check_command` is the real `pytest -q` run) **and** every Need on every live chain is `proven` by its own check. A Provider installing, or `pytest --collect-only` succeeding, is **never** sufficient. *(Honesty caveat, §11: the formalism relocates the burden to evidence-routing — it prescribes the real test as the root check but cannot force the harness to run it.)*
- **Anti-loop via past Attempts.** Before proposing, the agent consults `used`/`outcome`/`status`: an `exhausted` or `ineffective` Provider is dead; a `blocked_assignment` is a permanent Z3 cut for the run. This is the anti-oscillation guarantee the flattened text-bullet projector loses.

---

## 8. Why it's explainable / the reviewer pitch

**Three-tier intuition:**

1. **Tier 1 — what happened:** a `Failure` is a classified symptom; `indicates` is the obligation it implies.
2. **Tier 2 — why it really happened:** `depends_on` is the root cause one layer down (cross-layer, machine-readable); `conflicts_with` is a Z3-certified minimal conflict; `retracts` is the cross-manager escape.
3. **Tier 3 — what we did and whether it worked:** an `Attempt` with its `outcome` is what was tried and whether the **host proved it** (not the LLM, not a successful install, not collect-only).

Every node exists because of exactly one observation; every edge because of exactly one inference rule. A reviewer reads the diagnosis off the graph as a sentence — *"this Failure indicates this Need, which depends_on this lower Need, provided_by these alternatives, on which these Attempts succeeded or failed."*

**Single-sentence claim:** *EnvGraph diagnoses which repo file to edit; SAT resolvers compute which versions are compatible; SetupX transfers fixes across repos — we diagnose which **environment layer** caused this symptom, with a deterministic solver behind the graph and the host (not the LLM) certifying every fix, and we never touch the repo.*

---

## 9. Evaluation plan

**RQ-1 (diagnosis correctness, pure).** Given `(static evidence + a captured failure log)`, does the graph assign the correct **layer + root-cause Need**? Gold-labeled per the **10–11 taxonomy buckets** (7 existing + `build_failure`, `env_service`, `dynamic_import`). This RQ runs entirely on the injected `diagnostics` dict, independent of agent policy — a cleaner, more reviewable RQ than EnvGraph's end-to-end-only diagnosis eval. **Prerequisite:** report **classifier precision/recall on the three new rows separately** (a `KeyError` is regex-indistinguishable from an app bug; `env_service` will have a high false-positive rate that must be measured, not hidden).

**RQ-2 (end-to-end repair).** On a fixed-repo Docker-setup benchmark (Repo2Run / RAT-style, 50-repo set), does graph+solver beat raw-log ReAct on **real test-pass** (`ebsr ∧ pass_rate ≥ 0.8`, the project's honest-success definition — *not* build-success, *not* collect-only)?

**RQ-3 (ablations, mirroring EnvGraph Table III).** drop-solver (graph-only, picks by trust); drop-cross-layer (pip-only nodes, the current projector); drop-failure-scoping (full-closure planner); drop-`retracts` (no swap escape). Isolates each of the four novelty properties.

**Metrics (all readable from `diagnostics`, no new instrumentation):**

- **repair cycles to green** (lower is better);
- **root-cause hit rate** (did the agent act on the deepest unproven Need vs the symptom);
- **wasted-provider-installs avoided** (count of `ineffective`/`exhausted` re-picks prevented vs the raw-log baseline) — the direct anti-oscillation metric;
- **solver invocations** and **unsat-core size** (typed vs free-text) from `record_solver_result` / `solver_invocations`;
- **unsat→sat conversions via a blocked_assignment** (no-good-cut efficacy);
- **honest-vs-collect-only delta**: hollow-success rate when success is gated on per-Need checks vs `pytest --collect-only`.

**Honest scoping of the median case:** report the ~4-node cost for the trivial `module_not_found → one pip provider → import works` fault explicitly, and show the graph earns its keep on the multi-hop / conflict minority — pre-empting the over-engineering objection.

**Datasets.** The taxonomy itself is the validation set: each row is a `(error string, expected root Need, expected check_command, symptom-treatment anti-pattern)` tuple. The three new rows double as a **measured coverage delta** in `failure_classifier.py`.

---

## 10. Related work & novelty (honest)

**Not novel (stated plainly):** (a) graph-structured agent memory/world-models; (b) SMT/SAT/PubGrub dependency *resolution* — PubGrub already gives good unsat messages; (c) import→package mapping and regex failure classification are engineering.

**The novel composition** is the **conjunction** of four properties no prior system holds together:

1. **agent-facing root-cause DIAGNOSIS** (which layer is to blame), not version RESOLUTION and not raw-log repair-cuing;
2. **cross-layer** scope — one typed graph spanning naming + pip + system-native + interpreter + solver-conflict, grounding a symptom to the layer that owns it;
3. **solver-behind-the-graph** — Z3 does the version arithmetic, its unsat core is re-projected as a typed conflict element, the LLM never does version math;
4. **failure-scoped & pure** — only the observed-failure neighborhood is materialized, the module never executes, evidence is injected, so diagnosis is reproducible and unit-testable.

**Positioning.** *EnvGraph* is the nearest neighbor but solves the **opposite** free variable: it generates/revises the **repo** (its `G_int` and "residual implementation bug" classes are repo-source faults, out of scope here) and has no version solver, no system/native layer, no interpreter reasoning. *SetupX/XPU* is **cross-repo** experiential memory (orthogonal, composable, not competing — we are within-run). *PubGrub/uv/resolvelib* operate only in the pip/version layer and emit prose, not a typed graph an agent grounds symptoms onto. *Repo2Run/EnvBench/PIPer* — the same task and fixed-repo constraint — feed raw logs to a ReAct loop with no diagnosis layer; they are our baseline family.

We concede the genuinely new code is modest: `depends_on` linking + typed-core grouping (§C-2) + `retracts` + three classifier rows (§C-1) + the state lifecycle. The novelty is honest but rests on the conjunction, not on any single ingredient.

---

## 11. Limitations & open questions

- **Prerequisite code (must land before the empirical section, §C):** (C-1) three classifier rows — `build_failure` (`gcc`/`pg_config`/`Building wheel for X … error`), `env_service`, `dynamic_import` — and matching `infer_rule_based_constraints` branches, **including** a `native_library_missing → system_library` branch (it is classified but currently returns no constraint, `constraints.py:30`); (C-2) the `_Clause.owner_need_id` provenance + unsat-core grouping. Until these land, the flagship `psycopg2 → libpq-dev → gcc` example is not code-producible and `conflicts_with` is heuristic, not certified. **The paper must not claim capabilities the code cannot demonstrate.**
- **`depends_on` for system/toolchain is runtime-only.** The `cv2 → libGL` edge cannot be drawn at cold start (the `.so` is invisible until import executes). A static native-dep hint table (psycopg2→libpq-dev, cv2→libGL) could seed common chains but reintroduces curation the design otherwise minimizes — a real over/under-engineering tension. We choose the curated swap table for `retracts` (high value, 2–3 entries) and leave `depends_on` runtime-discovered.
- **`env_var` / `service` are demoted to agent-asserted annotations**, **not** a diagnosed layer: `python_deps`/Z3 contribute zero grounding (no import/package/Z3 representation; checks may need a live service). They ride along as agent facts and are **excluded from the cross-layer-diagnosis claim and the ablation** so they do not inflate the contribution.
- **Root honesty is relocated, not eliminated.** The model prescribes the real `pytest -q` as the root check but cannot *enforce* that the harness runs it; if the harness feeds collect-only evidence as a `test_run`, the root falsely proves. The honesty burden moves to evidence-routing.
- **`trust` is a 3-value label, not calibrated.** The agent has no principled threshold for attempting a low-trust `direct_name` guess vs probing first; the multi-provider value depends on agent policy the graph does not specify.
- **Provider-swap and native chains are encoded, not learned.** The graph reasons over the curated swap table; it does not discover that `psycopg2-binary` avoids the toolchain. Claiming discovery would overstate the contribution.
- **Open question:** can a single static native-dep hint table (Jayint-style) be added without compromising the "diagnosis under-claims at cold start" honesty property, and does seeding common chains measurably reduce repair cycles, or merely move the curation cost?