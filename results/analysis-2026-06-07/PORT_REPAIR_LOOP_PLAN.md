# Plan: port the Repo2Run repair loop into the RATBench runner (DockerAgent)

> **Revised 2026-06-08.** Reframed as a **standalone-verbatim port for a clean A/B diagnostic**.
> See the fidelity audit `results/analysis-2026-06-08/REPAIR_LOOP_PORT_FIDELITY_AUDIT.md` for the
> exact gap list this port must close. Key changes from the original draft: (a) **do NOT reuse**
> `src/recipe_repair.py` / `src/artifact_verify.py` — copy repo2run's functions verbatim into a new
> standalone module; (b) the deterministic editable-install enhancement is **DEFERRED to v2**;
> (c) §4 naming made consistent with repo2run's actual function names.

Goal: give the RATBench scoring path the same build→test→repair loop that lets the Repo2Run
benchmark runner recover dropped installs — with its three winning properties: (1) **score the last
repaired artifact unconditionally**, (2) **trajectory-aware LLM repair** that restores dropped
`pip install -e .`, (3) **validate the SHIPPED artifact**. Estimated ceiling from repair alone:
÷all 0.24 → ~0.45–0.50 (see REPAIR_RECOVERABILITY.md).

**Primary purpose — isolate the regression.** DockerAgent perf has dropped vs the original Repo2Run
runner. The prime suspect is the **agent-side modular repair rewrite** (`agent.py:_self_verify_and_repair`
+ `src/recipe_repair.py` + `src/artifact_verify.py`), which replaced the original runner-side repair.
This port re-creates Repo2Run's runner-side repair **verbatim** in the RAT runner and runs it with the
agent-side repair **toggled OFF**, so the A/B answers one question: *is the drop caused by the modular
rewrite?* For that answer to be valid, the port must share **zero code** with the suspect modules.

## 1. Where it plugs in
`run_rat_benchmark.py` flow today (lines ~190–215): `out = model.predict(full_name)` runs the agent,
the **external multi-docker-eval framework** builds + tests, writing `output/<full_name>/`:
`run_pytest_results.json`, `run_pytest_collect_results.json`, `eval_build/Dockerfile`,
`<org>__<repo>.json` (recipe). Then `success_scorer/pytest_collect_scorer/pytest_pass_rate_scorer`
parse those files into the row.

**Injection point:** a new `_repair_and_rescore(out, root_path, full_name, ...)` called **after
`predict()` and before the scorer block** (run_rat_benchmark.py:~208). It re-builds + re-tests the
agent's emitted Dockerfile, repairs on failure, and **overwrites `run_pytest_results.json` /
`run_pytest_collect_results.json`** so the existing scorers (unchanged) pick up the repaired result.
This mirrors the Repo2Run runner exactly: repair is eval-side and coupled to scoring.

## 2. Architecture decision — self-contained, runner-owned loop
The repair loop **owns its own docker build + test** (like the Repo2Run runner; unlike the agent
self-verify which is decoupled). It operates on the **agent's shipped eval Dockerfile**
(`eval_build/Dockerfile`, already self-contained with `RUN git clone {repo_url} && git checkout
{base_commit}` — adapter `_build_eval_dockerfile`:473), NOT a re-rendered clean-room — this fixes
the GAP-2 "validates a different image than the scorer" problem.

Rejected alternatives:
- *Hook into the framework* — the build+test+results parser live VM-side in multi-docker-eval; brittle
  to intercept.
- *Put it in the agent self-verify* — that's the in-progress migration, but it's structurally harder
  (must earn unconditional-adopt + trajectory threading) and the user asked for the runner port. Do
  this first; it's the proven, lower-risk path.

## 3. Components — copy verbatim / build new glue

> **Standalone-verbatim constraint.** The runner-side repair MUST live in a **new standalone module**
> (e.g. `repo2run_repair_port.py` at repo root, imported by `run_rat_benchmark.py`) that is a verbatim
> copy of Repo2Run's repair functions. It MUST NOT import from `src/recipe_repair.py` or
> `src/artifact_verify.py` — those are the **suspect** under test, and sharing any code defeats the A/B.
> Use the audit `results/analysis-2026-06-08/REPAIR_LOOP_PORT_FIDELITY_AUDIT.md` as the spec sheet of
> exactly what to copy.

**COPY VERBATIM from `run_repo2run_benchmark.py` into the standalone module (no `src/` imports):**
- The loop skeleton (3398–3530): `for attempt in range(max_rounds+1): build → test → if effective break;
  deterministic repair else LLM repair; **write+score the last attempt regardless of resolved**`.
  (Only the loop's *I/O endpoints* are rebound to the RAT runner's artifacts — see BUILD NEW #4.)
- `repair_dockerfile_for_missing_python_modules` (2725) + helpers `_requirement_for_missing_module`
  (2644), `_dockerfile_already_installs_requirement` (2675), `_preferred_pip_invocation_for_dockerfile`
  (2666) — the deterministic Dockerfile-text repair. **Copy as-is — no editable enhancement (see #3).**
- `build_dockerfile_repair_input` (2973) + `DOCKERFILE_REPAIR_SYSTEM_PROMPT` (54) +
  `DOCKERFILE_REPAIR_USER_PROMPT` (77) — the **trajectory-aware** LLM repair: feed
  `build_recipe.build_commands` + `successful_actions` (+ `failed_actions`) so the LLM can restore the
  dropped `pip install -e .` (Rule 5/11). Single most important piece — recovers the editable cases the
  modular agent repair can't (audit H3/H5: the rewrite never forwards `successful_actions`).
- `repair_dockerfile_with_llm` (3044) + `extract_dockerfile_repair_json` (2952) +
  `truncate_for_repair_prompt` (2892).
- `docker_build_failed_due_to_unavailable_daemon` (245) — infra short-circuit (don't burn an LLM round
  on a dead daemon / OOM).
- The effectiveness check inside `evaluate_built_image` (2823) + `derive_verification_commands` (2421) —
  copy verbatim so classification matches Repo2Run exactly (do **NOT** use
  `src/artifact_verify.py:classify_test_execution`, which the rewrite altered).

**BUILD NEW (the glue — the only non-verbatim code; keep minimal so it can't pollute the A/B):**
1. **`junit_to_pytest_results(junit_xml_or_stdout) -> dict`** — parse a real pytest run into the
   scorer's exact `run_pytest_results.json` schema: `{summary:{total_tests,passed,failed,skipped,
   errors,xfailed,xpassed}, error_breakdown:{ExcName:count}, returncode, raw_output, parse_method}`.
   Add `--junitxml=/tmp/report.xml` to the test command and `docker cp` it out (or parse stdout).
   **Must match the framework's `error_breakdown` keys** (ModuleNotFoundError, ImportError,
   ConnectionError, TimeoutError…) so scoring is consistent. ← verify against an existing file.
2. **`real_test_command(recipe) -> str`** — derive a runnable command: take
   `recipe.verified_test_commands`, **strip `--collect-only`**; if none remain, fall back to
   `pytest -q --disable-warnings` (or poetry/uv-prefixed per the recipe's tool). Append
   `--junitxml=...`.
3. **~~Editable-install recovery in the deterministic path~~ — DEFERRED to v2.** Repo2Run's
   deterministic path does NOT do this (audit-confirmed: `_requirement_for_missing_module` has no
   editable guard); it restores editable installs via the **LLM/trajectory** path (Rule 5), which we
   copy verbatim. Adding a deterministic enhancement here would mean A/B-testing "Repo2Run repair +
   our enhancement" rather than "Repo2Run repair", confounding the result. Revisit only **after** the
   A/B isolates the cause.
4. **Build/test/results binding** — rebind the verbatim loop's docker-build + test-exec + results-write
   I/O to the RAT runner's shipped `eval_build/Dockerfile` and `run_pytest_results.json`. This binding
   is glue; the repair *decision* logic above stays byte-for-byte verbatim.

## 3.1 Disposition of the existing agent self-verify — DEPRECATE-IN-PLACE, toggleable (do NOT remove)
Two live repair loops would **double-repair**: the agent self-verifies inside `predict()`
(collect-only-validated, decoupled, adopt-only-on-resolved) and the runner re-repairs after — wasteful
(2× LLM + 2× build loops) and confusing (the self-verify can "resolve" a hollow collect-only env and
ship it, masking what the runner sees). So: **single authoritative repair = the runner loop; toggle the
agent self-verify OFF when the runner loop is on. Keep all code + tests; mark deprecated-in-place.**

**STAYS — used ONLY by the (toggled-off) agent self-verify; the runner loop does NOT import them:**
- `src/recipe_repair.py` and `src/artifact_verify.py` remain in the tree, wired only to
  `agent.py:_self_verify_and_repair`. Under `--repair-mode runner` they are inert (self-verify off).
  The standalone `repo2run_repair_port.py` re-implements every primitive it needs by **verbatim copy**,
  precisely so the A/B reference shares no code with these suspect modules. (Original draft said the
  runner loop would *reuse* these — that was reversed on 2026-06-08 for A/B integrity.)

**DEPRECATED-IN-PLACE — toggle off by default, keep the code + tests, add a deprecation note:**
- `agent.py:_self_verify_and_repair` (orchestration call sites ~971/995).
- `src/artifact_verify.py:verify_and_repair_recipe` (the decoupled, adopt-on-resolved orchestrator).
- `src/recipe_repair.py:repair_recipe_with_llm` — superseded by the runner's trajectory-aware LLM repair;
  retained (still used by the legacy self-verify path when toggled on).

**Toggle mechanism — reuse the EXISTING flag, don't invent a new one:**
- `DockerAgent(enable_post_synthesis_repair=...)` already gates the self-verify
  (agent.py:131 default `True`; guard at agent.py:1175). The standalone CLI already exposes
  `--disable-post-synthesis-repair` (agent.py:2060).
- **Thread it through the adapter:** `multi_docker_eval_adapter.py:764` constructs `DockerAgent(...)`
  *without* the flag (so it defaults on). Add `enable_post_synthesis_repair=<from config>` there,
  reading an env var (e.g. `DOCKERAGENT_REPAIR_MODE`).
- **`run_rat_benchmark.py`: add `--repair-mode {runner|selfverify|both|off}`** (default `selfverify`
  until the runner loop is validated, then flip default to `runner`). It both sets
  `DOCKERAGENT_REPAIR_MODE` (read by the adapter to set `enable_post_synthesis_repair`) and decides
  whether `_repair_and_rescore` runs:
  | mode | agent self-verify | runner loop | use |
  |---|---|---|---|
  | `runner` | **off** | **on** | authoritative / recommended end-state |
  | `selfverify` | on | off | current behaviour / legacy |
  | `both` | on | on | debug-compare only (expect double work) |
  | `off` | off | off | clean baseline for A/B |

**Deprecation hygiene:**
- Add a one-line deprecation banner to `artifact_verify.py`'s module docstring and a comment at
  `agent.py:_self_verify_and_repair`: *"DEPRECATED in favour of the runner-side repair loop
  (run_rat_benchmark.py:_repair_and_rescore); retained + toggleable via enable_post_synthesis_repair /
  --repair-mode. Do not extend — port improvements to the runner loop."*
- Keep `tests/test_artifact_verify.py` + `tests/test_recipe_repair.py` green (the primitives are still
  used by the runner loop; the orchestrator tests still cover the toggled-on legacy path).
- **Don't collapse to one path yet.** Both coexist behind the toggle so we can A/B: `off` (baseline
  ~0.24) vs `selfverify` (current, weak) vs `runner` (target ~0.45–0.50). Permanent retirement of the
  self-verify is a later, separate cleanup once `runner` is proven.

## 4. The repaired-run loop (pseudocode)
```
# All repair fns below are VERBATIM copies from run_repo2run_benchmark.py living in
# repo2run_repair_port.py (zero src/ imports). Only the build/test/write I/O is glue (BUILD NEW #4).
def _repair_and_rescore(out, root_path, full_name, llm, model, max_rounds=2):
    d = output_dir(root_path, full_name)
    recipe = load(f"{d}/{slug}.json"); results = load(f"{d}/run_pytest_results.json")
    if effective(results): return out                      # already runs+passes → nothing to do
    dockerfile = read(f"{d}/eval_build/Dockerfile")        # the SHIPPED artifact
    test_cmd   = real_test_command(recipe)                 # glue: strip --collect-only, +junitxml
    run_summary = out.get("agent_run_summary") or {}       # carries successful_actions/failed_actions
    repair_input_base = build_dockerfile_repair_input(recipe, run_summary)   # VERBATIM (trajectory-aware)
    for attempt in range(max_rounds+1):
        build = docker_build(dockerfile, tag)              # glue I/O; classify via verbatim helpers
        if build.failed and not docker_build_failed_due_to_unavailable_daemon(build): test=None
        else: test = run_test_in_container(tag, workdir, runtime_prep, test_cmd)   # glue I/O
        pr = junit_to_pytest_results(test)                 # NEW glue → scorer format
        write(f"{d}/run_pytest_results.json", pr)          # UNCONDITIONAL: last attempt is scored
        if effective(pr): break                            # effectiveness check = VERBATIM repo2run
        if attempt == max_rounds: break
        missing = extract_missing_modules(test.output)     # VERBATIM
        dockerfile = repair_dockerfile_for_missing_python_modules(dockerfile, missing, ws) \
                     or repair_dockerfile_with_llm(llm, model, repair_input_base+failure).dockerfile  # VERBATIM
        remove_image(tag)
    return out
```
Bounded (`max_rounds=2`, 3 builds), never raises (degrade to original results on any error),
deterministic-before-LLM, scores the best attempt regardless of resolution.

## 5. Edge cases & risks
- **Concurrency (12 workers):** unique `image_tag` per repo (`dockeragent-repair-<slug>-<pid>`); always
  `remove_image` in finally. Cap repair concurrency or gate by free disk.
- **Disk OOM** (already caused ~4 false zeros): check `free_disk_gb` before a rebuild; skip+log if low
  rather than crashing the wave. Treat as infra short-circuit.
- **Scoring-format fidelity:** the new junit parser MUST reproduce the framework's
  `summary`/`error_breakdown` exactly, else repaired rows diverge from un-repaired ones. Validate by
  parsing an existing container run and diffing against the on-disk `run_pytest_results.json`.
- **Don't regress passers:** only run the loop when `not effective(results)` (hollow/0). Never touch a
  repo that already runs.
- **Resume marker:** `done_marker = run_pytest_results.json` (run_rat_benchmark.py:167) — overwriting it
  is fine, but write an `_repaired.json` sidecar + a `repair_rounds` field so re-runs/ESSR can see what
  was repaired (and `compute_essr.py` can report repaired-vs-raw).
- **base_commit / repo_url:** present in the recipe (`repo_url`) + dataset (`base_commit`); the eval
  Dockerfile already bakes the clone+checkout, so reuse it directly.
- **collect-only strip producing a too-broad command:** prefer the agent's verified command minus
  `--collect-only`; only fall back to bare `pytest` if nothing runnable remains.

## 6. Phased TODO (TDD)
0. **`repo2run_repair_port.py` — verbatim copy** of the repair fns listed in §3 (deterministic + LLM +
   trajectory input + prompts + infra short-circuit + effectiveness check). Standalone: **no `src/`
   imports**. RED: a test asserting the module imports nothing from `src/recipe_repair`/`src/artifact_verify`.
1. **`junit_to_pytest_results` + fixtures** — RED: feed a sample junit/pytest stdout, assert the exact
   scorer schema + error_breakdown. Validate against 2–3 real on-disk `run_pytest_results.json`.
2. **`real_test_command`** — RED: collect-only → stripped real command; poetry/uv prefix preserved;
   empty → fallback.
3. **~~Editable-install recovery~~ — DEFERRED to v2** (see §3 BUILD NEW #3). Not part of the faithful
   port; recovery comes from the verbatim trajectory-aware LLM path. Do **not** implement for the A/B.
4. **`_repair_and_rescore` loop** (mock build/test like `tests/test_artifact_verify.py`) — RED:
   hollow→deterministic→resolved; hollow→llm-editable→resolved; unconditional write of last attempt;
   infra short-circuit; never-raise.
5. **Port the LLM repair input/prompt** + a unit test that the trajectory (`successful_actions`,
   `failed_actions`) reaches the payload (audit H3/H5 — the modular rewrite's missing signal).
6. **Wire into `run_rat_benchmark.py`** after predict(); add `--repair-rounds` (default 2) and
   **`--repair-mode {runner|selfverify|both|off}`** (default `selfverify` → flip to `runner` once
   validated). Thread `DOCKERAGENT_REPAIR_MODE` → `multi_docker_eval_adapter.py:764`'s `DockerAgent(...)`
   to set `enable_post_synthesis_repair`. Add the deprecation banners (§3.1) to `artifact_verify.py`
   and `agent.py:_self_verify_and_repair`. Confirm `tests/test_artifact_verify.py` +
   `tests/test_recipe_repair.py` still green.
7. **`compute_essr.py`**: surface a `repaired` column / count so the paper-faithful table shows
   repaired-vs-raw lift.

## 7. Validation plan
- **Offline replay (no agent re-run):** the runner can rebuild the on-disk `eval_build/Dockerfile`
  for the ~22 DA-loss repos and run the loop — measure how many flip to effective + the ÷all lift.
  This mirrors Repo2Run's `--reuse-existing-workplace` and lets us tune repair cheaply.
- Target the 5 audit-confirmed (D-FINE, copier, django-oauth, mcp-atlassian, darts) first → expect
  ÷all ~0.34; then the editable bucket (mcpo, verifiers, yutto…) once trajectory-aware LLM repair lands.
- Full 50-repo re-run (with adequate disk) → compare ÷all before/after via `compute_essr.py`.

## 8. Open questions to resolve before coding
1. **Confirm the framework's `error_breakdown` derivation** (exact exception-name keys + how it counts)
   so `junit_to_pytest_results` matches — read one container's junit + the resulting on-disk file.
2. **Where does `eval_script` get the junit?** Today it's `pytest --collect-only` with no `--junitxml`;
   confirm we add `--junitxml` and `docker cp` the report out, vs. parsing stdout.
3. **Does the runner process have Docker + network** (git clone) in the scoring context? (It must, since
   the framework builds there — confirm the runner can shell out to docker directly.)
4. **Model for LLM repair:** reuse `--llm` (deepseek-v4-flash) or pin a stronger repair model? (Given
   the model-dependence finding, a stronger repair model may matter more than the loop itself.)

## 9. Note on the bigger picture
This port is the pragmatic, proven recovery path (ceiling ÷all ~0.45–0.50). It does **not** fix:
services/redis (needs GAP-3 runtime-prep service-start), native libs, infra-OOM (needs disk),
or the **model-level install-drop** (under MiniMax-M2.7 installs aren't dropped at all). Run the
controlled same-model A/B in parallel — if the drop is mostly a deepseek artifact, fixing the synthesis
model may beat investing in repair. The repair loop is a robust backstop either way.
