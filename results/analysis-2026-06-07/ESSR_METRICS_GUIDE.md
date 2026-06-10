# ESSR metrics guide — reading `scripts/compute_essr.py` output

Deterministic recompute (no LLM) of RATBench metrics from raw `run_pytest_results.json` +
`run_pytest_collect_results.json`, faithful to the official scorer
(`eval/common/scorers.py`) and report (`eval/report/generate_latex_report.py`), cross-checked
against the paper (arXiv:2604.23190). Every per-repo value is cross-checked against the stored
`_result_row.json`: **all three agents match 50/50 (pass-rate + collect)**.

## The table (2026-06-07 hard-50, deepseek-v4-flash)

```
agent          n  exec  cover  ESSR(÷exec)    ÷all   micro  collect÷all collect÷exe  hollow  fullpass
dockeragent   50    32   0.64       0.3729  0.2387  0.8453        0.30       0.4688       5         3
rat           50    46   0.92       0.6775  0.6233  0.8152        0.68       0.7391       6        16
repo2run      50    31   0.62       0.6322  0.3919  0.7315        0.54       0.8710      10         5
```

## What each field means

| Field | Formula | Plain meaning |
|---|---|---|
| **agent** | — | Which method. Same scorer for all. |
| **n** | count of `_result_row.json` | Target repos scored. |
| **exec** | count where `run_pytest_results.json` exists & parses | Repos that produced a real pytest results file (`pytest_executed`). Reach, not quality. |
| **cover** | `exec / n` | Fraction executed. |
| **ESSR(÷exec)** | `Σ pass_rate(executed) / exec` | **Official headline.** Per-repo pass-rate over **only executed** repos — flatters low-coverage agents. |
| **÷all** | `Σ pass_rate(all) / n` | **Paper-faithful** (`Npass/Nverified`): failures count as **0**. Report this. |
| **micro** | `Σ passed / Σ (total−skipped)` over executed | Test-weighted pass rate — big repos dominate. |
| **collect÷all** | `count(collect_success) / n` | **Collection-success rate**: `pytest --collect-only` succeeded (the test imports resolve). Binary per repo (`run_pytest_collect_results.json["success"]`). |
| **collect÷exec** | `count(collect_success) / exec` | Collection success among executed repos. |
| **hollow** | count where `collect_success` and `pass_rate < 0.5` | Repos that **collect but don't pass** — imports resolve, runtime doesn't. |
| **fullpass** | count where `pass_rate ≥ 0.999` | Clean 100%-pass wins. |

## Per-repo pass-rate (the building block)
```
effective_total = total_tests - skipped
pass_rate       = passed / effective_total   (if effective_total > 0, else 0.0)
pass_rate_exclude_code_issues = passed / (passed + ModuleNotFoundError + ImportError)
pytest_executed = run_pytest_results.json exists AND parses
collect_success = run_pytest_collect_results.json["success"]
```

## Key relationships
**1. `÷all = ESSR(÷exec) × cover`** (exactly): RAT `0.6775×0.92=0.6233`; R2R `0.6322×0.62=0.3919`;
DA `0.3729×0.64=0.2387`. The gap between `÷exec` and `÷all` **is** the coverage penalty.

**2. macro (`÷all`/`÷exec`) vs `micro`**: macro weights every repo equally; micro pools all tests
(big repos dominate). DA is worst on macro `÷all` (0.24) but best on `micro` (0.85) → many
small/zero repos drag the per-repo average down while a few large executed repos pass.

**3. collect vs pass (the hollowness signal)** — collection success minus pass-rate per agent:
- **RAT 0.68 → 0.62 (gap 0.06):** when it collects, it almost always passes. Honest environments.
- **Repo2Run 0.54 → 0.39 (gap 0.15), hollow=10:** the *most hollow* — its waiting/conflict-list dep
  machinery resolves imports (collect÷exec 0.87, best) but resolved imports ≠ runtime correctness.
- **DockerAgent 0.30 → 0.24 (gap 0.06):** small gap, but both numbers low — weak across the board,
  not merely "collect-rich, pass-poor".

**Takeaway:** even on the lenient **collect-only** metric, DockerAgent ranks **last** (0.30 vs RAT
0.68, R2R 0.54). Grading on collection instead of pass-rate does NOT rescue it. The collect-only
view actually *vindicates* the paper's pass-rate choice: same ranking, while exposing that a third
of Repo2Run's collected envs are hollow. (Did the paper define a collect-only metric? No — it
explicitly calls import/collection checks inadequate, l.344, and scores `Npass/Nverified`.)

## Two inflation vectors in the OFFICIAL code (both rejected here)
1. **`÷executed` denominator** — excludes setup-failure repos; the paper counts them as 0. Inflates
   low-coverage agents most (Repo2Run +24 pts headline vs ÷all).
2. **All-timeout → pass_rate 1.0 phantom** — the official scorer credits a repo with 0 tests and only
   `TimeoutError` as a full pass. Your honest scorer (patch 0003) and the paper (`Npass=0 → 0`) reject
   it. `compute_essr.py` drops both timeout phantoms; this corrected Repo2Run's headline from a
   phantom-inflated 0.70 to **0.6322** (2 repos: docling, websockets).

## Fidelity audit (code-reviewer)
`compute_essr.py` was adversarially audited against the official `scorers.py` /
`generate_latex_report.py`. Confirmed faithful: core pass-rate, both phantom removals, exclude-code
formula, executed-on-corrupt-JSON, collect-success, aggregate ÷exec, rounding order. One latent gap
found & fixed: a `null`-valued summary field must raise `TypeError` → `executed=False` (matching the
official scorer) rather than being coerced to 0 and counted as executed. No effect on current data
(still 50/50); robustness only.

## How to use
- **Honest ranking:** `÷all` + `cover` → RAT 0.62 ≫ Repo2Run 0.39 > DockerAgent 0.24.
- `ESSR(÷exec)` = "conditional on configuring" (hides coverage). `collect÷all` = "did imports resolve".
- `hollow` = environments that import but don't run. `micro` = big-repo-dominated pooled rate.

## Reproduce
```
python scripts/compute_essr.py \
  dockeragent=results/dockeragent/2026-06-07-baseline \
  rat=results/rat/2026-06-07-corrected \
  repo2run=results/repo2run/2026-06-07-repo2run
```
Now wired into `run_rat_benchmark.py:aggregate()` (prints paper-faithful ÷all + coverage + the
cross-check after every run). Full dump: `results/analysis-2026-06-07/essr_recompute.json`.
