# Dockerfile Seed Iteration Log

**Goal:** find a method that consistently produces the best seed Dockerfile in a **single shot**
from the v1 agent side, out of the messy Docker work environment.

**Hard constraint:** do NOT change the agent's actual in-container build logic. Only change
*how execution history / logs are recorded* and *how synthesis uses them*. Small, concise,
root-cause changes only.

**Design spec:** `docs/superpowers/specs/2026-06-18-pinned-seed-dockerfile-design.md`
**Plan:** `docs/superpowers/plans/2026-06-18-pinned-seed-dockerfile-plan.md` (pending)

## How to read this
Each code change gets an entry under **Changes**: WHY (the issue) / FIX / FILES / RESULTS.
Each e2e iteration gets an entry under **Run log**: repo, before/after Dockerfile quality, verdict.

---

## Changes

<!-- TEMPLATE — copy per change
### [2026-06-18] <short title>
**Why (the issue):** <root cause, with file:line evidence>
**Fix:** <the concise change>
**Files:** <paths touched>
**Results:** <what the e2e / unit run showed, before vs after>
-->

### [2026-06-18] Adversarial review outcome (6 sonnet agents)
**Verdict:** approve_with_changes, **NO constraint violations** (design does not touch build logic).
**6 must-fixes folded into the plan** (`docs/superpowers/plans/2026-06-18-pinned-seed-dockerfile-plan.md`):
gate-decouple must cover BOTH sites (agent.py:1194 + 1213-1214); pin = TWO RUNs not compound (retry-wrapper
needs `pip install` at RUN start); `build_pin_instructions` needs `project_name` (editable/VCS already dropped
by `_parse_installed`); it's `pip list --format=freeze` not `pip freeze`; runner `-r` parse bug → distinct pin
basename `jayint-pinned-closure.txt`; `COPY . /app` is runner-injected not agent-emitted. Plus: use
`final_map.installed` directly (no fresh probe); ship gate-decouple standalone first (recovers 11-15 repos).

### [2026-06-18] Change A — decouple run-success from artifact synthesis
**Why (the issue):** v1 success is the host-certified test gate, but TWO sites let downstream Dockerfile
synthesis void it: `agent.py:1193-1196` overwrote `configuration_success` with `_finalize_supervisor_artifacts`'s
return (False on synth failure, agent.py:1344), and the transient handler `agent.py:1209-1216` set it False on any
synth exception. A genuinely-passing run was scored as failure when only the downstream artifact hiccupped.
**Fix:** new `_v1_finalize_and_keep_success(gate_passed)` runs the artifact step for side effects but always
returns the gate verdict (catches/logs synth raise + False). Both call sites routed through it; transient
`except: configuration_success=False` removed. `_finalize_supervisor_artifacts` internals untouched. (No build logic.)
**Files:** `agent.py` (new method ~1330 + call sites 1194, 1207), `tests/test_gate_decouple_v1.py` (new).
**Results:** new tests 3/3 PASS; `test_agent_v1_glue.py` 21/21 PASS (no regression). Per prior analysis
(`outputs/v1_coverage_loss_report.md`) this false-failure class covered 11-15 repos; e2e confirmation in Change E.

### [2026-06-18] Change B+C — record the closure + append the dependency pin
**Why (the issue):** the v1 deterministic recipe replays `pip install -e .`, which RE-RESOLVES deps fresh at
build time (version drift; a dropped/mis-classified install vanishes → hollow on complex repos). The exact
closure IS computed each cycle (`final_map.installed`) but was discarded — `agent_run_summary.json` had
`has_installed=false`.
**Fix (recording, B):** init `self._final_installed=()` (agent.py:1060), store `final_map.installed` after the
loop (agent.py:1179), serialize it to the summary as `installed` (agent.py:2741). No extra probe (reuses the
already-computed closure → no exec round-trip). **Fix (synthesis, C):** new pure `build_pin_instructions(installed,
*, project_name)` (src/envstate/synthesis.py) → TWO RUN bodies `printf '%s\n' '<name==ver>'... > /tmp/jayint-pinned-closure.txt`
and `pip install -r /tmp/jayint-pinned-closure.txt`; excludes the project by normalized name; distinct basename
dodges the runner `-r` parse bug. Appended via new `Synthesizer.add_build_instruction` (→ `_record_setup_instruction`,
same path as recipe cmds) BETWEEN recipe-apply and `generate_dockerfile`, so the `pip install -r` renders LAST and
gets the retry wrapper. `_resolve_project_name` reads pyproject/setup.cfg. (No build logic touched.)
**Files:** `agent.py`, `src/synthesizer.py`, `src/envstate/synthesis.py`, `tests/test_build_pin_instructions.py`,
`tests/test_seed_pin_render.py`.
**Results (unit):** 28 PASS (pin fn 3, render 4, glue 21 unaffected). Rendered sample tail = structural installs →
`RUN printf ... > /tmp/jayint-pinned-closure.txt` → separate retry-wrapped `RUN ... pip install -r ...` as the LAST
RUN, no `COPY` of the pin file. E2E proof in Run log (run 3+).

### [2026-06-19] Capstone qualitative analysis (4 sonnet agents) — verdict + 1 real gap found
Workflow analyzed the 3 convergent pinned seeds vs their messy sessions. **Verdict: `good_with_minor_refinements`.**
microsearch×2 scored 9/10 (only cosmetic defects). **narwhals scored 5/10 — a CRITICAL gap the clean `docker build`
MISSED:** the agent patched repo source (`printf 'def get_dask_expr()...' >> narwhals/_dask/utils.py` + a `python -c`
open/write, both rc=0) but these have `mutation_class=null`, so `build_commands_from_ledger` DROPPED them → the rebuilt
image would regress ~499 tests (the `--build` check only builds + `pip check`s; it doesn't run the suite, so it passed).
Two cosmetic LOWs: pip/setuptools/wheel pinned (self-downgrade); apt-retry header emitted with no apt commands.

### [2026-06-19] Change D — keep successful file-edit commands in the seed (synthesis-only)
**Why:** `build_commands_from_ledger` dropped rc=0 source-patch commands (`printf >> file`, `python -c open/write`)
because the env-mutation classifier marks them `mutation_class=null` → the narwhals seed omitted essential source
patches (~499-test regression). The classifier itself feeds `env_revision`→world-model→PLANNER, so changing it could
perturb the agent's run-time trajectory (build-logic-adjacent). **Kept the fix purely in the synthesis layer.**
**Fix:** `_is_source_file_edit(cmd)` in `src/envstate/synthesis.py` (printf/echo/cat/tee writing stdout to a REAL
file [not `2>`/`&>`/`/dev/null`], `sed -i`, `python -c` open-append/write; never pytest/read-only); `build_commands_from_ledger`
keeps rc=0 events that are file-edits even when `mutation_class=None`. ZERO effect on recording, classifier, sandbox,
or planner. **Over-capture bug caught + fixed in review:** initial regex matched `cat x|grep y 2>/dev/null` (the
`2>/dev/null` stderr redirect) — tightened to stdout-to-real-file only.
**Files:** `src/envstate/synthesis.py`, `tests/test_ledger_keeps_file_edits.py`.
**Results:** 10 unit tests PASS. Re-synthesizing from narwhals' ACTUAL captured ledger: build_commands 9→11, **both
source patches now KEPT, 0 read-only `cat|grep` leaks.** Regression suite green. End-to-end narwhals re-run confirming.

### [2026-06-19] Change E — don't pin the installer itself
**Why:** `pip==25.0.1` (and setuptools/wheel) appeared in every pin closure → pip must self-downgrade before
installing the rest, an unusual ordering step + transient-failure risk (flagged on both microsearch seeds).
**Fix:** `build_pin_instructions` skips `{pip, setuptools, wheel}` (normalized). **Files:** `src/envstate/synthesis.py`,
`tests/test_build_pin_instructions.py`. **Results:** new tests PASS; installer-only input → `[]`.
**Deliberately SKIPPED (cosmetic LOW):** the unconditional apt-retry header for pure-Python repos — harmless dead
layer; not worth the apt-bootstrap entanglement. Noted for the user.

### [2026-06-19] Change F — raise success-gate partial-pass threshold 0.5 → 0.8 (user request)
**Why:** at 0.5 a partial-pass run (rc!=0) counted as a working env even if half the tests failed — too lenient.
**Fix:** `Synthesizer.MIN_PASS_RATIO = 0.8` (src/synthesizer.py:367) — single source of truth, propagates to the
finalize gate (agent.py:1289) AND `verification_bundle.py:132`. A partial-pass run now finalizes only with a STRONG
majority (≥80% passed). Updated the pinned test (`test_synthesizer.py`). Only governs the rc!=0 fallback; rc==0
(~100% pass) and the in-loop `done_flag` (stricter no-failures `_verified_test_run_passed`) are unaffected.
**Files:** `src/synthesizer.py`, `tests/test_synthesizer.py`. **Results:** 61 partial-pass gate tests + constant
test PASS — no behavioral test fixture fell in the [0.5,0.8) band, so the change is clean.
**Caveat (known, deferred):** the ratio can't tell an env-defect failure from a genuine source-bug failure, so 0.8
may reject a working env whose repo legitimately has >20% failing tests (see FUTURE-tier-b-honest-failure-diagnosis.md).

---

## Baseline findings (pre-change grounding, 2026-06-18)

Captured **26 messy workplaces** under `outputs/` (reusable fixtures: `agent_run_summary.json` +
`logs/setup_logs/` + sometimes a produced `Dockerfile`). Re-synthesizing from these is far cheaper
than re-running the ReAct loop, so the iteration loop will work against them where possible.

What a current v1g seed looks like (`outputs/e2e_v1g/microsearch_int`):
- `build_recipe_source = "action_ledger"`; recipe = `["pip install -e .", "pip install pytest pytest-asyncio"]`.
  **Faithful for a simple repo** — not hollow here. Hollowness is expected to appear on COMPLEX repos.
- `action_ledger` entries carry `cmd, rc, mutation_class, env_revision_before/after, stdout, summary` —
  rich enough to select successful mutating commands and order them. **Recording quality is good.**
- `successful_actions` is EMPTY (len 0) — the legacy [[v1-success-capture-gap]] field; v1g does NOT use it
  (it reads `action_ledger`), so that gap is not the active v1g synthesis path. Worth confirming no other
  consumer depends on it.
- **No `installed` / `pip_freeze` / `env_snapshot`** recorded → the closure pin (spec rung 3) is absent
  from every captured workplace; validating the pin requires a fresh run with new recording.
- `burr_int2` produced no Dockerfile because `configuration_success=false` (genuine failure) — a true
  negative, correct behavior, not a synthesis bug.

**The v1 path is deterministic (key):** `agent.py:1781 _synthesize_final_build_recipe` → on the v1
path (`enable_envstate` + `action_ledger`) uses `build_commands_from_ledger()` (src/envstate/synthesis.py),
NOT the LLM. That function replays only successful (rc==0) env-MUTATING commands in trajectory order →
structurally faithful (hence microsearch is fine). The LLM-replay hollowness (edsl) is the arm0 fallback
path, not v1. So on v1 the remaining gaps are (a) closure not pinned → replaying `pip install -e .` re-resolves
deps fresh at build time (drift / yanked-dep failure); (b) a command mis-classified (`mutation_class` None or a
flaky rc≠0 that later succeeded) gets silently dropped. **The freeze pin fixes BOTH** — locks exact versions
(anti-drift) AND backfills any structurally-dropped package (anti-hollow). Pin injection point:
`self.instructions` after `apply_build_recipe()` (src/synthesizer.py:2715), rendered by generate_dockerfile (3986).

**Recording gap to close for the pin:** the final `EnvSnapshot.installed` (pip freeze) is probed during the run
(`agent.py:1104/1117/1124/1169`) but discarded — not retained on `self`, not serialized. Fix = retain final
snapshot + serialize it (to the summary and/or a workplace artifact) so synthesis can append the pin RUN.

Candidate root-cause levers (all recording/synthesis-side, none touch build logic):
- **L1 (selection):** does action_ledger→recipe extraction keep ALL env-establishing commands (apt, multiple
  pip installs, build steps, env) and order them, on complex repos? (Needs a complex v1 fixture.)
- **L2 (closure pin):** record the final `pip freeze` and add it to the recipe (the spec's pin).
- **L3 (fidelity):** is the recipe a thin guess vs a faithful replay when the trajectory is long?

**Hollowness exemplar (the problem, on a complex repo):**
- `outputs/abl_edsl_arm0/.../expectedparrot__edsl` (arm0, `build_recipe_source="llm"`): the ENTIRE recipe is
  `pip install pytest pytest-asyncio pytest-mock pytest-env pytest-html pytest-xdist` — installs only pytest
  PLUGINS, never edsl or its deps. Verified cmd = `pytest tests/ --collect-only` (collect-only false-success).
- `outputs/agentstack_arm0/.../AgentStack` (arm0, llm): more faithful (`pip install -e ".[dev,test,crewai]"` +
  extras) but also gated on `pytest --collect-only`.
- Both are the arm0 LLM-replay path. v1g uses deterministic `action_ledger` extraction (faithful on
  microsearch) — likely more robust, but NO complex v1g fixture exists to confirm → needs a fresh v1 run.

**Two upstream poisoners to respect:**
1. Collect-only false-success → recipe synthesized for an env never actually exercised (garbage-in). The
   loop must target sessions with a REAL test pass, and the rubric must reject collect-only "success".
2. LLM replay drops real installs (edsl). The deterministic ledger path + a freeze pin are the antidotes.

**Seed-quality rubric (draft):** a seed is "good" iff (a) it installs the project itself (`-e .` or equiv),
(b) it installs the project's real runtime deps (not just test plugins), (c) the closure is pinned or
reproducible, (d) a clean `docker build` + the verified (non-collect-only) test command passes.

Environment: Docker UP; OpenRouter key present in `.env`; venv py3.13.9; datasets present; 42Gi free.

## Run log

<!-- TEMPLATE — copy per e2e iteration
### [2026-06-18] run N — <repo>
**Setup:** model, flags, dataset entry
**Seed Dockerfile quality:** <hollow / partial / good; what's missing>
**Root cause of any gap:** <recording vs synthesis vs other>
**Action taken:** <link to a Changes entry, or "analysis only">
-->

### [2026-06-18] run 1 (BASELINE, current code) — microsearch (simple control)
**Setup:** `run_one.py seed_baseline_microsearch alexmolas/microsearch@632ff2 12`, deepseek-v4-flash, python:3.12-slim, enable_v1+contract_graph.
**Result:** `configuration_success=true`, REAL test run (`python -m pytest -q`, not collect-only). `build_recipe_source=action_ledger`.
**Seed Dockerfile quality:** GOOD/faithful for a simple repo — `cd /app && pip install -e .` + `pip install pytest`. `build_commands_from_ledger` correctly kept the 2 rc=0 `language_package_install` cmds, excluded collect-only + test cmds.
**Gap confirmed:** `has_installed=false`, `has_env_snapshot=false` → freeze NOT serialized; no version pin. (Simple repo, so no hollowness to expose here — this is the control.)
**Action:** baseline only; harness + OpenRouter validated.

### [2026-06-18] run 3 (AFTER Changes A+B+C) — microsearch (simple control)
**Setup:** `run_one.py seed_pinned_microsearch alexmolas/microsearch@632ff2 12`, same model/base as run 1.
**Result:** `configuration_success=true`; **`has_installed=true`, 39-package closure captured** (was discarded in run 1).
**Seed Dockerfile quality:** structural (`pip install -e .` + `pip install pytest`, faithful) THEN the pin layer:
`RUN printf '%s\n' 'aiohttp==3.9.1' ... 'yarl==1.24.2' > /tmp/jayint-pinned-closure.txt` then a SEPARATE
retry-wrapped `RUN ... pip install -r /tmp/jayint-pinned-closure.txt` as the LAST RUN. Full transitive closure
(fastapi, numpy, pandas, pyarrow, pydantic...) now version-locked. **microsearch (project) correctly EXCLUDED.**
**Rubric:** ALL 6 PASS. 1-4 by inspection (no COPY of pin file; two separate RUNs; pin last; microsearch excluded).
Check 5: built the ACTUAL produced Dockerfile (with `COPY . /app` injected like the runner's render_eval_dockerfile)
from the workplace context → `BUILD EXIT=0`, `IMPORTS_OK` (fastapi/numpy/pandas/pyarrow/pydantic/bs4/feedparser all
import from the locked closure). Check 6 via Change A unit tests.
**Harness lesson:** a first build attempt FAILED because I reconstructed the pin from `summary.installed` (the RAW
closure, which INCLUDES `microsearch==0.0.1`). The agent's real Dockerfile correctly excludes the project — always
validate the ACTUAL produced Dockerfile, never a reconstruction from `summary.installed`. (Bakes into the rubric checker.)
**Action:** validates Changes A+B+C on a real run. before/after = same faithful structure + NEW exact-version closure lock that builds clean.

### [2026-06-18] run 2 (BASELINE, old code) — burr (complex) — NON-CONVERGENCE
**Setup:** `run_one.py seed_baseline_burr DAGWorks-Inc/burr@79137e 16`. Started on old code (pre-A/B/C).
**Result:** `configuration_success=false`, 43 ledger commands over the cycles, no verified test, **NO Dockerfile**.
**Read:** burr's env setup is too hard for the agent in 16 steps on this run → no working messy environment → no
seed. This is CORRECT behavior (garbage-in → no seed), not a synthesis defect. Confirms the seed-quality method
only applies to CONVERGENT sessions; non-convergence is an agent-build-logic limit, which is OUT OF SCOPE here.
(`burr_int` converged once historically — burr is stochastic.) Retrying burr pinned with 20 steps as `bb7jrf1mg`.

### [2026-06-18] Change E tooling — `outputs/dockerfile_seed_iteration/score_seed.py`
Reusable rubric checker (sonnet-built). Static checks (no_copy_pin, two_separate_pin_runs, pin_is_last_run,
project_excluded, pin_retry_wrapped) + `--build` (inject `COPY . /app`, docker build the ACTUAL Dockerfile, `pip check`).
Reports cfg/installed_n/recipe/verified_test_commands/is_collect_only. Self-test: PASS on `seed_pinned_microsearch`
(static + build), FAIL on old `microsearch_int` (discriminates). Used to score every run in the loop.

### [2026-06-18] run 4 (PINNED) — burr(20 steps) — non-converge AGAIN, but pin robustness PROVEN
**Result:** `configuration_success=false` again (burr env setup too hard for the agent → OUT OF SCOPE), no seed.
BUT: **`has_installed=true`, 89-pkg closure recorded even on a failed run** (Change B robust). And the decisive
robustness test: I fed burr's recorded closure through the ACTUAL `build_pin_instructions(project_name="burr")` →
88 pkgs (burr correctly excluded) → `docker build` on clean `python:3.12-slim` → **BUILD EXIT=0, `No broken
requirements found.`** So the exact-version pin regression risk (review should-fix #5) did NOT materialize on a
complex 88-pkg closure (2.3× microsearch). **Pin installability is robust across closure sizes.**

### [2026-06-18] run 5 (PINNED) — safari-webarchiver — non-converge (`planner_giveup`)
`configuration_success=false`, `stop_reason='planner_giveup'` after 11 cycles, 10-pkg closure recorded, no seed.
Planner couldn't establish a working env → OUT OF SCOPE (planner/build logic). Convergence rate across my picks:
microsearch ✓, burr ✗✗, safari ✗ — the agent converges reliably only on simpler repos. This is the known v1
convergence limit (see [[v1-regresses-vs-arm0-on-16]] / [[v1-coverage-loss-self-inflicted]]), independent of seed synthesis.

### [2026-06-19] run 6 (PINNED) — narwhals — CONVERGED, COMPLEX, score_seed PASS
**Result:** `cfg=true`, real `python -m pytest -q` (NOT collect-only), 50-pkg closure. `score_seed` Overall **PASS**
(all 5 static checks). The deterministic ledger extraction faithfully captured a genuinely complex setup:
`pip install -e .`, pandas, `pip install -r requirements-dev.txt`, **`apt-get install default-jre-headless`** (system
dep), pyspark, and dask version-resolution (`dask==2024.9.1 dask-expr==1.1.15 --force-reinstall`) — then the 50-pkg
closure pin, narwhals excluded. This is the strongest case: a complex multi-dep + system-dep env reproduced as a
one-shot seed. Clean `--build` test running as `bd7e885jv`.

### [2026-06-19] run 7 (PINNED) — microsearch#2 — CONVERGED, score_seed PASS (run-to-run stable)
`cfg=true`, `stop_reason='done_flag'`, identical 39-pkg closure + recipe to run 3. Overall **PASS**. Confirms the
method is stable across stochastic agent trajectories — the same convergent repo yields the same good pinned seed.

**Tally so far — pinned seeds scored:** microsearch (PASS, builds clean), microsearch#2 (PASS), narwhals (PASS,
complex). Pin build-robustness: 39-pkg (microsearch) + 88-pkg (burr) + 50-pkg (narwhals) closures ALL build clean.
Convergence (out of scope): microsearch ✓✓, narwhals ✓, burr ✗✗, safari ✗.

**DECISIVE: narwhals complex seed `--build` = FULL PASS** (`build_clean` + `closure_importable`). A complex env
(pandas/pyspark/dask + `apt default-jre-headless` + 50-pkg pin) reproduces as ONE clean-building seed. The method
(gate-decouple + record-closure + pin) consistently produces a faithful, complete, clean-building seed in one shot
across simple (×2) and complex convergent sessions. Capstone sonnet qualitative analysis next.

### [2026-06-19] run 8 (PINNED, code w/ Changes D+E) — narwhals#2 — end-to-end PASS, Change E confirmed
`cfg=true`, real `python -m pytest -q`, 51-pkg closure. `score_seed` Overall **PASS** (all 5 checks). **`pip==` count
in pin = 0** → Change E (skip pip/setuptools/wheel) confirmed end-to-end. This run took a DIFFERENT stochastic
trajectory and converged WITHOUT the dask source patch (utils.py count 0), so Change D wasn't exercised here — that's
expected; Change D's definitive proof is the deterministic re-synthesis from run-6's ACTUAL ledger (patches kept,
reads dropped). Net: 4 convergent pinned seeds now PASS (microsearch ×2, narwhals ×2).

---

## FINAL OUTCOME (2026-06-19)
**A method that consistently produces the best one-shot seed Dockerfile from the messy convergent session — found,
implemented, validated.** Five synthesis/recording-only changes (A gate-decouple, B record-closure, C closure-pin,
D keep-file-edits, E don't-pin-installer); 38 unit tests pass; ZERO agent build-logic edits. Evidence: 4 convergent
pinned seeds score PASS; pin closures of 39/50/88 packages all `docker build` clean; the one real gap (dropped source
patches) was caught by sonnet qualitative analysis — which a clean build alone missed — and fixed (Change D), proven
on narwhals' real ledger. Convergence itself (microsearch ✓✓ narwhals ✓✓ burr ✗✗ safari ✗) is the agent's build-logic
limit = out of scope. Remaining cosmetic (apt-retry header on pure-Python repos) deliberately left for user review.
Changes are UNCOMMITTED for manual inspection.

## REAL BENCHMARK-RUNNER E2E VALIDATION (2026-06-19)
Ran the actual `run_repo2run_benchmark.py` (not a manual build) on the new code.
- **Fresh runs hit the convergence wall (agent build-logic, OUT OF SCOPE):** databonsai non-converged because its
  tests need a valid OPENAI_API_KEY the sandbox lacks (`test_run_attempts: []` — NOT the 0.8 gate; exonerated).
  narwhals fresh ran >1h at cycle 11/16 graph-off (the runner only exposes the graph via `--arm v1g`, which forces
  12 steps+cleanroom) — graph-off hurt convergence vs my run_one (graph-on). Stopped it.
- **Runner-side path VALIDATED via `--reuse-existing-workplace`** on the known-good new-code narwhals 50-pkg pinned
  seed (skips the agent/convergence gamble, runs the REAL render→git-clean→build→test→repair):
  `reused=true, dockerfile_generation_success=true, environment_build_success=true, repair_rounds=0, goal=success`.
  Pin carried into `Dockerfile.eval` (2 RUNs); `pip install -r /tmp/jayint-pinned-closure.txt` present + retry-wrapped
  (runner did NOT drop it — distinct basename dodged the `-r` parse bug); `COPY . /app` runner-injected; the inline-RUN
  pin survived `git clean -fdx`; build rc 0, tests effective. **ZERO repair rounds = the seed was correct first-shot.**
- **RATBench NOT run locally (honest):** `run_rat_benchmark.py` imports its harness from `RAT_ROOT=/Users/john/rat-bench-integration`,
  a stale separate checkout lacking my changes → would test OLD code. The RATBench `--repair-mode runner` path is a
  verbatim port of repo2run's repair, so the repo2run reuse run covers the shared runner-side surface. A true RATBench
  number needs the VM harness with this branch synced.
**Bottom line:** agent-side seed generation + the runner-side consumption of the pinned seed are both validated on real
infra. A fresh-sweep headline number is gated by the agent's CONVERGENCE rate (out of scope, unchanged), not seed quality.

## A/B ABLATION — Option Y: pin vs no-pin (2026-06-19, autonomous)
Isolating the pinned-seed changes by running the SAME 5 converging-medium repos through `--arm v1g` twice:
PIN arm = working tree (all 6 changes A–F live); NO-PIN arm = `git stash` the 4 files → clean HEAD (MIN_PASS_RATIO back to
0.5, no pin/recording changes). Same model (deepseek-v4-flash), concurrency 2, repair-rounds default 2.

### WHY (the issue surfaced)
PIN arm converged only **2/5** (Scrapling ✓ repair=0, microsearch ✓ repair=0; burr/safari/narwhals ✗ `dockerfile_missing`).
A sonnet forensic pass (read-only over the on-disk workplace artifacts) classified the 3 failures:
- **DAGWorks-Inc__burr → GATE_REJECTION (pin-caused).** Real test run = `84 failed, 294 passed` → pass ratio **0.7778**.
  Gate log: `[v1] finalize test-run: rc!=0 sub-majority pass-ratio (0.7777…) -> reject`. The 84 failures are ALL one
  orthogonal issue (`pytest-asyncio` mode not set), not 84 env defects. At the old MIN_PASS_RATIO=0.5 this is ACCEPTED;
  at the new 0.8 it is REJECTED. **The 0.8 change (F) is the exclusive cause.**
- **narwhals → GENUINE non-convergence.** Polars↔Python 3.13 SIGSEGV (rc=139) on `pivot_test`; pytest killed before any
  summary → 0 passed. Rejected at ANY threshold. (Earlier reuse-validation built narwhals on a different base/polars combo.)
- **safari-webarchiver → GENUINE non-convergence.** Needs macOS `WebKit` framework, impossible on Linux Docker; 0/12 pass.

### KEY METHODOLOGICAL FINDING (confound)
The A/B as run conflates TWO variables: the PIN (changes B/C/D/E, which improve seed REPRODUCIBILITY / repair-rounds) and
the GATE THRESHOLD (change F, 0.5→0.8, which trades CONVERGENCE for quality). Raw convergence counts (2/5 vs no-pin) are
dominated by F, not the pin. The burr delta is a gate-threshold effect, NOT a seed-quality effect.
Second irony worth recording: the pin's value is reproducibility insurance for LARGE/version-fragile closures — but the
only repos that converge here are SMALL closures (Scrapling, microsearch) whose un-pinned seed already rebuilds first-shot
(repair=0). So this repo set may show little/no repair-round delta even if the pin is sound — the test repos are too easy
to stress it.

### FIX / DECISION (pending — NOT silently applied)
burr's rejection at 0.7778 is the **0.8 bar working as the user explicitly requested**, not a code bug. Reverting 0.8 is a
user decision, so it is NOT auto-applied. Options to put to the user once the no-pin control finishes:
  (a) keep 0.8 (stricter quality bar; accept it rejects ~78%-passing envs whose misses are one config issue), or
  (b) lower to ~0.6–0.7 (would keep burr), or
  (c) make the gate failure-aware (e.g. accept if the only failures share one orthogonal cause).
Plus a clean follow-up arm to ISOLATE the pin from the gate: **pin-changes @ MIN_PASS_RATIO=0.5** vs **baseline @ 0.5**.

### RESULTS (no-pin control RUNNING)
no-pin arm (boixl2zja) in flight → outputs/aby_nopin/. Expected to CONVERGE burr (0.7778 ≥ 0.5) and still FAIL
narwhals+safari (genuine). Final compare via `outputs/aby_compare.py`. Verdict to separate convergence (gate-driven) from
seed-quality (pin-driven, on the co-converged set).

### KEY DISCOVERY (microsearch, early datapoint) — Change A validated + a pre-existing cleanroom bug
microsearch finished in BOTH arms: same first-shot build (env_build=True, repair=0), but PIN cfg_success=**True** /
NOPIN cfg_success=**False**. Sonnet forensic (read-only, pin diff via `git stash show -p`) root-caused it:
- BOTH arms had a GENUINE in-sandbox pass: `python -m pytest -q` rc=0, `1 passed` (tests/test_engine.py::test_search_engine).
  done_flag=True in both. The success is REAL, not collect-only.
- **NOPIN drops it via a pre-existing CLEANROOM BUG.** `_finalize_supervisor_artifacts → _verify_cleanroom_or_fail →
  verify_cleanroom` runs the verified test command through Docker SDK `containers.run(image_ref, "<cmd>")` AS A STRING.
  Docker SDK `shlex.split()`s it. NOPIN's LLM happened to phrase the command `cd /app && python -m pytest -q` →
  `["cd","/app","&&",...]` → Docker execs `cd` as a binary (it's a shell builtin) → container fails →
  cleanroom=False → `_finalize_supervisor_artifacts` returns False → configuration_success=False. The ENV is fine; the
  VERIFIER is broken on any shell syntax (`cd …&&`, pipes). The benchmark runner itself invokes via `sh -lc` and is
  unaffected; only the agent-side cleanroom check has this bug.
- **PIN's Change A (gate-decouple) correctly MASKS it.** `_v1_finalize_and_keep_success`: if the host test gate passed,
  return True regardless of what the artifact/cleanroom step returns. So a real `1 passed` is never discarded by an
  artifact-side failure. (Incidentally PIN's LLM used the bare `python -m pytest -q`, so PIN's cleanroom also passed — but
  Change A would have kept it either way.)

IMPLICATIONS:
1. **Change A = validated, correct success-capture fix** (recovers the documented v1 success-capture gap). IN SCOPE
   (it governs how execution results are recorded/used, not in-container build logic).
2. **Separate pre-existing bug found (FLAGGED, not auto-fixed):** cleanroom `containers.run(<string>)` should be
   `containers.run(["bash","-lc",<string>])` (mirror the runner's `sh -lc`). It's verification/build logic = OUTSIDE the
   recording-only boundary, Change A already neutralizes its impact, and editing source mid-run would corrupt the live
   no-pin arm. Recommend fixing it separately + re-running for a clean read; left for user decision.
3. **A/B convergence counts are NOISY:** part of the pin/no-pin delta is LLM test-command phrasing interacting with the
   cleanroom bug, not the pin closure. The pin's true value (exact-version reproducibility) needs HARDER repos
   (large/fragile closures) to measure — these 5 converging repos are too easy (un-pinned seed already builds first-shot).

## BUG 1 + BUG 2 — VM smoke surfaced two seed defects (2026-06-19, autonomous)

First VM smoke of the pinned-seed agent (run-20260619-084304, jhao104/proxy_pool, v1g/deepseek) exposed two
agent-side synthesis defects. Investigated via an adversarial workflow (3 sonnet tracers → skeptic → judge,
w2r08vz1g); the skeptic refuted two wrong hypotheses (single-XML-blob; "repair loop salvaged it").

### BUG 1 — project leaks into the pinned closure (latent risk)
WHY: `_resolve_project_name` (agent.py) returned None when a repo's pyproject.toml has no `[project]`/`[tool.poetry]`
table and no setup.cfg (proxy_pool's pyproject is synthesized only inside the container). With project_name=None,
`build_pin_instructions`' exclusion guard `if proj and _norm(name)==proj` short-circuits, so the project's own
(non-existent) spec `proxy_pool==1.0.0` leaks into the pin closure → would break `pip install -r` once the seed
actually runs.
FIX (agent.py:_resolve_project_name): add a repo-URL-basename fallback before `return None` (strip `.git`, trailing
`/`). `_norm` maps `proxy_pool`→`proxy-pool` so the closure entry is then excluded. Recording/synthesis only.
TESTS: tests/test_resolve_project_name.py (6) — basename fallback, `.git` strip, trailing-slash, real-`[project]`
precedence, empty→None, end-to-end pin exclusion.

### BUG 2 — "no-op recipe": the whole seed collapses into one unterminated heredoc (the 50-run blocker)
WHY (confirmed root cause): the build agent records only the FIRST LINE of a multi-line action
(`build_agent.py:_extract_worker_action` plain/fenced branches apply `.splitlines()[0]`). A heredoc like
`cat > /tmp/prepend.py << 'EOF'\n<body>\nEOF` is stored as the orphan opener `cat > /tmp/prepend.py << 'EOF'`
(body + terminator gone). Change D (`_is_source_file_edit`) then KEEPS it (`cat` + `> /tmp/...`), so the broken
opener is emitted as a Dockerfile `RUN`. At eval time the adapter's heredoc-accumulation mode (fixed control-plane,
not ours to touch) sees the unterminated `<<` and greedily swallows ALL 13 following RUNs — incl. the separately
added pin — into one base64 `/bin/sh` script that installs NOTHING. Decoded proxy_pool eval recipe: 4 heredoc opens,
0 terminators, 14 RUN directives swallowed.
- Skeptic correction: the run was NOT salvaged by the repair loop (build_success=False, all repair attempts failed).
  It scored "success" only because the v1 done-gate trusts the live in-sandbox result (248 passed) and cleanroom is
  off, so the broken Dockerfile is never rebuilt before declaring success. The defect is invisible until external eval.
- Note: the truncated heredocs were no-ops in the LIVE container too, so dropping them makes the SEED match what
  actually built the working env (the real installs were printf/python-c/pip forms, not the heredocs).
FIX (src/envstate/synthesis.py): new `_is_unterminated_heredoc(cmd)` = single-line (no `\n`) AND bears a heredoc
operator (precise regex `<<-?\s*['\"]?[A-Za-z_]\w*`, so `$((1<<4))` bit-shifts don't match). Drop such commands in
BOTH gates: in `build_commands_from_ledger` BEFORE the mutation_class branch (covers the path where classify_mutation
tags `cat > f <<` as 'file_or_env_change' and would otherwise keep it — the judge's `_is_source_file_edit`-only fix
missed this), and in `_is_source_file_edit` itself. A genuinely terminated multi-line heredoc keeps its `\n` and is
preserved. Synthesis-only; pin layer and the live-test done-gate untouched.
TESTS: tests/test_bug2_heredoc_guard.py (11) — /tmp//app//testbed heredoc-opens dropped; terminated heredoc kept;
printf/sed-i/python-c kept; **integration: heredoc-open dropped even when mutation_class is set** (fails without the
extraction-loop guard); bit-shift not treated as heredoc; helper matrix; recipe never contains an embedded `RUN`.

### NOT FIXED (out of scope / flagged)
- The upstream recording truncation (`_extract_worker_action` `.splitlines()[0]`) means the build agent's heredoc
  file-writes are no-ops in-container too. Fixing it changes what EXECUTES in the sandbox (build logic) + broad blast
  radius (the loop/prompt/stuck-guard assume single-line actions). Left as a flagged follow-up; the synthesis-layer
  guard makes the SEED correct without touching execution.

### RESULTS
172 synthesis/finalize/Bug-1/Bug-2 tests pass. Both fixes compose: with Bug 1, the pin excludes proxy_pool; with
Bug 2, the seed keeps the real installs (pip/python-c) and drops the 4 broken heredoc-opens → no eval-adapter
collapse. Pending VM re-smoke on proxy_pool (decode recipe: terminated, no embedded RUN, installs execute,
repair_rounds drops, closure excludes project) before the 50-repo run.

### RE-SMOKE RESULT (proxy_pool, run-20260619-154217) — both fixes VERIFIED; deeper issue isolated
- BUG 2 fixed (verified): agent Dockerfile is now **8 well-formed RUNs** (was 1 collapsed base64 blob). No
  unterminated heredoc, no `RUN` swallowed. The recipe is legible and the runner repair loop now engages
  PRODUCTIVELY (targeted incremental fixes: add missing `version`, add setuptools/wheel) — vs the old monolithic
  no-op it could not touch.
- BUG 1 fixed (verified): pin closure excludes `proxy_pool` (no `proxy_pool==`/`proxy-pool==` in the printf line).
- STILL build_failed (NOT a regression — old 084304 was also build_failed): proxy_pool's working pyproject fix was a
  heredoc `cat > /app/pyproject.toml << 'PYEOF'` (cyc2). Recorded truncated → dropped by the Bug-2 guard (a body-less
  heredoc can't be replayed). Seed lacks the pyproject patch; proxy_pool's raw pyproject has a `[project]` table
  missing `version` → real eval error `project must contain ['version']` → `pip install -e .` aborts the build.
  Repair (2 rounds: sed version + setuptools) did not fully recover it within the bound.
- ROOT = the flagged DEEPER issue: recording truncation (`_extract_worker_action` `.splitlines()[0]`) loses heredoc
  bodies entirely. The synthesis guard correctly avoids the catastrophic collapse but cannot RECOVER lost content.
  For repos whose ESSENTIAL setup is a heredoc (pyproject/config creation — a common LLM pattern), the seed is
  well-formed but INCOMPLETE and leans on the repair loop. Fixing the recording = changing what EXECUTES in-container
  (behavioral, broad blast radius) → OUT of the recording/synthesis-only scope; needs a user decision (gate on 50-run
  attribution data: how big is bucket C "synthesizer dropped a verified-working env").
