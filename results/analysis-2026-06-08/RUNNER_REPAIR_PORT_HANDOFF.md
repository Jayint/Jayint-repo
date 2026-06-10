# Handoff: Repo2Run repair loop ported into the RAT runner (`--repair-mode runner`)

**Date:** 2026-06-08 · **Status:** port complete, deployed, 3 runs banked. Investigation paused for analysis.

---

## 1. What was built

A standalone, **verbatim** port of Repo2Run's runner-side build→test→repair loop into the RAT runner,
as a clean A/B reference to isolate a suspected DockerAgent perf regression (suspect = the agent-side
modular repair rewrite in `src/recipe_repair.py` + `src/artifact_verify.py`).

- **`repo2run_repair_port.py`** (repo root, 2825 lines) — byte-for-byte copies of Repo2Run's repair
  fns (deterministic + trajectory-aware LLM repair + infra short-circuit). Imports **nothing** from
  `src/recipe_repair.py` / `src/artifact_verify.py` (the suspect), so the A/B shares zero code with it.
  Verified: 5 core fns byte-identical to source; import-isolation clean.
- **Glue** (only non-verbatim code): `junit_to_pytest_results` (emits the RAT scorer's
  `run_pytest_results.json` schema), `real_test_command` (strips `--collect-only`), `_repair_and_rescore`
  (loads agent trajectory, runs the verbatim loop on the shipped `eval_build/Dockerfile`, writes results
  unconditionally, never raises).
- **Runner wiring** (`run_rat_benchmark.py`): `--repair-mode {runner|selfverify|both|off}` (default
  `selfverify`) + `--repair-rounds`; gated injection in `_run_one`; env `DOCKERAGENT_REPAIR_MODE`
  threaded to child/worker procs and to the adapter (toggles the agent self-verify off under `runner`).
- **`scripts/compute_essr.py`**: added `repaired_count` / `repaired_pass_rate` columns.

**Commits** (branch `rat-bench-integration`, local — not pushed):
- `9d3ce7e` feat: standalone verbatim repo2run repair port + `--repair-mode runner`
- `b2e09cb` fix: load agent trajectory from `DOCKERAGENT_ROOT/workplace`, not `root_path`
- `ac9604f` fix: fire repair on `build_failed` repos that kept `eval_build/Dockerfile`

**Tests:** `tests/test_repo2run_repair_port.py` (45) + `tests/test_compute_essr.py` (14) +
existing `test_artifact_verify.py`/`test_recipe_repair.py` (38) — all green.

> ⚠️ **`deploy.sh` is untracked but was patched** (`|| true` on the now-empty untracked-glob; `set -e`
> killed it after the src/tests files got committed). Keep that fix or `./deploy.sh` aborts silently.
> Also note: `repo2run_repair_port.py` is at repo ROOT, so deploy.sh only ships it once committed.

---

## 2. Two bugs the smoke/runs caught and fixed

1. **Trajectory path** (`b2e09cb`): loader used `{root_path}/workplace/...` but the agent writes to
   `{DOCKERAGENT_ROOT}/workplace/multi_docker_eval_<slug>/agent_run_summary.json` (cwd-relative,
   `multi_docker_eval_adapter.py:758`). Wrong path → `successful_actions` empty → trajectory repair
   silently degraded. **Fixed** → confirmed live: 37/37 trajectory loads, 0 empty-trajectory warnings.
2. **Repair gate** (`ac9604f`): `status != "error"` skipped all error repos, but 15/50 `build_failed`
   repos kept `eval_build/Dockerfile` and are eligible. Gate now keys on Dockerfile existence.

---

## 3. The three banked runs (raw artifacts)

All on the VM `root@167.233.64.96:/opt/rat-bench-integration/` (and locally synced if pulled):

| run dir | mode | coverage | ÷all (paper) | ÷exec | full-pass |
|---|---|---|---|---|---|
| `rat_run_runner2` | runner (gate-fixed) | 28/50 = 0.56 | 0.2142 | 0.3825 | 5 |
| `rat_run_runner`  | runner (first)      | 24/50 = 0.48 | 0.1892 | 0.3942 | 5 |
| `rat_run_dockeragent` | selfverify (banked, older code) | 32/50 = 0.64 | 0.2387 | 0.3729 | 3 |

Config: `--llm deepseek/deepseek-v4-flash --concurrency 12 --num-turn 30`, dataset
`datasets/rat_python_hard_subset.json` (50 repos). Recompute via
`python3 -c "import sys;sys.path.insert(0,'scripts');from compute_essr import score_agent;print(score_agent('<dir>'))"`.

---

## 4. Key findings (for your analysis)

1. **The repair port works.** Fires on hollow envs, loads the trajectory, and the LLM **restores the
   dropped `pip install -e .`** — verified live (e.g. `mcp-atlassian` round-1 input carries the editable
   install; ~9/21 repaired repos in run-1 ended passing).
2. **Repair does NOT recover `build_failed` repos.** Even with the gate fix firing repair on all 15
   (37 repairs, 37/37 trajectory), **recovered-from-error = 0**. Those are structural failures (native
   libs / services), out of scope for trajectory repair. The gate fix is correct but inert for coverage.
3. **The metric is COVERAGE-driven, not repair-driven.** ÷all is dominated by how many repos `predict`
   produces a buildable env for (~40% fail with `build_failed`/`no_dockerfile`). Repair only helps
   *hollow-success* repos.
4. **Single-run A/B is noise-limited.** Two *identical-config* runner runs differ by 0.025 on ÷all
   (coverage 0.48 vs 0.56) purely from agent (deepseek) non-determinism — **as large as the gaps between
   arms.** `recovered-from-error=0` proves the 24→28 coverage swing was variance, not the gate fix.
   ⇒ The regression cannot be isolated from one run per arm.

**Implication:** if a regression exists, it most likely lives in the **agent/synthesizer predict-success
rate**, not the repair loop. Repair is a working but secondary backstop.

---

## 5. Paths not taken (if you resume)

- **Multi-run A/B** (≥3× each off/selfverify/runner) → error bars to beat the ±0.025 noise floor.
- **Predict-failure characterization** → why ~40% of repos fail `predict` (`build_failed`/`no_dockerfile`
  causes), and whether `selfverify` vs `off` changes that rate. This is the real coverage lever.
- **`no_dockerfile` repos (10/50)** have a recipe but no Dockerfile → unrepairable by the verbatim loop
  (it operates on a Dockerfile). Would need recipe→Dockerfile rendering (out of scope for the port).

Error-cause breakdown (run-1): 15 `build_failed`, 10 `no_dockerfile`, 1 `docker_timeout`.
