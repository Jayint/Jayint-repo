# Credit-fix re-run + combined result — corrected DockerAgent score

**Date:** 2026-06-09 · **Code:** `d69f8a2` (identical to `rat_run_runner4`) · **Dataset:** same 50-repo hard subset
**What:** re-ran the **16** `rat_run_runner4` repos whose `run.log` hit an OpenRouter **HTTP 402 (credit wall)**, with credits restored, then **merged the 16 fresh results over runner4's other 34** and re-scored with `scripts/compute_essr.py`.
**Runs (VM `167.233.64.96:/opt/rat-bench-integration`):** `rat_run_creditfix/` (the 16) · `rat_run_combined/` (merged 50) · subset dataset `datasets/rat_python_hard_subset_creditfix.json`.

---

## 1. Headline — the credit wall was a *fairness* confound, not a *score* confound

| agent (same deepseek LLM, same 50 repos) | coverage | **div_all** (÷all, paper-faithful) | ESSR (÷exec) | full_pass |
|---|---:|---:|---:|---:|
| `runner4` (original, 16 repos = 402-killed) | 33/50 | 0.1787 | 0.2708 | 4 |
| **COMBINED (credit-fixed)** | **41/50** | **0.2048** | 0.2498 | 4 |
| RAT baseline (`rat_run_rat_corrected`) | 46/50 | **0.6233** | 0.6775 | 16 |
| repo2run baseline (`rat_run_repo2run`) | 31/50 | **0.3919** | 0.6322 | 5 |

**Fixing the credit wall moves DockerAgent's div_all from 0.1787 → 0.2048 (+0.026).** That is only **~6 % of the 0.44 gap to RAT.** Coverage (repos that reach an "executed" state) rises 33 → 41/50, but **`full_pass` stays at 4** and ESSR(÷exec) actually *falls* (0.27 → 0.25) because most of the newly-executed repos pass few or no tests.

> **The 402 wall denied 16 repos a fair attempt — but with credits restored, 13 of those 16 still fail for genuine env-construction reasons.** The wall was *masking real failures, not hiding successes.* The corrected DockerAgent score (**0.20**) still trails RAT (0.62) and repo2run (0.39) by a wide margin. This **confirms** the earlier diagnosis: the deficit is environment construction (lossy synthesizer + collect-only false-pass), not the credit artifact and not the repair loop.

---

## 2. The 16 repos: before (credit-killed) → after (credits restored)

| repo | runner4 (orig) | creditfix (new) | verdict |
|---|---|---|---|
| `google/Xee` | died @ ImageSelector | **success 26/59 (0.441)** | ✅ now genuinely runs |
| `resend/resend-python` | died @ ImageSelector | **success 257/429 (0.599)** | ✅ now genuinely runs |
| `MemTensor/MemOS` | build_failed (0) | **success 41/154 (0.266)** | ✅ 402 had degraded it; now real |
| `rayai-labs/agentic-ray` | died @ ImageSelector | success 0/17 (0.000) | ⚠ builds+collects, 0 pass |
| `Peterande/D-FINE` | other_error 0/1 | success 0/1 (0.000) | ⚠ collects 1, 0 pass (genuine) |
| `docling-project/docling` | died | **build_failed** (0) | ❌ genuine build failure |
| `epam/ai-dial-sdk` | died | **build_failed** (0) | ❌ genuine build failure |
| `scylladb/scylla-cluster-tests` | died | **build_failed** (0) | ❌ genuine build failure |
| `stlehmann/pyads` | died | **build_failed** (0) | ❌ genuine (RAT solved via C submodule build) |
| `feast-dev/feast` | died | **no_dockerfile** (0) | ❌ agent never converged |
| `frappe/press` | died | **no_dockerfile** (0) | ❌ agent never converged |
| `gip-inclusion/les-emplois` | died | **no_dockerfile** (0) | ❌ agent never converged |
| `GoogleCloudPlatform/slurm-gcp` | died | **no_dockerfile** (0) | ❌ agent never converged |
| `ModelEngine-Group/nexent` | died | **no_dockerfile** (0) | ❌ agent never converged |
| `nomadkaraoke/karaoke-gen` | died | **no_dockerfile** (0) | ❌ agent never converged |
| `aiidateam/aiida-core` | died | **docker_timeout** (0) | ❌ genuine timeout |

**Score:** 3/16 reach real passing tests (Σ essr **+1.306** → +0.026 div_all). 2/16 build+collect but 0-pass. 11/16 still fail at build/dockerfile/timeout. **Zero 402s in the re-run** — the wall is gone (credits: ~$6.73 remaining vs near-zero during runner4; ImageSelector calls cleared the 65,536-token affordability check).

---

## 3. What this means

1. **The corrected, comparable DockerAgent number is `div_all = 0.2048` (code `d69f8a2`, full credits).** Use this, not the contaminated 0.1787, for cross-agent comparison. It is still **~3× below RAT (0.6233)** and below repo2run (0.3919).
2. **The credit wall mattered for fairness, not for the conclusion.** It robbed 16 repos of an attempt, but only 3 were actually solvable by this agent — so it inflated the *failure count* without hiding real capability. (This refines §1–§3 of `DOCKERAGENT_FAILURE_WALKTHROUGH.md`, which correctly flagged the artifact by repo-count but could be read as implying a larger score impact; the measured impact is +0.026.)
3. **The gap is genuine env-construction.** The 34 untouched repos still carry the two architectural failure modes (synthesizer drops/hallucinates the install; `collect-only` false-pass), and the 16 re-runs add 11 fresh genuine build/dockerfile failures of the same kind (e.g. `pyads` build_failed where RAT compiled the C submodule; the no_dockerfile cluster where the agent never converged in 30 turns). The combined run shows **7 hollow-collect repos** (collection succeeds but pass-rate < 0.5) — the collect-only false-pass signature.
4. **`full_pass` 4 vs RAT 16** is the cleanest one-number summary of the remaining gap: on hard repos, RAT fully configures 16 environments; DockerAgent fully configures 4.

---

## 4. Provenance & raw artifacts
- Merged tree (50 repos, scored): `rat_run_combined/` on the VM. The 16 fresh: `rat_run_creditfix/`.
- Scores: `credit_fix_combined_score.json` (this dir) — full `score_agent` output for combined / runner4 / RAT / repo2run.
- Launch (identical flags to runner4): `--model dockeragent --repair-mode runner --repair-rounds 2 --llm deepseek/deepseek-v4-flash --concurrency 12 --num-turn 30`, root `./rat_run_creditfix`, subset dataset of the 16.
- Re-run was the only write; all baseline runs untouched. No `pkill` over SSH (script-file launch).
- Related: `DOCKERAGENT_FAILURE_WALKTHROUGH.md` (the log-level diagnosis this re-run validates).
