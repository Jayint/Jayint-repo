# Dockerfile-repair: our self-verify vs the Repo2Run original

Compares **our** repair (`agent.py:_self_verify_and_repair` → `src/artifact_verify.py:verify_and_repair_recipe`
→ `src/recipe_repair.py`) against the **original** in `run_repo2run_benchmark.py`
(the build→test→repair loop at lines 3398–3530 + helpers 2563–3140). All line numbers are exact.

## TL;DR — which is correct?
- **Repo2Run owns the correct *end-to-end behavior*:** the repair loop **is** the eval loop, so
  every repaired artifact is the artifact that gets scored, and it's re-validated in the *real*
  eval image each round (`run_repo2run_benchmark.py:3398-3541`).
- **Ours owns the better *factoring*:** it repairs the **structured recipe** (single source of
  truth) and re-renders, so the agent's Dockerfile, the RAT adapter, and the repo2run runner all
  inherit one fix; its detection/resolution code is a faithful, byte-identical port; its
  classifier is a strict superset.
- **But ours has two behavioral regressions** that cost us pass-rate: (1) it **discards the
  repaired recipe unless `status == "resolved"`**, throwing away partial/unconfirmed
  improvements; (2) the self-verify runs in a **separate clean-room image, decoupled from the
  scorer**, so a clean-room false-negative silently keeps the broken recipe. Repo2Run has
  neither problem because repair and scoring are the same loop.

Net: **port Repo2Run's loop semantics (persist the repaired artifact, validate where you score)
onto our cleaner recipe-level engine.**

## What each side actually does (per round)

| Step | Repo2Run (`run_repo2run_benchmark.py`) | Ours (`artifact_verify.py`) |
|---|---|---|
| Render | `render_eval_dockerfile` + `normalize_eval_dockerfile_for_replay` on the **agent Dockerfile text** (3382) | `render_verification_dockerfile` re-renders from the **recipe** via the synthesizer, then injects `git clone` to make it self-contained (208-233) |
| Build | `docker build` of the eval Dockerfile (3414) | `build_image` in a temp context (254) |
| Test | `evaluate_built_image` runs **every** `test_command`, requires **all** effective (2843-2881) | `run_test_command_in_image` runs the **single** `verified_test_command` (269) |
| Classify | `classify_test_execution` → `effective` (2864) | `classify_test_execution` → `effective` (105) — port + extra help/invocation/internal-import signals |
| Repair order | deterministic missing-module **first** (3454), else LLM (3503) | `_apply_repair`: deterministic missing-module **first** (306), else LLM (310) |
| Persist | writes the repaired Dockerfile to disk **every** attempt; the **last** one is scored (3400, 3537-3541) | adopts the repaired recipe **only if `resolved && changed`** (`agent.py:1202`); else **keeps original** (`agent.py:1208`) |
| Bound | `range(max_repair_rounds + 1)`, default per `--dockerfile-repair-rounds` | `range(max_rounds + 1)`, default `self_verify_max_rounds=2` |

## Faithfully ported / equivalent (no gap)
- **Missing-module regex** — byte-identical: `recipe_repair.py:74-76` == `run_repo2run_benchmark.py:2563-2565`.
- **Known fallbacks** — identical: `{ppocr, ppstructure → paddleocr==2.7.3}` (`recipe_repair.py:79-82` == `:2566-2569`).
- **module → requirement resolution** — identical algorithm: scan `*requirements*.txt` (≤100 files),
  then `poetry.lock` (≤20) for the **declared/pinned** version, prefer that over a bare name
  (`recipe_repair.py:122-185` == `:2602-2663`).
- **deterministic-before-LLM** ordering — same.
- **LLM prompt philosophy** — both forbid dropping/merging/reordering successful setup commands,
  demand a full artifact (not a patch), and use a `high|medium|low` confidence enum
  (`recipe_repair.py:41-68` vs `:54-75`).
- **never-raise + bounded-rounds** contract — both.
- **dedupe-before-install** — both skip a requirement already installed
  (`recipe_repair.py:202-215` vs `:2675-2705`).

## Gaps & differences (ranked by impact)

### GAP 1 — HIGH · adopt-gate discards repaired recipes
`agent.py:1202`: `if result.status == "resolved" and result.changed:` adopt, **else keep
original** (`:1208`). A repair that installed 2 of 3 missing modules, or added a service start
the clean-room couldn't *confirm*, is **thrown away** — the original (more broken) recipe is what
ships. Repo2Run instead writes the repaired Dockerfile every attempt and scores the **last**
one (`:3400`), so partial progress always survives. This is the mechanism behind the many
`status=unresolved; keeping original recipe` losses (e.g. `rq/rq`, `open-webui/mcpo`).
**Fix:** adopt the repaired recipe whenever it strictly adds setup (any successful deterministic
install, or an LLM repair with ≥ the original's commands), not only on `resolved`.

### GAP 2 — HIGH · repair loop decoupled from the scorer
Repo2Run's repair loop **is** the scoring loop — same image, `environment_build_success` is read
straight off the last repaired attempt (`:3537-3541`). Ours runs self-verify in a **separate**
image `dockeragent-selfverify-<slug>` (`agent.py:1192`) and the **real** score is produced later
by a different harness (`multi_docker_eval_adapter`). Two consequences: (a) a clean-room
false-negative (different cloned HEAD, transient network) discards a genuinely-good repair;
(b) the artifact validated is not guaranteed to be the artifact scored. **Fix:** either score the
self-verified image directly, or feed the adopted recipe into the scorer with no second gate.

### GAP 3 — MED · no service/daemon deterministic repair; ours can also drop the service upstream
Neither deterministic repair starts services — both only handle `ModuleNotFoundError`. But
Repo2Run **replays the agent's `runtime_commands` every eval round** inside the test script
(`evaluate_built_image`, `:2843-2844`), so an agent-run `redis-server --daemonize yes` survives.
Ours threads runtime-prep through `_select_runtime_preparation_commands_for_eval`, which can
**filter** the service start (this is exactly the `rq/rq` redis case), and the repair loop can't
recover it because (a) `ConnectionError` is not a missing-module signal and (b) GAP 1 discards the
round. **Fix:** add a deterministic `ConnectionError|Connection refused → start <service>` repair
that writes to `runtime_preparation_commands`, and stop filtering verified service starts.

### GAP 4 — MED · single vs all test commands
Repo2Run requires **all** `test_commands` to be effective (`all_test_commands_effective`,
`:2878-2881`). Ours validates **one** `verified_test_command` (`artifact_verify.py:389`). If the
recipe ships multiple test commands, ours under-validates and can call a partial env "resolved."

### GAP 5 — LOW · no infra-aware fast-break
Repo2Run breaks the loop on `docker_build_failed_due_to_unavailable_daemon` (`:3487`) so it
doesn't spend an LLM round on an infra outage. Ours has no equivalent — an infra build failure
still triggers an LLM repair attempt.

### GAP 6 — LOW · missing eval-fidelity touches
Repo2Run adds `--add-host postgres:127.0.0.1` for postgres suites (`:2848`) and threads observed
`pip_constraints` through normalize on every repaired render (`:3482`). Our verify path has
neither, so postgres-backed repos and version-pin consistency are weaker in the clean-room.

### GAP 7 — tradeoff · recipe-repair vs Dockerfile-repair (not a bug)
Ours edits the **recipe** and re-renders, inheriting the synthesizer's retry/bootstrap wrappers
for free (a real strength: one fix, all consumers). Repo2Run edits the **rendered Dockerfile**
and re-runs a much heavier `normalize_eval_dockerfile_for_replay` with domain special-cases
(torch replacement `:1354`, mosaicml stack `:1385`, poetry-lock handling `:1632`, orphan
multi-line `RUN` collapse `:1835`). Those special-cases only protect ours if the **synthesizer**
reproduces them; otherwise we silently lose them on re-render.

## Recommended changes (priority order)
1. **`agent.py:1202-1209`** — replace the `resolved`-only adopt gate with "adopt if the repaired
   recipe strictly supersets the original's setup commands," so partial repairs persist (closes GAP 1).
2. **`artifact_verify.py` + scorer** — make the scored artifact the self-verified one, or drop the
   second gate so the adopted recipe reaches `multi_docker_eval` unchanged (closes GAP 2).
3. **`recipe_repair.py`** — add a deterministic `ConnectionError → runtime_preparation_commands`
   service-start repair; stop `_select_runtime_preparation_commands_for_eval` from filtering
   verified daemon starts (closes GAP 3).
4. **`artifact_verify.py:389`** — verify **all** recipe `test_commands`, not just one (closes GAP 4).
5. Port the infra fast-break, postgres host-alias, and pip-constraints threading (GAPs 5–6).
