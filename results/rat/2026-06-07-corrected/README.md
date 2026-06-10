# RAT baseline — corrected run (2026-06-07)

Authoritative RAT baseline on the curated 50-repo hard subset, with the **patched honest scorer**.

## Headline
- **ESSR (paper macro, ÷ executed) = 0.6775** (46/50 executed)
- coverage-penalized (÷50) = 0.6233 · micro pooled = 0.9617

## Config (identical to the DockerAgent baseline for head-to-head)
- Agent: **RAT** (`--model rat`, the paper's `RATModel`)
- Model: `deepseek/deepseek-v4-flash` (OpenRouter, Alibaba provider pin)
- Dataset: `datasets/rat_python_hard_subset.json` (50 repos)
- `--concurrency 12 --num-turn 30 --timeout 7800`, `RAT_PYTEST_TIMEOUT=1800`
- Harness: paper code (byte-identical) + the `/repo` path fix (0001) + metric patches
  0002 (pytest timeout 180→1800), 0003 (no timeout→1.0 phantom), 0004 (recursive results
  glob), 0005 (language detect). RAT `num_turn` → SetupAgent `max_turn=30`.

## Contents
- `output/<org>/<repo>/` — per repo: `_result_row.json` (scored row), `run.log`,
  `junit_report.xml`, `run_pytest_results.json`, `construct_test_result.json`, `_meta.json`,
  `trajectory.json`, etc.
- `rat_results.json` — aggregate of all 50 rows.
- `_run50_rat_corrected.log` — scheduler log.
- `essr_per_repo_corrected.csv` — per-repo pytest_pass_rate / exclude_code / executed.

## Provenance
Pulled 2026-06-07 from `root@167.233.64.96:/opt/rat-bench-integration/rat_run_rat_corrected/`.
Source run PID 917900. Fidelity + method validation: `../../RAT_BASELINE_FIDELITY_REPORT.md`.
Head-to-head vs DockerAgent: `../../HEADTOHEAD_DOCKERAGENT_VS_RAT.md`.
