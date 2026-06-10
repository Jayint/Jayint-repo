# Per-instance result structure — `results/dockeragent/2026-06-07-baseline`

(Compared against `results/rat/2026-06-07-corrected`. Both share the same harness/scorer, so
the *scored* files are identical in shape; they differ in the **agent-side** artifacts.)

## Top level of each run dir

```
<run>/
├── README.md                      # headline ESSR, config, provenance
├── rat_results.json               # ARRAY of 50 scored rows (aggregate of every _result_row.json)
├── _run50_*.log                   # scheduler/supervisor log for the whole 50-repo wave
├── essr_per_repo_*.csv            # (RAT dir only) per-repo pass-rate table
└── output/<org>/<repo>/           # one dir per benchmark instance (50)
```

`rat_results.json` row = the per-repo `_result_row.json` (see below). It is the authoritative
scoreboard.

## Per-instance dir: `output/<org>/<repo>/`

### Files present in BOTH agents (the scored / harness side)

| File | Freq (DA / RAT) | Shape | What it tells you |
|---|---|---|---|
| `_result_row.json` | 50 / 50 | object | **THE scored row.** Keys: `status`, `success`, `pytest_collect_success`, `pytest_pass_rate`, `pytest_total_tests`, `pytest_passed`, `pytest_failed`, `pytest_errors`, `pytest_executed`, `pytest_timeout_unverified`, `error_breakdown{}`, `pass_rate_exclude_code_issues`, `_category`, `failure_reason`, `head_sha`. **Primary signal for win/loss.** |
| `_meta.json` | 50 / 50 | object | Run accounting: `pid`, `start_ts`, `end_ts`, `duration_s`, `failure_reason`, `requested_model`, `base_image`, `head_sha`, `free_disk_gb`. |
| `run.log` | 50 / 50 | text (100–300 KB) | Full agent + harness trace. DA: look for `Self-Verify Round N`, `missing=[...]`, `tests did not execute`, `Verification Bundle Rejected/Auto-finalized`, `skipping evaluation`. |
| `run_pytest_results.json` | 32 / 46 | object | Final pytest run. `summary{total_tests,passed,failed,skipped,errors,xfailed,xpassed}`, `error_breakdown{ExceptionName:count}`, `failed_tests[]`, `error_tests[]`, `raw_output`, `returncode`, `parse_method`. **Absent ⇒ tests never executed** (a failure signal in itself). |
| `run_pytest_collect_results.json` | 34 / 44 | object | `pytest --collect-only` result — collection errors surface here before execution. |
| `junit_report.xml` | 0 / 43 | xml | JUnit per-test detail (RAT side; DA stores results only as JSON). |

### Files UNIQUE to DockerAgent (our agent's recipe artifacts)

| File | Freq | What it tells you |
|---|---|---|
| `<org>__<repo>.json` | 50 | **THE DockerAgent recipe** — the richest DA artifact. Keys: `instance_id`, `repo_url`, `language`, `dockerfile` (full text), `eval_script` (the bash that actually runs pytest), `build_success`, `test_success`, `platform`, `setup_scripts{}`, and **`logs{}`** (below). |
| `eval_build/Dockerfile` | 41 | The materialized Dockerfile used by the eval framework (present only when a build was attempted). 9 missing ⇒ build never reached. |

`<org>__<repo>.json → logs{}` subkeys (the DA decision trace — where root-cause lives):
`agent_steps`, `error`, `build_recipe`, `build_recipe_source`, `build_recipe_error`,
`verified_test_command(s)`, `verified_runtime_preparation_commands`,
`verified_post_test_patch_commands`, `filtered_*`/`refined_*`/`dropped_broad_test_commands`,
`artifact_preflight`, **`artifact_repair_rounds`** (the self-verify repair loop),
`test_command_source`, `runtime_preparation_source`, `skip_evaluation`, `platform_support`,
`memory_stats`.

### Files UNIQUE to RAT (reference agent trajectory) — used as the "what RAT did" oracle

| File | Freq | What it tells you |
|---|---|---|
| `outer_commands.json` | 50 | **Ordered list of every shell command RAT ran** in the container (`{command, returncode, time}`) interleaved with `{LLM_time}`. **The gold source for "what RAT did that DA didn't"** (e.g. `redis-server --daemonize yes`, `pip install -e .`). |
| `inner_commands.json` | 50 | Lower-level command stream (per-tool). |
| `trajectory.json` | 50 | Full chat transcript: list of `{role, content, agent}` (≈60 turns). |
| `tool_stats.json` | 50 | Per-tool call counts/timings/return codes (`run-pytest`, `run-pytest-collect`, `stop`, …). |
| `construct_test_result.json` | varies | Tiny marker file (often `File not found`/empty). |

## How to read a win/loss in 3 files
1. `_result_row.json` (both) → the score + `error_breakdown` (e.g. `{"ConnectionError":345}`).
2. DA `<org>__<repo>.json` → `build_success`/`test_success` + `dockerfile` + `logs.error` +
   `logs.artifact_repair_rounds` → **why DA's recipe is broken**.
3. RAT `outer_commands.json` → **the command RAT ran that DA omitted** → the fix.

## Aggregate buckets (this run, 50 repos)
`DA_LOSS` (RAT beat DA) **22** · `BOTH_FAIL` 16 · `PARTIAL_TIE` 8 · `BOTH_PASS` 3 · `DA_WIN` 1.
DA mean pass-rate **0.239** vs RAT **0.623** (ESSR ÷executed: 0.373 vs 0.678).
