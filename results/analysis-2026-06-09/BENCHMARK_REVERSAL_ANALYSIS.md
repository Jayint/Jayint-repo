# Why DockerAgent beats repo2run on Repo2Run-bench but loses on RATBench

**Date:** 2026-06-09 · **Method:** 6 Sonnet agents (workflow `wf_d5a83e78-b03`) grounding the comparison in: `docs/REPO2RUN_BENCHMARK.md`, `outputs/repo2run_benchmark/`, `datasets/repo2run_table15.json` (420), `datasets/rat_python_hard_subset.json` (50), and the per-repo logs on the VM. Raw findings: `benchmark_reversal_findings.json`.

## The two results that need reconciling

| benchmark | metric | DockerAgent | repo2run | winner |
|---|---|---:|---:|---|
| **Repo2Run-bench** (420 repos) | **EBSR** = Dockerfile builds **+ pytest executes** (pass NOT required) | **368/420 (87.6%)** | 361/420 (86.0%) | **DockerAgent +7** |
| **RATBench-hard** (50 repos) | **ESSR** = fraction of tests that **pass** | **0.20** | 0.39 | repo2run (and RAT 0.62) |

Both facts are real. The reversal is explained by **three compounding factors, ranked by how much they actually move the result.**

---

## Factor 1 (dominant) — the two benchmarks are different *repo populations*, and RATBench's was adversarially chosen to break env-construction

**Repo2Run-bench (420)** is a near-monoculture of 2024-era AI/ML/LLM **pip-installable libraries**, pre-selected by the paper to be buildable (86% build by construction):
- 100% Python, language-breakdown not even tracked; **0** multi-language native repos in the metadata.
- **< 3%** have any service/Docker surface (~11/420).
- Its own 59 failures cluster on GPU/CUDA + heavy compiled extensions (FlagGems, VILA, GPTQModel, DeepSpeedFugaku).
- Examples: `kan-gpt`, `CodonTransformer`, `AgentStack`, `byaldi`, `rerankers`, `crewAI-tools`, `fast-graphrag`.

**RATBench-hard (50)** is **adversarially curated** — its own header says it *"over-weights cases where Repo2Run (44.8 ESSR) and RAT (63.2 ESSR) struggle."* It is built from the exact failure modes that break environment construction:
- **22/50 carry non-Python code** (C, Rust, Cython, Go, TypeScript): `dumb-init` {C 23%}, `pynitrokey` {C 21%}, `tesserocr` {Cython 17%}, `yutto` {Rust 17%}, `nginx-proxy` {Go}, `feast` {Go+TS}.
- **12 connection_error_stress** — tests hit live Redis/Postgres/APIs unreachable in a sandbox (RAT's #1 failure, 24.8%).
- **7 native_runtime_stress**, **3 ci_service** (deps invisible to requirements.txt), **11 test_deficient** (no runnable suite), large 5–8 MB monorepos.

**The overlap between the two benchmarks is exactly ONE repo** (`D4Vinci/Scrapling`). So "DockerAgent wins on one, loses on the other" is, before anything else, **two almost-disjoint repo sets** — and RATBench's was hand-picked to contain the repos DockerAgent's architecture can't handle.

> Decisive check: if you re-score RATBench-hard by an **EBSR-equivalent** (build + execute, ignore pass-rate), DockerAgent gets ≈**0.42** vs RAT ≈**0.84** — *the same ~0.42 gap as ESSR.* **Switching the metric does not rescue DockerAgent on the hard set.** It loses because it can't even build+execute these repos — i.e. the repos, not the metric, are the problem.

---

## Factor 2 (the mechanism) — DockerAgent's sandbox→synthesize architecture is faithful on easy repos, lossy on hard ones

DockerAgent runs the agent in a sandbox, then a **synthesizer re-derives a Dockerfile** from the trajectory. On a clean repo whose setup is a single `pip install -e .`, the synthesizer reproduces it perfectly — which is why DockerAgent ties/beats repo2run on the easy 420. On the hard repos the re-derivation drops or mangles steps. The two case studies:

**`sooperset/mcp-atlassian`** (modern `hatchling` + `uv` MCP server, 2739 tests):
- Sandbox did it right: `pip install uv` (Step 10, ✓) → `uv sync --dev` (Step 12, installed 140 pkgs, collected 2739) → `Final Answer: Success`.
- **Synthesizer dropped the `pip install uv` bootstrap** — `build_commands: ["uv sync --dev"]` only. Eval build: `/bin/sh: 1: uv: not found` (exit 127) → **build_failed**.
- repo2run-tool bypassed uv entirely with plain `pip install -e .` → **2563/2739 (0.994)**.

**`stlehmann/pyads`** (Python wrapper over a **C++ git-submodule**, compiled via meson/ninja):
- DockerAgent copied the repo's own `FROM python:${python_version}` but didn't declare the `ARG` → `failed to parse stage name "python:": invalid reference format` → **build_failed**.
- RAT and repo2run both got **114/114 (1.0)** by driving the submodule + meson build.

These repo *types* — uv-packaged MCP servers, native-submodule libs — are a **RATBench specialty**: of the 50, only 1 has a C-submodule build and 4 are MCP; in the Repo2Run-420 they're essentially absent. So DockerAgent's architecture was never stress-tested on them until RATBench.

---

## Factor 3 (amplifier, not the cause of the loss) — EBSR is lenient, so it *flatters* the Repo2Run-bench win

`docs/REPO2RUN_BENCHMARK.md` (verbatim): *"EBSR 不要求测试全部通过。只要测试真正运行起来即可"* — **EBSR counts an environment as success if the build works and pytest runs, even if tests fail (or barely run).** That makes the 87.6% "win" partly a product of a forgiving metric:

**The cross-benchmark control proves it.** `D4Vinci/Scrapling` is the one repo in *both* datasets:
- **Repo2Run-bench: DockerAgent = EBSR success** — but the verified command was `pytest --collect-only` (42 tests *collected*, exit 0). **Zero tests were executed or passed.**
- **RATBench: DockerAgent = pass_rate 0.0** (collection itself fails in the harness). RAT reference = **0.9324** (676/725).

So the *same agent on the same repo* is a "success" under EBSR (collect-only) and a zero under ESSR. EBSR rewards exactly the hollow/collect-only environments that ESSR exposes — which is why the Repo2Run-bench number looks better than the agent's true environment quality.

**But** — and this is the verifier's correction — this leniency explains why the *Repo2Run-bench win looks big*, not why DockerAgent *loses on RATBench*. Within RATBench-hard the metric switch contributes ≈0% of the gap (Factor 1's decisive check). The metric flatters the win; the **repos + architecture** cause the loss.

---

## The reconciled answer

1. **The win is real but flattered.** DockerAgent genuinely beats repo2run's published EBSR (368 vs 361) — but on a benchmark of pre-selected cleanly-installable libraries, scored by a metric (build+execute) that doesn't require tests to pass. Its "successes" there include collect-only/hollow envs (Scrapling).
2. **The loss is about the repos, not the metric.** RATBench-hard is a different, adversarially-chosen population (services, native, modern packaging, monorepos) — and DockerAgent fails on them even by EBSR-equivalent (0.42 vs RAT 0.84). The metric change doesn't rescue it.
3. **The underlying mechanism is the synthesizer + collect-only certificate.** Faithful on a single `pip install`, lossy on uv-bootstrap / ARG-FROM / native builds / live services. The harder the repo, the more the re-synthesis step drops — and ESSR (unlike EBSR) makes every drop visible as a 0.

**Implication:** the Repo2Run-bench 87.6% was never measuring what RATBench measures. Moving to RATBench didn't make DockerAgent worse — it made an *already-present* weakness (hollow environments from a lossy synthesizer) finally *visible*, on repos chosen to require real test execution. The fix is the same env-construction redesign (host-certified EnvState; Dockerfile from certified facts; real-run verification, not collect-only).

## Artifacts
- `benchmark_reversal_findings.json` — full grounded findings (5 gather agents + adversarial verify) with verbatim evidence.
- Sibling docs: `DOCKERAGENT_FAILURE_WALKTHROUGH.md` (the log-level failure modes), `CREDIT_FIX_COMBINED_RESULT.md` (the corrected 0.20).
- Sources: `docs/REPO2RUN_BENCHMARK.md`, `outputs/repo2run_benchmark/{results,agent_success_eval_failed.jsonl}`, `datasets/{repo2run_table15.json,rat_python_hard_subset.json}`.
