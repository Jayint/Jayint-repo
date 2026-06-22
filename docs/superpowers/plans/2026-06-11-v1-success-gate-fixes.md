# Plan: Fix the v1 success gate (port arm0's verification rigor)

Branch: `john-planner-v1` · Date: 2026-06-11 · Model under test: deepseek/deepseek-v4-flash

## Why

Same-16-repo, same-model head-to-head (`outputs/arm0_vs_v1_t20.json`): the original
DockerAgent (arm0) **beats** the v1 three-role rewrite — success 12/16 vs 9/16,
ESSR÷all **0.41 vs 0.20**, 3,102 vs 1,418 tests executed. Adversarially-verified
root cause (`outputs/v1_reasoning_rootcause.md`, 3 confirmed / 4 refined / 0 refuted):

- **v1's done-gate is a single sticky check** — `maintainer.py:256/286`
  `done = done_flag or _collect_only_passed(report)`, i.e. *any* `pytest --collect-only`
  with `rc==0` finalizes the run forever. Collect-only only **imports** test modules; it
  never enters setup/fixtures or runs a test, so it masks every execution-time failure.
- arm0 instead gates on a **Verification Bundle** (`radical` branch,
  `src/verification_bundle.py` + `agent.py`): the agent's claimed test command must be an
  *observed, non-failing, non-empty, non-truncated, non-fabricated* test run. That stack of
  guards — not a laxer gate — is why arm0 wins.

**Goal:** port arm0's verification rigor into v1. Scope = **core v1 reasoning / gate only**;
the Dockerfile synthesis/replay gap is explicitly **out of scope** for this plan.

**Key enabler:** `src/synthesizer.py` already ships in v1 with arm0's byte-identical
classifiers — `analyze_test_run`, `observation_has_test_failure_signal`,
`observation_has_empty_test_run_signal`, `is_test_command`. We **reuse** them, not reimplement.

## What each fix solves (per repo)

| Repo | Today | Defect | Fixed by |
|------|-------|--------|----------|
| Yelp/dumb-init | success, 0% (182 fail) | collect-only passed; C binary never `make`-built | P1 (gate→execution) + P3 (build signal) |
| rq/rq | success, 0% (345 ConnErr) | collect-only hid missing Redis service | P1 + P3 (runtime-service) |
| epam/ai-dial-sdk | success, 0 collected | deps in poetry venv; gated via `poetry run` | P1 + **P2 (wrapper-free)** |
| Nitrokey/pynitrokey | success, 0% | same venv isolation | P2 |
| conor/n8n-autoscaling | success, fabricated test | Planner created `test_zero.py`; `--ignore` hid real test | P4 (anti-fabrication) + P1 (empty-run) |
| microsoft/markitdown | build_failed, no converge | Maintainer mislabeled 1 topology issue as 5 `[deps]`; no prune | P5 (dedup/reclassify) |
| FoundationAgents/OpenManus | build_failed, no converge | proxy `done_when`s + BuildAgent preflight thrash (17/56) | P4 (done_when) + P5 (self-check) |

Net target: flip the 4 false-successes (dumb-init, ai-dial, n8n, rq) to either **genuine pass**
(once P3 provisions the service/build) or **honest fail**, and recover the 2 non-converges.

## Design principle & the coupling constraint

The gate, the Planner's fixed objective, and the finalize path all currently encode
**"make `pytest --collect-only` exit 0"** (`planner.py:58-60`, `maintainer.py:150`,
`agent.py:_resolve_v1_verified_collect_only`). **Tightening the gate alone would make nothing
finalize** — the BuildAgent would keep running collect-only, which would no longer satisfy the
gate. Therefore **P1 changes all three together**.

The new gate fires when the report contains a command that is:
1. a test command (`synth.is_test_command`), `rc == 0`;
2. **wrapper-free** — not under `poetry run` / `pipenv run` / `hatch run` / `conda run`/activate (P2);
3. `synth.analyze_test_run(cmd, output)["is_effective_test_run"]` is True — reuses arm0's
   guards (rejects help text, truncated/piped output, **any failure signal**, empty runs);
4. **actually executed** — output shows an execution summary (`\d+ passed`, `ran \d+ tests`),
   not merely `collected N items` (closes the collect-masks-runtime hole: dumb-init/rq);
5. not gaming collection — no `--ignore` of pre-existing tests, collected count ≥ 1 (P4).

Calibration knob (one decision): step 4 requires ≥1 executed PASS with 0 failures. The looser
alternative (accept a clean run that collected ≥1 but executed 0) is rejected by default — it
is exactly the hole we are closing. Documented here so the strictness is an explicit choice.

## Phased implementation (TDD: red → green → refactor; 80%+ coverage)

### Phase 1 — Execution-aware done-gate + Planner objective + finalize path  (CRITICAL)
**Files:** `src/envstate/maintainer.py`, `agent.py`, `src/envstate/planner.py`,
`src/envstate/orchestrator.py` (COLLECT_ONLY_CMD constant).

1. **maintainer.py**: add `_verified_test_run_passed(report, synth) -> bool` per the gate
   spec above (reusing a module-level `Synthesizer()` detector). Replace both call sites
   (`:256`, `:286`) `_collect_only_passed(report)` → `_verified_test_run_passed(...)`.
   Keep `_collect_only_passed`/`_is_collect_only_cmd` only if still needed by P4's provenance
   check; otherwise remove. Update `_progress_synced_with_done` unchanged (still keys off `done`).
2. **agent.py** `_resolve_v1_verified_collect_only`: rename/retarget to
   `_resolve_v1_verified_test_run`; when the gate wasn't reached in-loop, **actively run the
   real test command** (e.g. `python -m pytest -q`) in the live container and accept only if
   the same `_verified_test_run_passed`-style check holds. Record the real evidence in the
   ActionLedger (as today). Never fabricate.
3. **planner.py:58-60**: change the fixed objective from "make `pytest --collect-only` exit 0"
   to: *"run the project's test suite with a bare interpreter (`python -m pytest -q`) and reach
   ≥1 passed with no collection/setup errors."* (Drops the `poetry run` line → P2.)
4. **orchestrator.py**: generalize the success/verify constant/wiring from collect-only to the
   verified-test-run concept (keep a back-compat alias if other modules import it).

**Tests (red first):** extend `tests/test_v1_finalize_gate.py` + `tests/test_v1_maintainer.py`:
- `dumb-init`: report with `pytest` output `182 failed` → **not** done.
- `rq`: output with `ConnectionError ... 345 errors` → **not** done.
- pure collect-only `collected 182 items` (no pass) → **not** done.
- genuine `... 182 passed in 4.0s` (wrapper-free) → done.
- finalize path: gate not reached, active real run passes → success; fails → `None`, nothing recorded.

**Flips:** dumb-init/rq/ai-dial from false-success to honest-fail (then P2/P3 make them pass).
**Risk:** HIGH — changes all run behaviour; pass-rate may dip before P3 lands. Mitigation:
land P1–P4 before re-judging; honest-fail is the correct interim state.

### Phase 2 — Wrapper-free / grader-interpreter verification  (CRITICAL)
**Files:** `src/envstate/maintainer.py` (gate helper `_is_venv_wrapped(cmd)`),
`src/envstate/planner.py` (prompt).

- Gate: a verifying test command run under `poetry run`/`pipenv run`/`hatch run`/`conda run`
  or a sourced venv does **not** count — the grader uses bare system `python -m pytest`.
- Planner prompt: for poetry/pipenv/hatch projects, emit a precursor step to make deps
  importable system-wide (`poetry config virtualenvs.create false` / install into system
  python) **before** verifying, and verify with bare `python -m pytest`.

**Tests:** ai-dial `poetry run pytest ... 8 passed` → **not** done; bare `python -m pytest ...
8 passed` → done. pynitrokey analogous.
**Flips:** ai-dial-sdk, pynitrokey. **Risk:** MED.

### Phase 3 — `build` not signal-less + runtime-service modeling  (HIGH)
**Files:** `src/envstate/world_model.py` (`_derive_progress`), `src/envstate/planner.py` (prompt).

- `world_model.py:189`: stop deriving `build=True` purely from "no build open_problem" when
  `repo_layout` contains a Makefile/CMakeLists/configure or C/C++/Go/Rust sources. Add
  `_build_required(repo_layout) -> bool`; when True, `build` requires a positive build signal
  (an observed successful compile command / artifact probe), else `build=False`.
- Planner prompt: runtime-service heuristic — when a known service client
  (redis/psycopg2/pymongo/mysqlclient/celery/kombu…) is in `required`, hypothesize a live
  service is needed and emit a runtime task (start it / probe it) rather than treating
  `import X` as runtime-satisfied.

**Tests:** `test_world_model_progress.py` — dumb-init-like layout (Makefile + `.c`) → build
False until a `make` success is observed. planner — redis in required → runtime task emitted.
**Flips/enables:** dumb-init (forces `make`), rq (start redis → tests pass). **Risk:** MED.

### Phase 4 — Planner `done_when` discipline + anti-fabrication / anti-`--ignore`  (HIGH)
**Files:** `src/envstate/planner.py` (prompt + parse guard), `src/envstate/maintainer.py`
(gate provenance check).

- Planner prompt: `done_when` must be the real test-execution command (ban weaker proxies
  like `pip show`/`pip list`/`pip install exit 0`); **ban creating test files** to satisfy the
  gate; if there are no genuine pre-existing test files (or the only test-named files fail to
  import), emit `giveup` with `reason: no_real_test_suite` — never fabricate.
- Gate provenance: reject a verifying command that `--ignore`s a pre-existing test to reach
  exit 0, or whose collected/executed count is 0.

**Tests:** n8n — fabricated `test_zero.py` only → gate rejects (no pre-existing test);
`--ignore=examples` on the verifying cmd → rejected. OpenManus — `done_when: pip show X` task
is normalized/penalized toward the acceptance command.
**Flips:** n8n (honest no_real_test_suite); tightens OpenManus. **Risk:** MED.

### Phase 5 — BuildAgent self-check + Maintainer dedup/reclassify  (MED)
**Files:** `src/envstate/build_agent.py` (prompt + pre-submit self-check),
`src/envstate/maintainer.py` (prompt + a dedup/prune pass).

- build_agent: hard-encode the two sandbox composition rules (one mutation per Action; never
  pipe setup output through grep/head/tail — redirect to a file then read separately) and a
  pre-submission self-check that rejects compound/filtered commands so cycles aren't burned
  re-learning preflight rules (OpenManus lost 17/56 actions this way).
- maintainer: each cycle, collapse problems sharing one mechanism; drop problems contradicted
  by a later `rc==0` observation (stale `ls: cannot access pyproject.toml`); never label a
  pytest-collection/import-mode signature (`ModuleNotFoundError: No module named 'tests…'`,
  `import file mismatch`) as `deps` — it is `tests`/`build`.

**Tests:** build_agent rejects a compound mutation+probe before submit; maintainer reclassifies
a collection signature as `tests` and prunes a contradicted stale problem.
**Flips/recovers:** OpenManus (budget), markitdown (converges). **Risk:** LOW–MED.

## Validation

1. **Per phase:** `pytest tests/ -q` (the envstate suite) green; coverage ≥80% on touched modules.
   Note: full suite imports `eval.common.scorers` (VM-only) for the RAT runner tests — run the
   gate/world-model/planner/maintainer/build_agent tests locally; run runner tests on the VM.
2. **After P1–P4:** commit → `deploy.sh` (DEPLOY_SRC/DEPLOY_BRANCH overrides) → re-run the
   16-repo smoke (`--arm v1 --num-turn 20`, fresh `--root-path`) → recompute with
   `scripts/compute_essr.py` and `outputs/compare_arm0_v1.py`. Success criterion: false-successes
   gone, v1 ESSR÷all moves toward arm0's 0.41, dumb-init/ai-dial/rq flip to genuine pass.
3. Watch the budget/credit wall (OpenRouter ~$5.49) as before; salvage on 402.

## Sequencing & dependencies

```
P1 (gate+objective+finalize)  ──┬─> P2 (wrapper-free)
                                └─> P4 (done_when/anti-fabrication)
P3 (build-signal + runtime)   ── independent, needed for dumb-init/rq to *pass* (not just honest-fail)
P5 (self-check + dedup)       ── independent, recovers OpenManus/markitdown
```
P1 must land first (everything else assumes the execution gate). P2/P3/P4 can proceed in
parallel after P1. P5 is independent and lowest risk.

## Out of scope (this plan)

- Dockerfile **synthesis/replay** fidelity (the 3 build-fail regressions where v1 solved
  in-sandbox but the synthesized Dockerfile failed) — deferred per instruction; tracked in
  `outputs/v1_t20_consolidated.json` / the synth/replay memory.
- Eval-harness test timeouts (pre-commit, darts).

## Status (2026-06-11)

- **P1 — DONE** (commit `70829d0`): execution-aware gate (`maintainer._verified_test_run_passed`
  reusing `src/synthesizer.py`), planner objective → `python -m pytest -q`, finalize path
  rejects collect-only. New `tests/test_v1_execution_gate.py`; collect-only tests migrated.
- **P2 — DONE** (commit `3b0039a`): planner wrapper-free verification guidance.
- **P3 — DONE** (commit `3b0039a`): `world_model._build_required` (build not signal-less for
  compiled repos) + planner runtime-service heuristic.
- **P4 — DONE** (commit `3b0039a`): planner done_when discipline + anti-fabrication;
  gate `maintainer._uses_test_exclusion` rejects `--ignore`/`--deselect`/`--ignore-glob`.
- **P5 — PENDING**: BuildAgent pre-submit sandbox self-check (OpenManus thrash) + Maintainer
  dedup/reclassify (markitdown).
- **Validation re-run — PENDING**: deploy + 16-repo smoke `--arm v1 --num-turn 20`, recompute
  with `scripts/compute_essr.py` + `outputs/compare_arm0_v1.py` vs arm0's 0.41. Local suite
  green: 1138 passed, 76 skipped, 3 pre-existing unrelated failures.
