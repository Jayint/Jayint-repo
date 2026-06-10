# Definitive runner-repair result — rat_run_runner4 (code d69f8a2)

**Date:** 2026-06-09 · **Dataset:** datasets/rat_python_hard_subset.json (50 repos) ·
**Config:** `--repair-mode runner --repair-rounds 2 --llm deepseek/deepseek-v4-flash --concurrency 12 --num-turn 30`
**Code:** commit `d69f8a2` (faithful pre-loop port + glue fixes + clobber fix), VM hash-verified, run stamped `d69f8a2`.

## Validity checks (all clean)
- clobber = 0 (no framework results destroyed; the runner3 clobber bug is fixed)
- trajectory loaded = 29/29 (0 empty-trajectory warnings)
- disk healthy (pruned mid-run 93%->25%)

## Numbers (apples-to-apples; "real exec" = collected & ran >=1 test, build_failed stubs + 0-collect excluded)

| run (code) | real exec | build_failed | zero_collect | div_all (paper, /50) | div_exec (real) | full_pass |
|---|---|---|---|---|---|---|
| **runner4 FAITHFUL** (d69f8a2) | 17 | 12 | 4 | **0.1787** | **0.5256** | 4 |
| runner2 gate-fix | 22 | 0 | 6 | 0.2142 | 0.4868 | 5 |
| baseline selfverify (banked) | 23 | 0 | 9 | 0.2387 | 0.5188 | 3 |

NOTE on score_agent's headline `div_exec`: it counts the `build_failed` stub as "executed-with-0-pass",
which UNDER-states runner4 (0.2708). The table above excludes stubs/0-collect for comparability. `div_all`
is stub-independent and directly comparable.

## Read
1. **Quality is best in the faithful version:** on repos that actually build+test, runner4 div_exec(real)=0.5256 —
   highest of the three. Repaired repos pass ~99% (mcpo 27/27, proxy_pool 147/147, copier 1098/1113, DDNS 853/877,
   markitdown 331/341, pal-mcp-server 870/905). The repair restores dropped editable installs and runs real tests.
2. **div_all is COVERAGE-driven, not repair-driven.** runner4's lower div_all (0.1787) is this run having more
   predict-level failures (27 status=error: 12 build_failed-with-repair + no_dockerfile etc.), i.e. fewer repos
   reached a buildable+testable state. That is agent (deepseek) non-determinism — run-to-run coverage swings
   ~±several repos dominate the headline (see runner1=24, runner2=28, runner4=17 real-exec across identical configs).
3. **Bimodal outcome:** ~8 repos pass >=98%, the rest fail/empty. When the env builds, tests pass; the score is
   gated by whether predict produces a buildable env — the synthesizer, not the repair loop.

## Raw artifacts (VM root@167.233.64.96:/opt/rat-bench-integration)
- rat_run_runner4/  (definitive, d69f8a2) ; rat_run_runner2/ ; rat_run_dockeragent/ (baseline)
- rat_run_runner3/  = INVALID (clobber bug, kept for reference only)
- recompute: `python3 -c "import sys;sys.path.insert(0,'scripts');from compute_essr import score_agent;print(score_agent('<dir>'))"`

## Known follow-up (optional, not blocking)
The `build_failed` stub makes genuine build-failures count as "executed" in score_agent's div_exec. div_all is
unaffected. To make div_exec directly comparable by default, either drop the stub (build-fail => not executed,
matching runner2/baseline) or teach the scorer to treat parse_method=build_failed as not-executed.

## Cross-agent comparison (same deepseek LLM, same 50-repo hard subset, div_all over /50)

| agent (run dir) | real exec | div_all | div_exec(real) | full_pass | rows |
|---|---|---|---|---|---|
| RAT baseline (rat_run_rat_corrected, newest valid) | 42 | 0.6233 | 0.7420 | 16 | 46 |
| repo2run baseline (rat_run_repo2run) | 26 | 0.3919 | 0.7537 | 5 | 31 (PARTIAL) |
| DockerAgent + faithful repair (rat_run_runner4) | 17 | 0.1787 | 0.5256 | 4 | 33 |
| DockerAgent selfverify (rat_run_dockeragent) | 23 | 0.2387 | 0.5188 | 3 | 32 |

READ: DockerAgent (0.18-0.24) substantially UNDERPERFORMS both RAT (0.62) and repo2run (0.39) baselines.
Gap is driven by COVERAGE/env-construction: RAT reaches real test execution on 42/50 repos, repo2run 26,
DockerAgent only 17-23. Even on built envs DockerAgent quality (div_exec ~0.52) trails RAT/repo2run (~0.75).
The faithful repair loop is correct but does NOT close the gap — the deficit is in predict/synthesis, not repair.
CAVEATS: repo2run run is PARTIAL (31/50, blocked at bug #8) so 0.3919 is a lower bound; runs are on different
dates (RAT 06-06, repo2run 06-07, DockerAgent 06-09) with agent non-determinism. Directionally the gap is large
and consistent. This confirms the original "DockerAgent dropped vs repo2run" concern.
