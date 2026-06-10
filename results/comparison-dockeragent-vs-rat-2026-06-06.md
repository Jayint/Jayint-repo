# DockerAgent vs RAT — 50-repo Python hard subset (2026-06-06)

Same dataset (`datasets/rat_python_hard_subset.json`), same model
(`deepseek/deepseek-v4-flash` via OpenRouter, Alibaba provider pin), same harness + scorers.

| Metric | DockerAgent (K=8) | RAT (K=12) | Definition |
|---|---|---|---|
| rows | 50 | 50 | repos evaluated |
| `status=success` | 28 | 50 | model produced a setup the harness accepted (NOT test-passing) |
| `success` (scorer bool) | 28 | 50 | == status=success here |
| `pytest_collect_success` | 10 | 36 | pytest could import/collect the repo's tests |
| `pass_rate > 0` | 13 | 24 | at least one test passed |
| `pass_rate == 1.0` | 5 | 23 | ALL tests passed (strict "fully resolved") |
| mean `pytest_pass_rate` | 0.195 | 0.462 | mean fraction of tests passing |
| mean `pass_rate_exclude_code_issues` | 0.206 | 0.462 | same, excluding code-issue failures |

## Caveats
- **`status=success` overstates capability.** It means the model finished its setup
  process and the harness accepted it — not that tests passed. 14 of RAT's 50 "successes"
  could not even collect tests.
- **Use `pytest_pass_rate` / `pass_rate==1.0` for the real comparison.** On the strict
  fully-resolved bar: RAT 23/50 vs DockerAgent 5/50 (~4.6x).
- **Concurrency differs** (RAT K=12 vs DockerAgent K=8). Verified 0 timeouts / 0 infra
  failures on both, so K shouldn't bias correctness — but for a perfectly controlled
  head-to-head, re-run DockerAgent at K=12.
- DockerAgent's 22 errors were all genuine (11 build_failed + 11 no_dockerfile), 0 infra.
  RAT reported 0 errors (it produces *some* setup for every repo, hence the loose `status`).

## Raw artifacts
- DockerAgent: `results/dockeragent/2026-06-06-k8/` (rat_results.json + 50 result rows + run.logs)
- RAT: `results/rat/run-2026-06-06-k12/` (rat_results.json + 50 result rows + run.logs)
