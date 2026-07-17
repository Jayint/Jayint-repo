# Dockerfile Extraction from a Dirty Container — Findings & Contract-Graph Roadmap

> **Status:** reference / analysis + roadmap (not yet an implementation plan)
> **Date:** 2026-06-18
> **Scope:** how we currently turn a "dirty" setup container into a reusable Dockerfile,
> why it fails, what an external paper does differently, and how the EnvState contract
> graph can help — with a ranked roadmap from a 11-agent ideation workflow.

---

## TL;DR

- The current Dockerfile extractor (both the repo2run `src/synthesizer.py` and the lean v1
  `src/envstate/synthesis.py`) is **trajectory replay**: it reconstructs a Dockerfile by
  replaying a regex-filtered subset of the recorded successful-command log. **It never
  snapshots the container.**
- This is the **dominant failure mode** in our benchmarks (e.g. one run's "56% success"
  was build-success; real test-pass ~16% — "hollow" Dockerfiles; a RUN-concat bug crashed 7 repos).
- The failure splits into 6 classes (P1–P6 below). P1–P3 are *structural* to replay; P4–P6 are fixable.
- **Governing principle the analysis converged on:**
  **Ledger SOURCES** (what commands ran) · **host Snapshot PINS** (`pip freeze` = what's installed) ·
  **Graph ORDERS / ANNOTATES / FLAGS** (layer order, non-bakeable services, which contract regressed) ·
  **Clean rebuild CERTIFIES** (the only ground truth). **The graph must never SUBTRACT.**
- **Recommended first step:** wire the already-written `cleanroom.py::verify_cleanroom` into
  `run_v1`'s done branch and report a host-only `clean_certified` metric instead of the graph's
  optimistic `goal_ready`. Smallest change that turns the dominant hollow-Dockerfile failure from
  *silent* into *caught*, and becomes the measurement keystone for everything else.

---

## 1. The problem

Goal: produce a **reusable Dockerfile** that rebuilds a working env *from a clean base* after an
agent has set the env up live inside a container. The hard part is that a command sequence that was
*locally correct* (each command worked against the accumulated/dirty container state) does **not**
guarantee a *globally correct* script (the ordered sequence rebuilds the env from scratch).

## 2. How extraction works today (two synthesizers, same strategy)

### 2a. ReAct agent → trajectory → synthesizer (two stages, not one)
- The **ReAct agent** (original DockerAgent monolithic loop, `agent.py:~1432` `for step in range(max_steps)`)
  is the *doer*: live `Thought → Action → Observation` inside the dirty container — installing,
  failing, retrying, probing. Its output is the **recorded ledger** (`agent_run_summary.json`:
  `successful_actions` / `failed_actions`).
- **Trajectory replay** is a *separate, post-hoc* step done by the **synthesizer** — it reads that
  ledger and reconstructs a Dockerfile. The replay is **not** a verbatim re-run; it's a *distilled
  successful subset* of the messy live session.

### 2b. repo2run / arm0 synthesizer — `src/synthesizer.py` (~4,010 lines)
Live call chain: `run_repo2run_benchmark.py:3502` `resynthesize_dockerfile_from_existing_workplace(...)`
→ `src/workplace_replay.py:166` `synthesize_build_recipe(...)` → `:174` `generate_dockerfile(...)`.

Pipeline:
1. **Input** (`src/workplace_replay.py:152-164`): `recipe_input` = recorded ledger (`successful_actions`/
   `failed_actions`, each with `step_index` + observation) + LLM-summarized setup-log + verified test bundle.
   **No container handle. Verified: zero `docker commit` / `pip freeze` / `exec` / `get_archive`.**
2. **LLM recipe** (`synthesize_build_recipe`, `:497`): whole `recipe_input` → prompt → JSON with keys
   `{build_commands, post_test_patch_commands, runtime_preparation_commands, test_commands,
   excluded_commands, rationale, confidence}`.
3. **Trajectory-first override** (`normalize_build_recipe`, `:708`): if any usable successful trajectory
   exists, the LLM's `build_commands` are **discarded** and the observed successful setup commands are
   replayed in order (`_collect_trajectory_first_build_commands`, `:811`).
4. **Drop / supplement / sanitize**: drop read-only / "failed-only" (pip package-subset match) / LLM-excluded;
   supplement (fallback branch) observed file rewrites / version pins / backend bootstrap / tox deps;
   sanitize for RUN (strip `|tail`/`2>/dev/null`, `echo -e`→`printf`, base64-script multiline, apt/pip retry wrappers).
5. **Render** (`generate_dockerfile`, `:3986`): `FROM` + `WORKDIR` + pip/apt bootstrap + `RUN` lines.
   Runner post-processes: `render_eval_dockerfile` (`run_repo2run_benchmark.py:920`) inserts `COPY . <workdir>`.
6. **Fresh-build verify + repair** (`run_repo2run_benchmark.py:~3539-3700`): build from a **clean git checkout**,
   `docker run --rm` each test cmd; on failure → deterministic missing-module repair, then an **LLM Dockerfile
   repair agent** (`DOCKERFILE_REPAIR_SYSTEM_PROMPT`, `:55`), default **2 rounds**. `should_use_agent_dockerfile`
   (`:285`) gates whether the agent Dockerfile is even tried.

### 2c. v1 EnvState synthesizer — `src/envstate/synthesis.py` + cleanroom
- `build_commands_from_ledger(ledger)` (`synthesis.py:7`) — "authoritative, order-preserving build-command
  extraction" → one RUN per ledger event. Leaner; same family (replay the ledger, don't snapshot).
- `agent.py:1346` `generate_dockerfile(...)` then `_verify_cleanroom_or_fail` (`:1356`) →
  `cleanroom.py:verify_cleanroom` (`:38`): rebuild fresh image + re-run probes/tests.
- **The clean rebuild is OFF by default in v1**: `enable_cleanroom=False` (`agent.py:235,278`); v1 path
  explicitly skips it — *"the v1 path skips cleanroom… EBSR is the trusted success metric"* (`agent.py:962-969`).
  So v1 today certifies success on the **dirty-container** test pass and never verifies the Dockerfile reproduces.
- **Important:** the contract graph is currently **purely planner-advisory** — `render.py` only emits planner
  text; the real Dockerfile comes from `build_commands_from_ledger`, which **never touches the graph or
  `host_satisfied`**. (Verified in the skeptic lens.)

## 3. The six problem classes

| ID | Problem | Fixable? |
|----|---------|----------|
| **P1** | **Distillation loss** — filters drop load-bearing commands (successful install whose output contains `error:`/`Traceback` dropped as failed; `pip install -e .`/retries dropped by package-subset matching; read-only misclassification; LLM over-exclusion; cross-command `export VAR` lost across independent RUN layers) → **hollow Dockerfiles**. | structural-ish |
| **P2** | **Dirty-base ≠ clean-base** — even a faithful replay fails: messy path (install A → uninstall A → install A′), transitive deps resolve differently on clean base, **unpinned versions drift** at rebuild. | structural |
| **P3** | **Persistent → layered semantics** — runtime/process state can't be baked: `service_start` (`redis-server &`) lives in the container but dies in a RUN layer; uncaptured ENV/WORKDIR. | structural |
| **P4** | **Garbage-in** — replay inherits a weak success label (e.g. `pytest --collect-only` rc=0 false-pass) → replays a trajectory that never ran the tests. | fixable |
| **P5** | **LLM + heuristic brittleness** — recipe LLM + ~4,000-line regex stack + 2-round repair LLM; over-exclusion, RUN-concat crash, nondeterminism. | fixable |
| **P6** | **Verification bolted-on / off** — the clean-rebuild verify is the only real correctness check and is OFF by default in v1; repair is 2 rounds then ship-broken. | fixable |

## 4. Empirical evidence (prior project benchmark analyses)

- **RAT-hard:** headline "56% success" was *build*-success; real test-pass ~**16%** — synthesizer **drops
  editable/test installs → "hollow" Dockerfiles** (build green, env not set up).
- **runner4:** genuine deficit (after removing credit-wall noise) = **lossy synthesizer** (drops/hallucinates
  installs) + collect-only false-pass.
- **Jayint graph-impact:** the **synthesizer's RUN-concat bug was the dominant failure — 7 repos + a harness crash**, not the graph.

## 5. External reference — HerAgent (arXiv 2602.07871)

- **Title:** "HerAgent: Rethinking the Automated Environment Deployment via Hierarchical Test Pyramid."
  Authors Xiang Li, Siyu Lu, Federica Sarro, Claire Le Goues, He Ye (UCL / Uppsala / CMU), Feb 2026.
- **Code repo:** `https://github.com/EuniAI/EnvAgent` (the user referenced `EuniAI/HerAgent`; "HerAgent"
  is the system name in the paper).
- **Method:** LangGraph multi-agent, GPT-5. Three stages: Testsuite Extraction → Environment
  Implementation (generate a single persistent `prometheus_setup.sh` from a Tree-sitter/Neo4j code
  knowledge graph) → Environment Repair. **Hybrid whole-script + single-command repair**: error → propose
  ONE command → **merge into the whole Bash File** → re-run the whole script. The script is "the sole
  persistent carrier of environment state." Output is a Bash File **+ a Dockerfile** (conversion not described).
- **Maturity hierarchy:** Installability ⊊ Testability ⊊ Runnability (run the entry point), with per-level
  success oracle. **Repo2Run Testability→Runnability drops 110→55** (passing tests overstates readiness).
- **Ablation (Table 3, the crux):** whole-script vs single-command are **complementary, not better/worse** —
  single-command-only: Runnability collapses 26→**19** ("indispensable for global dependency conflicts");
  whole-script-only: Testability drops 32→27. Their *entire stated motivation* for script-centric repair is
  to **avoid context loss of ephemeral non-interactive shells**.
- **Takeaway for us:** their core motivation (durable state ledger) we already solve via the **persistent
  container + host-certified world model**. Do **not** switch the planner to whole-script editing (it would
  break per-step contract grounding). DO port: (1) a **Runnability/clean-rebuild oracle**; (2) emit the
  **certified ledger → Dockerfile** (our ledger is a *better* draft than theirs — host-certified per step);
  (3) optionally a whole-script "clean rebuild" escape hatch for the narrow global-conflict case.

## 6. Governing principle (converged across all 5 ideation lenses + critiques)

> **Ledger SOURCES** (what commands ran) · **host Snapshot PINS** (`pip freeze` = what's installed) ·
> **Graph ORDERS / ANNOTATES / FLAGS** (layer order via `depends_on`, non-bakeable services, which contract
> regressed) · **Clean rebuild CERTIFIES** (the only ground truth). **The graph must NEVER SUBTRACT** — its
> `Attempt→addresses` coverage is a biased subset of the ledger (only the ~5 diagnosable failure signatures
> in `extract._RULES`) and is blind to implicit state (`export`, `cd`, `mkdir`, conftest writes, weight
> downloads), so using it to filter the recipe *over-prunes load-bearing commands*.

This **reverses** the tempting "use the graph to build a smarter Dockerfile" framing — the graph's value is
*ordering / flagging / localizing*, not sourcing or filtering.

## 7. Roadmap — ranked proposals (11-agent ideation workflow, run `wf_94a71e64-880`)

Ranked by leverage-per-effort against the known failures.

| # | Proposal | Effort | Lev | Attacks |
|---|----------|--------|-----|---------|
| 1 | Snapshot-pin the deps layer from host pip-freeze Facts | M | High | P1,P2 |
| 2 | Clean rebuild = authoritative shipping gate (+ `clean_certified` metric, graph-derived probes) | L | High | P6,P4,P2,P1 |
| 3 | Raw one-RUN-per-event emitter w/ contract-id annotations (kill RUN-concat crash) | S | High | P5,P1,P3 |
| 4 | Fold clean-room regressions into the graph as Blockers → graph-native repair | L | High | P5,P6,P2 |
| 5 | Differential dirty-vs-clean `host_satisfied` diff → precise P2 drift Blockers + deterministic re-pin | M | Med | P2,P4 |
| 6 | Graph-ordered, **non-subtractive** command selection w/ executed-command provenance | M | Med | P1,P5 |
| 7 | Goal-status honest finalize gate (emit only on certified-OR-verified execution) | S | Med | P4,P1,P6 |
| 8 | `export`→ENV + service lifecycle (cleanroom preamble / conservative ENTRYPOINT) | S | Med | P3,P1 |

### Details

**P1 — Snapshot-pin the deps layer.** *Mechanism:* graph is supervisory only — `host_satisfied` says which
layers are real; system-layer Contracts (`contract:system_library:*`, `contract:binary:*` from
`world_map.system_installed`) are the ONLY explicit apt/pkg-config RUN lines (freeze can't see them). Deps
layer is the host `pip freeze` snapshot pinned as constraints, NOT an Attempt/ledger replay. Graph never
subtracts. *Changes:* `extractor.py:23` (`pip list --format=freeze`) + `:34` (`pip inspect`) already capture
into `WorldModelMap.installed`/`DependencyState.resolved` — no new probe. Add `emit_constraints(installed)` +
pin renderer in `src/envstate/synthesis.py`: write `constraints.txt` into build context, emit
`COPY constraints.txt` + `RUN pip install -r requirements.txt -c constraints.txt`. Keep ledger's
`pip install -e .` and `@ git+`/VCS installs verbatim. Drop repo's own dist + `-e`/`@ file://`/`@ git+` from
constraints; scrub credentials. Render apt RUN from `system_installed`. *Risks:* editable installs / locally
built C-ext wheels serialize local host paths (keep editable as a ledger command, never a constraint);
arch-pinned wheels may fail on a differently-tagged base; gate behind P2 clean rebuild.

**P2 — Clean rebuild = authoritative gate (KEYSTONE).** *Mechanism:* probe compiler over `ContractGraph`:
each satisfied Contract → in-image assertion keyed on `data['kind']/data['check']` (goal contracts use
`data['check']` verbatim: `repo_tests_pass`→`python -m pytest -q`, `repo_tests_collect`→`--collect-only`;
foundationals→`--version`; atomic `python_import`→`python -c import X`, DEFERRED until import sweep wired).
Group probes by `data['layer']` (base<system<runtime<deps<build<tests) → first failing assertion localizes
which contract regressed at which layer. Optionally seed `contract:goal:repo_reproducible (required=True)`
above `GOAL_TESTS_PASS` in `goals._BACKBONE`. *Changes:* `cleanroom.py` `verify_cleanroom` currently discards
`_out` (`:77,89`) — retain per-failure stdout; add `failed_contracts`/`last_good_layer` to `CleanroomResult`.
New `src/envstate/contracts/probe_compiler.py`. Wire a cleanroom step into `orchestrator.run_v1`'s done branch
(today `run_v1` has no docker client — inject a host-side build/verify capability). Wrap probes in
`/bin/sh -lc <repr>` (`containers.run` has no shell). Add `WorldModelMap.clean_certified` bool
(`world_model.py:192+`); finalize/report reads `clean_certified`, **never** graph `goal_ready`. Default ON for
artifact runs. Pin to a real-execution gate (`maintainer._shows_execution`/`_shows_pytest_completion`), never
the P4-tainted `verified_test_commands` string. Memoize build by ledger-revision; hard rebuild cap 2–3.
*Risks:* largest change + real cost/latency; `pytest -q` in a fresh image can flake (false-negative denial of a
genuine win — the documented v1 success-capture gap). Mitigate with **deterministic-only denial**: deny on
import/collection error or atomic-contract regression; keep test-assertion flakiness advisory only when
collection + all atomics re-satisfy AND the same tests passed in-sandbox.

**P3 — Raw one-RUN-per-event emitter (QUICK WIN).** *Mechanism:* one RUN per rc==0 mutating ledger event, no
heuristic command-joining; annotate `# satisfies <contract_id> [<layer>]` by matching event→Attempt
(`Attempt.data['commands']`) → `addresses`-edge → Contract (best-effort, never load-bearing for keep/drop, so
it can't empty the recipe). Detect backgrounded services syntactically (trailing `&` + daemon allowlist) →
ENTRYPOINT/CMD or a `repo_services_ready` Blocker, never a RUN. *Changes:* `synthesis.py`
`build_commands_from_ledger` already emits one event/line; ensure the v1 finalize path **bypasses the big
synthesizer's ~4,000-line regex distill/concat** (where the crash lives) and renders one `event.cmd` per RUN
raw. Add the annotation pass. base64-heredoc branch only for genuinely multiline commands. *Risks:*
annotations degrade gracefully when absent; service allowlist conservative.

**P4 — Fold clean-room regressions into the graph as Blockers.** *Mechanism:* new
`projection.fold_cleanroom_result(graph, result) -> GraphPatch`: run `extract.promote_atomic_contracts` on each
retained failure stdout to mint the atomic Contract, mint a Blocker (`ids.blocker_id`) + `violates` edge,
`root_or_downstream` by `depends_on` depth; no `_RULES` match → generic `blocker:clean_regression` carrying
failing command + stderr tail (so version-drift surfaces). `run_v1` applies the patch and loops;
`find_next_target_contracts` surfaces the root; planner proposes a fix; a fresh Attempt addresses it;
auto-retire via `projection._auto_resolve_blockers`. *Changes:* `CleanroomResult` retains per-failure stdout;
new `fold_cleanroom_result` + call in `run_v1`. Add build-time patterns to `extract._RULES`
(ResolutionImpossible/version_conflict, "Could not build wheels"/build_failure — BlockerKinds exist with no
producing rule). Resolve host-creates-Blocker ownership (route via Maintainer or extend
`HOST_CREATABLE_NODE_TYPES` + assert in `validate_patch`). **Repair against a persistent clean-base scratch
container, not the dirty sandbox.** Deprecate `artifact_verify.py`/`recipe_repair.py`/`repo2run_repair_port`
for the v1 path. Cap rebuilds 1–2; honest unfixable-Blocker give-up. *Risks:* repairing against the DIRTY base
is a no-op for transitive-drift P2; oscillation/infinite-rebuild → hard caps; unifies rather than removes the LLM repair.

**P5 — Differential dirty-vs-clean `host_satisfied` diff.** *Mechanism:* `host_satisfied_live` MINUS
`host_satisfied_clean` (re-run `validators.build_import_sweep_command`/`parse_import_sweep` + each goal
`data['check']` in the clean image) = exactly the Contracts that hold dirty but regress clean. Mint Blockers
tagged root/downstream. *Changes:* new `projection.diff_host_satisfied(live, clean, graph)`; `verify_cleanroom`
returns the clean `host_satisfied` set; capture dirty `pip freeze` with the diff for exact re-pin;
pre-classify goal checks needing live services → route to `repo_services_ready`. Gate behind a successful clean
build (composes with P4). *Risks:* best pure P2 detector; deterministic re-pin is what stops it merely
relocating P2 to the LLM.

**P6 — Graph-ordered non-subtractive selection.** *Mechanism:* keep `build_commands_from_ledger` as the
authoritative source; graph only (a) stable-sorts events into canonical layer order via `addresses`-edge
`Contract.data['layer']` and (b) drops only events whose owning Attempt `outcome=='failed'` or is superseded
(last-ok-per-Contract). Events with no owning Contract kept RAW. *Changes:* graph-aware ordering/filter over
LEDGER events (not Attempts, whose `data['commands']` is the *proposed* string). Stamp `current_step_idx` onto
each `ActionEvent` in `build_agent.run_recipe` at execution time (deterministic event→Attempt). Adversarial
invariant test: an unlabeled `export VAR`/`mkdir` + a legacy task-path install must all survive.

**P7 — Honest finalize gate.** *Mechanism:* gate emission on
`project_status(graph, contract:goal:repo_tests_pass, host_satisfied)=='satisfied'` OR existing
`_verified_test_command_id` evidence; demote outcome-prune to advisory; never prune on `unknown`. *Changes:*
OR-gate at `run_v1` finalize; reuse `projection.py:169-175` certification (`--collect-only` excluded).
**Mandatory escape hatch:** emit when a genuine execution exists even if projection is sparse (avoid the
documented self-inflicted coverage losses). *Risks:* collect-only already mostly closed (commit `ce77880`), so
residual leverage is limited.

**P8 — `export`→ENV + service lifecycle.** *Mechanism:* graph FLAGS non-bakeability (persistence tag in
`goals.py` seed: `repo_services_ready`:='runtime', filesystem goals:='durable'); emission driven off LEDGER
evidence: `export VAR`/`cd`→ENV/WORKDIR; allowlisted daemons→ENTRYPOINT. *Changes:* `synthesis.py`
`render_env_layer`; `cleanroom.py` service-start preamble (start daemons + readiness wait) before re-running
test_commands. Keep ENTRYPOINT conservative (allowlist only). *Risks:* service-vs-setup classification brittle;
over-eager ENTRYPOINT breaks repos whose own fixtures start the service (port clash).

### Quick wins (S-effort, high-leverage)
- **P3** — raw one-RUN-per-event emitter: bypass the regex concat for the v1 path (kills the 7-repo crash).
- **P2 (the `clean_certified` half)** — wire `verify_cleanroom` into `run_v1` and report `clean_certified`
  instead of `goal_ready` (stops laundering hollow Dockerfiles into "success").
- **P1 (the pin core)** — write `constraints.txt` from the already-captured pip-freeze Facts + `-c`.
- **P8 `export`→ENV** — recover cross-RUN shell state that currently evaporates.
- **P7 finalize gate** — withhold emission unless certified OR verified real execution (with escape hatch).

### Recommended first step
> Wire `src/envstate/cleanroom.py::verify_cleanroom` into `orchestrator.run_v1`'s done branch and make a
> host-only `WorldModelMap.clean_certified` the reported success metric (instead of graph `goal_ready`),
> feeding it minimal probes from the goal checks (`repo_tests_pass`→`python -m pytest -q`,
> `repo_tests_collect`→`--collect-only`) wrapped in `/bin/sh -lc`. Smallest change that turns the dominant
> hollow-Dockerfile failure from *silent* into *caught*, exposes the true hollow rate, and is the measurement
> keystone every other proposal is scored against — pairs immediately with P1's freeze-pin.

### Fundamental limits (where the graph genuinely cannot help)
1. **Deps/runtime end-state** is better read by host snapshot (`pip freeze`/`pip inspect`, already captured)
   than reconstructed from graph/replay — graph coverage is biased to `extract._RULES` and blind to implicit
   state. Graph ORDERS/ANNOTATES/FLAGS, snapshot PINS, ledger SOURCES — **graph never SUBTRACTS**.
2. The graph is built entirely from the **dirty** container, so it **structurally cannot see P2** (dirty≠clean).
   Only a clean rebuild is ground truth; `clean_certified` (set only by `verify_cleanroom`) must be reported
   truth, not `goal_ready`.
3. **Running services** are non-bakeable by replay OR docker-commit — honest answer is explicit ENTRYPOINT
   lifecycle; graph's job is to FLAG, not assert satisfied.
4. **Editable / C-ext / VCS installs** aren't reproducible from freeze alone — freeze pins versions, the *what*
   must come from the ledger + the project's own install step.
5. The collect-only `host_satisfied` tier-split is premature: no synthesis/cleanroom code reads
   `host_satisfied` today; the honest execution predicate already exists in `projection`
   (`_verified_test_command_id`, `--collect-only` excluded).

## 8. Key code touch-points (reference)

| Concern | Location |
|---------|----------|
| repo2run synthesizer (trajectory replay) | `src/synthesizer.py` (synthesize_build_recipe `:497`, normalize `:708`, trajectory collect `:811`, render `:3986`) |
| repo2run runner: build/verify/repair | `run_repo2run_benchmark.py` (Synthesizer `:39`, resynthesize call `:3502`, repair prompt `:55`, render_eval `:920`, should_use_agent `:285`) |
| recipe_input builder | `src/workplace_replay.py:99-166` |
| v1 synthesizer | `src/envstate/synthesis.py:7` `build_commands_from_ledger` |
| clean rebuild | `src/envstate/cleanroom.py` (`verify_cleanroom :38`, `_out` discarded `:77,89`, `ensure_repo_in_dockerfile :18`) |
| cleanroom wiring (off in v1) | `agent.py` (`_verify_cleanroom_or_fail :1356`, default off `:235,278`, v1 skip `:962-969`, generate_dockerfile `:1346`) |
| host facts / freeze | `src/envstate/extractor.py:23` (pip freeze), `:34` (pip inspect) |
| world model | `src/envstate/world_model.py` (`installed`, `system_installed`, `host_satisfied`, `contract_graph`; `clean_certified` → `:192+`) |
| contract graph | `src/envstate/contracts/{graph,projection,render,ids,extract,goals}.py` (graph advisory-only today) |
| planner recipe emission | `src/envstate/planner.py:373-390` (`apply_recipe_patch`), objective `:61` |
| build agent recipe loop | `src/envstate/build_agent.py` `run_recipe` |

## 9. References & artifacts

- **Paper:** HerAgent — arXiv **2602.07871** ("Rethinking the Automated Environment Deployment via
  Hierarchical Test Pyramid"). Code: `https://github.com/EuniAI/EnvAgent` (ref'd as `EuniAI/HerAgent`).
- **Ideation workflow:** run `wf_94a71e64-880` (task `wzqsayveo`), 11 agents, 5 lenses
  (recipe-from-graph, contracts-as-assertions, reproducibility-pinning, blocker-driven-repair, skeptic) →
  adversarial critique → ranked synthesis. Script saved under the session `workflows/scripts/` dir.
- **Related design docs:** `docs/DESIGN-concise-contract-graph.md`, `docs/DESIGN-contract-graph-v1.md`,
  `docs/DESIGN-environment-state-maintainer.md`.
- **Prior empirical findings** (project memory): RAT-hard hollow-success (56%→16%), runner4 lossy synthesizer,
  Jayint RUN-concat crash (7 repos).

## 10. Open decisions before building
- Whether to make the clean rebuild **default-ON** for v1 artifact runs (cost/latency vs catching hollows).
- The host-creates-Blocker ownership model (Maintainer-routed vs `HOST_CREATABLE_NODE_TYPES`).
- Whether to keep the repo2run heavy synthesizer at all for the v1 path, or replace it wholesale with
  ledger-raw (P3) + freeze-pin (P1) + cleanroom (P2).
