# Design: Concise Contract Graph (v2)

> **Status:** approved design, ready for implementation planning.
> **Date:** 2026-06-17
> **Supersedes the schema of:** the implemented `v1g` contract graph
> (`src/envstate/contracts/`, 11 nodes / 12 edges).
> **Realizes:** `docs/DESIGN-concise-contract-graph.md` (the proposal), with the
> ambiguities in that doc resolved by the decisions in §2.

---

## 1. Goal

Replace the implemented `v1g` contract graph — an 11-node / 12-edge
inventory-and-provenance graph — with the **concise 3-node / 3-edge planner
overlay** described in `docs/DESIGN-concise-contract-graph.md`.

The guiding split:

```
WorldModelMap = what is true / what happened        (host-owned inventory & provenance)
ContractGraph = what matters / what to fix next      (compact fault/repair overlay)
```

The current code builds a full mirror of environment state in the graph (one
contract per declared dependency, plus materialized Failure / Capability /
CommandExecution / Requirement / EnvironmentRevision nodes). This design makes
the graph a small, planner-facing fault-localization structure whose status is
always projected from host evidence, never independently stored.

This is the gated `v1g` arm only (`orchestrator.py:50` `enable_contract_graph=False`).
The default `v1` / `arm0` paths are untouched. Because the graph is off by
default, the on-disk graph format may break freely — no compatibility shims.

---

## 2. Design decisions (resolved)

These were the open problems in the proposal doc; each is now fixed.

1. **Blocker ownership — Maintainer-owned, host-grounded.**
   The Maintainer (LLM) creates `Blocker` nodes by interpreting failures. The
   host does *not* own them. Guardrail: every `Blocker.evidence_refs` must point
   at a real WorldModelMap command (validator-enforced), and the host runs a §9
   regex to hand the Maintainer a *candidate subject*. This lands cleanly on the
   §11 split: **host owns `satisfied`, Maintainer owns `violated`** (via Blockers).

2. **Status — pure per-cycle projection.**
   Contract status is recomputed every cycle from live evidence into exactly
   three values: `satisfied` (host check passed at current revision), `violated`
   (an active Blocker `violates` it), `unknown` (neither). No stored status
   stream as source of truth. `REPAIR_ATTEMPTED` and `INVALIDATED` are dropped;
   "repair attempted" becomes `Attempt.outcome`, not a contract status.

3. **Coverage — bulk path + one-shot import sweep.**
   Cold-start runs the normal bulk install + `pytest --collect-only`, **plus one
   deterministic host command** that tries importing every declared dependency in
   a single shot (reusing `KNOWN_IMPORT_NAMES` / `resolve_import_name`) and
   reports the failures as WorldModelMap command evidence. This preserves a
   complete missing-dep signal at O(1) commands with **no per-dep graph nodes**.

4. **Planner — full multi-step RecipePatch.**
   The Planner emits `apply_recipe_patch` with `steps[]`, each step carrying its
   own `target_node_ids`. It reads a three-section render (World State / Repair
   Map / Repair Frontier) built from `depends_on` traversal and by-layer frontier
   grouping. `Attempt.outcome=ok_but_still_blocked` makes partial progress visible.

5. **Maintainer writes — strip semantic writes, keep host-derived in place.**
   The Maintainer stops writing `open_problems` / `notes` into the map; those
   become graph state (`open_problems` = derived view over active Blockers;
   `notes` → `diagnostic_notes`). The deterministic auto-resolve logic moves to
   mark Blockers inactive. `done_flag` / `progress` computation stays where it
   physically lives — they are already host-derived deterministic values, and
   `done_flag` is the sensitive success gate; relocating it is orthogonal.

6. **Migration — clean break, one pass.**
   Replace the graph + integration in a single cohesive change (safe because the
   arm is off by default). Internal work is ordered by dependency with tests at
   the end; it does not need to stay green at every intermediate commit.

7. **Atomic-contract promotion — both host and Maintainer.**
   The host deterministically promotes atomic contracts from unambiguous
   signatures (no LLM call); the Maintainer promotes the rest semantically. This
   mirrors §9's matching order (deterministic signatures before LLM interpretation).

8. **Recipe execution — autonomous BuildAgent, not a host-driven step engine.**
   The RecipePatch is executed *whole* by the BuildAgent's existing mini-ReAct
   loop (seeded with the full ordered recipe instead of one goal), not driven
   step-by-step by the host. Per-step `Attempt.outcome` is derived from the host
   **status projection at the recipe's validate steps** — not from attributing
   shell commands to steps. Steps run in order; on an unrepairable failure the
   BuildAgent stops and reports, and the next Planner cycle re-plans
   (continue-independent-steps deferred). Budget scales with step count. See §9.4.
   *Rationale:* the fault-localization win comes from the Planner+graph, not from
   thinning the BuildAgent; this keeps the BuildAgent's valuable same-cycle local
   adaptation (e.g. `libgl1` → `libgl1-mesa-glx`), is the lowest-risk change on an
   already-large rewrite, and gives weaker models slack to recover from a bad recipe.

---

## 3. The "host" (terminology)

The system has four actors; three are LLMs (**Planner**, **BuildAgent**,
**Maintainer**). The **host** is the fourth: the deterministic, non-LLM Python
code — the orchestration harness plus everything that certifies facts. LLMs
propose, interpret, and decide; the host executes, observes, and certifies. It
is the source of truth.

Host responsibilities (existing): runs the loop (`orchestrator.py`); executes
BuildAgent commands in the sandbox and records them in the `ActionLedger`; runs
independent read-only probes (`snapshot.py`, `validators.run_confirmed_validators`);
folds deterministic facts into the map (`world_model.apply_deterministic`);
certifies the graph (`projection.refresh_host_graph`); owns the success gate
(`done_flag`, set only on a real `pytest` pass); deterministic auto-resolve.

Host responsibilities (new): runs the cold-start import sweep; maintains
`dependency_state`; projects Contract status, derives Attempt outcome,
auto-resolves `Blocker.active`; deterministically promotes obvious atomic
contracts.

---

## 4. Schema

### 4.1 Node types (3)

**`Contract`** — an operational obligation.
- `id`: `contract:<kind>:<subject>`, or `contract:goal:<name>`, or 2-segment
  foundational `contract:<name>` (e.g. `contract:python_version_compatible`).
- `level`: `goal` | `atomic`
- `kind`: `python_import` | `python_package_installable` | `system_library` |
  `binary` | `service` | `env_var` | `build_command` | `test_command` |
  `verification` | goal kinds
- `subject`, `layer` (`deps`|`system`|`runtime`|`build`|`tests`|`config`)
- `check`: host-verifiable command
- `source_refs`, `evidence_refs`, `description`, `metadata`
- **Status is never stored** — projected each cycle (§6.3).

**`Blocker`** — a normalized runtime symptom (Maintainer-created, host-grounded).
- `id`: `blocker:<slug(signature)>`
- `signature`
- `kind`: `module_not_found` | `missing_binary` | `missing_system_library` |
  `version_conflict` | `build_failure` | `service_unreachable` |
  `env_var_missing` | `test_collection_failure` | `unknown`
- `layer`, `root_or_downstream` (`root`|`downstream`|`unknown`)
- `summary`, `evidence_refs` (**must** cite real commands)
- `active` (host-controlled), `metadata.extracted_subject`

**`Attempt`** — a repair (Planner-proposed, host-derived outcome).
- `id`: `attempt:<slug>`
- `intent`, `kind` (`python_install`|`system_install`|`env_config`|
  `service_start`|`build_fix`|`validation`|`test_retry`|`inspect`|`other`)
- `proposed_by` (`planner`|`build_agent`|`host`|`maintainer`)
- `commands`, `outcome` (`pending`|`ok`|`failed`|`ok_but_still_blocked`),
  `outcome_reason`
- `evidence_refs`, `created_from_target_node_ids`, `metadata`

### 4.2 Edge types (3)

- `violates`: Blocker → Contract (Maintainer)
- `addresses`: Attempt → Contract (host-committed from a step's `target_node_ids`;
  collapses today's `repaired_by` + `targets`)
- `depends_on`: Contract → Contract (host seeds the backbone; Maintainer adds
  semantic deps)

### 4.3 Collapse from v1g

```
RepoArtifact        -> Contract.source_refs            (delete node)
Requirement         -> WorldModelMap.required          (delete node)
Capability          -> Contract.status (projected)     (delete node)
Validator           -> Contract.check (scalar)         (delete node)
VerificationTarget  -> goal Contract                   (delete node)
CommandExecution    -> WorldModelMap command evidence  (delete node)
EnvironmentRevision -> WorldModelMap revision evidence (delete node)
Failure             -> Blocker
OpenProblem         -> Blocker
Transition          -> Attempt
```

---

## 5. Ownership (field-level rule, replaces the binary node partition)

| Actor | May create / write |
|---|---|
| **Host** | goal + foundational Contracts; deterministic atomic-Contract promotion; `depends_on` backbone; **Contract status** (projected); **Attempt.outcome** (derived); **Blocker.active** (auto-resolve); `addresses` edges |
| **Maintainer** | Blocker creation + classification (`kind`/`root_or_downstream`/`summary`); atomic-Contract promotion; semantic `depends_on`; Contract `description`; `diagnostic_notes` |
| **Planner** | Attempt proposals (`intent`/`kind`/`commands`/`target_node_ids`) via RecipePatch steps |

Invariant: **the graph never stores mutable truth.** Status, outcome, and active
are recomputed from host evidence every cycle, so graph/world-model disagreement
is structurally impossible — the world model always wins.

---

## 6. Lifecycle

### 6.1 Per-cycle data flow

The loop is **Planner first, Maintainer last** (matching the proposal's §8 and
today's `orchestrator.py`): the Maintainer's semantic patch feeds the *next*
cycle's render, so there is one cycle of latency between a failure and the
Maintainer's interpretation of it (deterministic host promotion has no latency).

```
1. Host fact refresh   -> fold installed/env/system + dependency_state into WorldModelMap
                          (cold-start: bulk install + pytest collect + one-shot import sweep)
2. Host graph project  -> seed/refresh goal+foundational contracts; deterministically promote
                          obvious atomics from new signatures; PROJECT status,
                          AUTO-RESOLVE Blocker.active. (no per-dep nodes)
3. Planner (LLM)       -> 3-section render -> RecipePatch(steps[].target_node_ids) | done | giveup
4. Orchestrator        -> commit Attempts (addresses); BuildAgent runs the WHOLE recipe in its
                          mini-ReAct loop (in order, local repair within step scope, stop-and-
                          report on unrepairable failure); record command evidence in WorldModelMap
5. Host outcome derive -> re-project status from validate-step evidence; Attempt.outcome from the
                          target contract's projected status (satisfied / still-violated). See §9.4
6. Maintainer (LLM)    -> semantic graph patch only (blockers / contracts / edges / classification
                          / notes); validated scope=maintainer; no map writes; feeds next cycle
-> repeat
```

### 6.2 Cold-start (cycle 0, deterministic)

1. Repo scan → `repo_layout` (exists).
2. Manifest extraction → `required` + `dependency_state.declared` + `build_system`.
3. Runtime/build detection → `language`, python version, package manager, test framework.
4. Seed the coarse backbone (goal + foundational only):
   ```
   contract:goal:repo_tests_pass (required)
     -> repo_tests_collect -> repo_imports_work, test_runner_available
     -> repo_imports_work  -> repo_deps_installed
     -> repo_deps_installed -> package_manager_available, python_version_compatible
     -> repo_build_ready, repo_services_ready, repo_config_ready
   foundational atomics: python_version_compatible, package_manager_available,
                         test_runner_available, project_installable
   ```
   **No per-dep contracts.** Bulk deps live in `required` + `dependency_state`.
5. First action = bulk path: `pip install -r …` / `pip install -e .` +
   `pytest --collect-only` + the one-shot import sweep.

### 6.3 Status projection (host, each cycle)

`project_status(graph, contract)` → `satisfied` if a host check passed at the
current revision · `violated` if an active Blocker `violates` it · else
`unknown`. Nothing stored as truth.

### 6.4 Lazy contract formation

- **Host deterministic promotion** — clear signatures → atomic contracts, no LLM:
  `ModuleNotFoundError: yaml` → `contract:python_import:yaml`;
  `pg_config … not found` → `contract:binary:pg_config`;
  `ImportError: libGL.so.1` → `contract:system_library:libGL.so.1`.
- **Maintainer semantic promotion** — `add_contracts` for obligations needing
  reasoning (e.g. `psycopg2_installable depends_on pg_config`).
- Both must attach under the goal backbone (validator-enforced reachability).

### 6.5 Blocker lifecycle

- Maintainer creates from host command evidence (`evidence_refs` → real cmd;
  host supplies the §9 candidate subject).
- Host auto-resolves: flips `active=False` when `extracted_subject` is confirmed
  in `installed`/`system_installed` (the logic moving out of
  `world_model._auto_resolve_problems` / `_auto_resolve_system_problems`).
- `open_problems` becomes a derived view over active Blockers (compat).

### 6.6 Attempt lifecycle & outcome derivation

- Planner proposes Attempts inside RecipePatch steps (`proposed_by=planner`,
  `commands`, `created_from_target_node_ids`).
- Host commits (`addresses` edges) → BuildAgent runs the recipe → host derives
  outcome **from the target contract's re-projected status** (measured by the
  recipe's validate steps + host probes — *not* by attributing shell commands to
  steps; see §9.4):
  - `ok` = the step's commands ran (no hard rc≠0) **and** the target contract
    projects `satisfied`
  - `failed` = a step command hard-failed and could not be locally repaired
  - `ok_but_still_blocked` = commands ran but the target (or a child contract)
    still projects `violated` (an active Blocker remains)
  - `pending` = proposed, not yet run

---

## 7. Maintainer patch contract

```json
{
  "add_contracts": [],                 // atomic only; must attach under backbone
  "add_blockers": [],                  // evidence_refs MUST cite real commands
  "add_edges": [],                     // violates / depends_on ONLY (host owns addresses)
  "update_blocker_classification": [], // {blocker_id, root_or_downstream, kind, summary}
  "update_contract_description": [],   // {contract_id, description}
  "diagnostic_notes": []
}
```

Dropped vs today: **no `add_status_events`** (status is projected; "violated" is
expressed by creating a Blocker), **no `add_attempts`** (attempts come from the
Planner via host commit), **no map writes**.

`diagnostic_notes` are stored as a small capped list on the `ContractGraph`
(most recent ~10), surfaced in the next cycle's Repair Map render. They are
advisory only and never affect status, outcome, or readiness.

---

## 8. Validator rules (scope=maintainer)

1. Edge type/endpoint validity (3 edges).
2. **Ownership (field-level):** Maintainer may not set Contract status, write
   `Attempt.outcome`, set `Blocker.active`, create goal/foundational contracts,
   or add `addresses` edges. *(Closes today's `update_nodes` ownership hole —
   updates are field-scoped and ownership-checked.)*
3. **Grounded blockers (new):** every `Blocker.evidence_refs` must point at a
   real WorldModelMap command.
4. **No inventory mirrors (new):** reject a patch adding more than a small bound
   (`MAX_PROMOTIONS_PER_CYCLE`, default 8) of atomic contracts in one cycle, or
   any contract with no failure/goal grounding.
5. **Backbone attachment (new):** every new atomic Contract/Blocker must be
   reachable under a goal contract via `depends_on`/`violates`; reject orphans
   when a reasonable parent exists.
6. Reference-integrity: reject patches citing nonexistent nodes/evidence.

---

## 9. Planner interaction

### 9.1 Input — three-section render

1. **Deterministic World State** — host-certified facts: base/runtime, package
   manager + build system, declared-vs-installed, `dependency_state` highlights,
   recent command evidence, progress / final-verification state. Authoritative.
2. **Contract Graph Repair Map** — required goal contracts, violated/unknown
   contracts on goal paths, active Blockers, recent Attempts + `outcome`,
   relevant `depends_on` paths. (Conflicts with World State → trust World State.)
3. **Current Repair Frontier** — unresolved *root* contracts grouped by layer,
   Attempts to avoid (already-failed), validators to run after repair.

### 9.2 Output

`action ∈ {apply_recipe_patch, done, giveup}`:
- `apply_recipe_patch`: top-level `target_node_ids` + `recipe_patch.steps[]`,
  each step `{id, kind, command, target_node_ids}` — a coherent multi-step recipe.
- `done`: `satisfied_goal_contract_ids` (advisory; host gate decides).
- `giveup`: `reason`.

### 9.3 New dataclasses & helpers

- `world_model.py`: `RecipeStep(id, kind, command, target_node_ids)`,
  `RecipePatch(steps)`; `PlannerDecision.action` adds `apply_recipe_patch`.
- `graph.py`: `depends_on` walk from goals, root-blocker query, frontier-by-layer
  grouping, `goal_ready` (over `project_status`).

### 9.4 Planner ↔ BuildAgent execution contract

The RecipePatch is the Planner's authored intent; the **BuildAgent executes it
whole** in its existing mini-ReAct loop (`build_agent.py`), *not* driven
step-by-step by the host. Division of labour: **Planner = global recipe designer
(authors the ordered, concrete commands); BuildAgent = local executor/debugger
(runs them, repairs locally within step scope, does not redesign the recipe).**

- **Whole-recipe, autonomous.** `BuildAgent.run` is seeded with the full ordered
  recipe (a checklist of concrete `command`s incl. `validate`-kind checkpoint
  steps) instead of a single `done_when`. It keeps its current latitude to fix
  *local* execution errors (wrong package name, missing compiler/header, wheel
  build failure) but must not invent a different strategy.
- **In order; stop-and-report on unrepairable failure.** Steps run top-to-bottom.
  If a step's command hard-fails and local repair can't fix it within the
  step's budget, the BuildAgent stops and reports back; the host still projects
  status (so any already-satisfied targets are recorded), and the **next Planner
  cycle re-plans** from the updated graph. *Continue-independent-steps is
  deferred* (it needs inter-step dependency tracking not worth it for v1).
- **Outcomes via projection, not attribution.** The host does **not** map shell
  commands to steps. Each step's `Attempt.outcome` is derived from whether its
  `target_node_ids` project `satisfied` after the run — measured by the recipe's
  `validate` steps + host probes (§6.6). This keeps per-step outcomes clean
  despite whole-recipe execution.
- **Budget scales with steps.** Replace the flat `LOCAL_BUDGET=8` with
  `LOCAL_BUDGET_BASE + k·num_steps` (capped), with the existing
  repeated-identical-failure stuck-guard (`build_agent.py:59-72`) as the runaway
  backstop.
- **Resilience note.** Whole-recipe execution + local-repair latitude + validate
  checkpoints + next-cycle re-plan is what lets weaker models recover from a
  partially-wrong recipe; a thin host-driven command-runner would have less slack.

---

## 10. File-by-file change map (clean break, one pass)

| File | Change | Scope |
|---|---|---|
| `schema.py` | 3 NodeType / 3 EdgeType / 3-row EDGE_RULES / 3-value status; add `BlockerKind`, `AttemptKind`, `AttemptOutcome`; field-level ownership constants; drop `ValidationState`; keep `redact_secrets` | rewrite |
| `nodes.py` | keep generic `Node`/`Edge`; drop `ContractStatusEvent` | edit |
| `graph.py` | drop `status_events`/`latest_status`; add `project_status` + traversal helpers | rewrite |
| `ids.py` | add `blocker_id`/`attempt_id` + `contract:python_import:`/`binary:`/`system_library:`/`service:` + 2-seg foundational; delete dead namespaces | edit |
| `goals.py` | rewrite seed → coarse backbone + foundational; `evaluate_goal_readiness` over `project_status` | rewrite |
| `projection.py` | rewrite: drop 5 inventory projectors; add §9 subject extractor + host promotion + import-sweep ingestion + status projection + outcome derivation + blocker auto-resolve | rewrite |
| `validators.py` | drive read-only probes off promoted atomics; build the one-shot import-sweep command | edit |
| `validation.py` | 3-edge rules + field-level ownership + 3 new reject rules; close update hole | rewrite |
| `patch.py` | new semantic `GraphPatch` keys + parser | rewrite |
| `apply.py` | apply new patch shape (field-level classification/description updates) | edit |
| `transitions.py` → `attempts.py` | rename; `addresses` edges; host outcome derivation | rewrite |
| `render.py` | three-section planner render + maintainer serializer for new nodes | rewrite |
| `world_model.py` | add `dependency_state`; `open_problems` → derived view; auto-resolvers operate on Blockers; `notes` → `diagnostic_notes` | edit |
| `planner.py` | `RecipePatch` parser + three-section prompt + action vocab | edit |
| `build_agent.py` | seed `run` with the whole ordered recipe (not one `done_when`); execute in order with local repair, stop-and-report on unrepairable failure; budget `LOCAL_BUDGET_BASE + k·num_steps`; keep stuck-guard; prompt: "execute the recipe, repair locally, do not redesign" (§9.4) | edit |
| `maintainer.py` | new patch keys + prompt rewrite; stop map writes | edit |
| `orchestrator.py` | per cycle: commit Attempts (`addresses`) → run whole recipe via BuildAgent → derive per-step `Attempt.outcome` from re-projected target status; refresh order (Planner→BuildAgent→Maintainer-last) | edit |

Also: grep `src/` for hardcoded id literals (e.g. `planner.py:166`
`"contract:goal:repo_tests_run"`) before the rename so the cut misses no ripple
site. Note the top goal renames `repo_tests_run` → `repo_tests_pass`.

---

## 11. Testing (pytest, TDD, ≥80%)

- **Unit (table-driven):** schema ownership; ids grammar; `project_status` truth
  table; outcome-derivation truth table; blocker auto-resolve; backbone seeding;
  import-sweep command builder; §9 promotion regex; validator reject rules;
  RecipePatch parser.
- **Integration:** `refresh_host_graph` end-to-end on fixtures (cv2/libGL,
  psycopg2/pg_config, redis); maintainer patch apply+validate; orchestrator one
  cycle with a stub LLM.
- **Recipe execution (§9.4):** BuildAgent runs a multi-step recipe in order;
  stop-and-report on an unrepairable step (later steps don't run, earlier
  satisfied targets are still recorded); budget scales with step count; outcomes
  derive from re-projected target status, including a `ok_but_still_blocked` case
  (commands ran, target still violated).
- **Regression guard:** the coverage scenario — assert the import sweep surfaces
  a missing dep that lazy formation alone would miss.

---

## 12. Non-goals

- A full dependency / provenance graph (that stays in WorldModelMap + ledger).
- Relocating `done_flag` / `progress` computation (orthogonal; the success gate
  is sensitive).
- A backward-compatible on-disk graph format (the arm is off by default).
- Changing the default `v1` / `arm0` paths.

---

## 13. Success criteria

1. The planner targets contract IDs via multi-step RecipePatches, not vague
   single failures.
2. The graph stays small on large repos (no per-dep nodes).
3. Runtime failures attach to the right obligation under the goal backbone.
4. Repeated failed attempts are visible (`Attempt.outcome`) and avoided.
5. WorldModelMap remains authoritative; status/outcome/active are always
   projected, never stale.
6. The host `done_flag` remains the sole hard success gate.
7. No coverage regression vs the current eager probing — the import sweep keeps
   the missing-dep signal complete.
