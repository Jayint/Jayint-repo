# DockerAgent baseline (2026-06-07)

Authoritative baseline of **our agent** on the curated 50-repo hard subset, with the
**patched honest scorer** — directly comparable to the RAT corrected run.

## Headline
- **ESSR (paper macro, ÷ executed) = 0.3729** (32/50 executed)
- coverage-penalized (÷50) = 0.2387 · full-pass (≥0.999) = 3
- vs RAT 0.6775 (46/50) — RAT ~1.8×. Per-repo: RAT-only wins 14, DA-only 0, both-pass 10.

## Config (identical to the RAT baseline for head-to-head)
- Agent: **DockerAgent** (`--model dockeragent`, our `agent.py` via `DockerAgentModel` shim)
- Model: `deepseek/deepseek-v4-flash` (OpenRouter, Alibaba provider pin)
- Dataset: `datasets/rat_python_hard_subset.json` (50 repos)
- `--concurrency 12 --num-turn 30 --timeout 7800`, `RAT_PYTEST_TIMEOUT=1800`
- Same harness/scorer as the RAT run. DockerAgent `num_turn` → `agent.run(max_steps=30)`.
- Dockerfile **repair loop active + working** (fired 41/50, resolved 28) — verified, see
  `../../DOCKERAGENT_RUN_SANITY.md`.

## Contents
- `output/<org>/<repo>/` — per repo: `_result_row.json`, `run.log`, `junit_report.xml`,
  `run_pytest_results.json`, `construct_test_result.json`, `_meta.json`, `trajectory.json`, etc.
- `rat_results.json` — aggregate of all 50 rows.
- `_run50_dockeragent.log` — scheduler log.

## Provenance
Pulled 2026-06-07 from `root@167.233.64.96:/opt/rat-bench-integration/rat_run_dockeragent/`.
Source run PID 1223709. Readiness audit: `../../DOCKERAGENT_BASELINE_READINESS.md`.
Sanity/anomaly verification: `../../DOCKERAGENT_RUN_SANITY.md`.
Head-to-head: `../../HEADTOHEAD_DOCKERAGENT_VS_RAT.md`.
